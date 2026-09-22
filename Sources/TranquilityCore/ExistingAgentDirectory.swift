import Foundation

/// Observes existing workers without importing ownership, enrolling, resuming,
/// renaming, or attaching to them. Display reads are memory-only; actions scan
/// again and require a single verified session at the expected exact endpoint.
public final class ExistingAgentDirectory: @unchecked Sendable {
    public static let shared = ExistingAgentDirectory()
    private let lock = NSLock()
    private var observed: [SessionOwnershipRecord] = []
    private var knownSockets: [String] = []
    private var refreshedAt: TimeInterval = 0
    private let scan: @Sendable ([String]) -> TmuxFleet.Snapshot

    public init(scan: @escaping @Sendable ([String]) -> TmuxFleet.Snapshot = {
        TmuxFleet.scan(extraSockets: $0)
    }) { self.scan = scan }

    public var cachedRecords: [SessionOwnershipRecord] { lock.withLock { observed } }

    /// Call off the main actor. force=false is for the periodic display refresh;
    /// action callers use the default fresh scan, never cached identity evidence.
    @discardableResult
    public func refresh(extraSockets: [String] = [], force: Bool = true) -> [SessionOwnershipRecord] {
        let prior = lock.withLock { (observed, knownSockets, refreshedAt) }
        if !force, extraSockets.isEmpty, ProcessInfo.processInfo.systemUptime - prior.2 < 5 {
            return prior.0
        }
        let snapshot = scan(Array(Set(prior.1 + extraSockets)).sorted())
        let records = Self.records(in: snapshot)
        lock.withLock {
            observed = records
            knownSockets = Array(Set(prior.1 + snapshot.servers.map(\.socketPath) + extraSockets)).sorted()
            refreshedAt = ProcessInfo.processInfo.systemUptime
        }
        return records
    }

    /// fresh=false is strictly memory-only, including the first call.
    public func records(fresh: Bool = false) -> [SessionOwnershipRecord] {
        fresh ? refresh() : cachedRecords
    }

    public func record(sessionId: String, fresh: Bool = true) -> SessionOwnershipRecord? {
        records(fresh: fresh).first { $0.sessionId.caseInsensitiveCompare(sessionId) == .orderedSame }
    }

    public func verifies(sessionId: String, pid: Int, pane: TmuxPaneAddress) -> Bool {
        guard pane.isExternal, let exact = pane.socketPath,
              let record = record(sessionId: sessionId), record.pid == pid,
              record.socketPath == exact, record.paneId == pane.paneId,
              record.paneTty == pane.paneTty, record.sessionName == pane.sessionName else { return false }
        return true
    }

    /// A physical pane containing multiple verified workers is ambiguous as an
    /// input target. A conversation duplicated across panes is also excluded;
    /// the visible fleet inventory still shows all evidence for human review.
    public static func records(in snapshot: TmuxFleet.Snapshot) -> [SessionOwnershipRecord] {
        let available = Set(snapshot.servers.filter { $0.status == "ok" }.map(\.socketPath))
        var candidates: [SessionOwnershipRecord] = []
        let physicalCounts = Dictionary(grouping: snapshot.panes, by: { $0.socketPath + "\0" + $0.paneId })
        let sessionCounts = Dictionary(grouping: snapshot.panes.filter { !$0.dead }
            .flatMap(\.agents), by: { $0.sessionId.lowercased() })
        for pane in snapshot.panes {
            guard available.contains(pane.socketPath), !pane.dead,
                  pane.identityStatus == "verified", pane.agents.count == 1,
                  let exact = TmuxFleet.canonicalSocket(pane.socketPath),
                  exact == pane.socketPath, pane.paneId.hasPrefix("%"),
                  Int(pane.paneId.dropFirst()) != nil, !pane.tty.isEmpty,
                  physicalCounts[exact + "\0" + pane.paneId]?.count == 1 else { continue }
            let agent = pane.agents[0]
            guard agent.pid > 0, !agent.sessionId.isEmpty, !agent.identityEvidence.isEmpty,
                  sessionCounts[agent.sessionId.lowercased()]?.count == 1,
                  ["codex", "claude-code"].contains(agent.harness) else { continue }
            candidates.append(SessionOwnershipRecord(
                sessionId: agent.sessionId, harness: agent.harness, pid: agent.pid,
                paneId: pane.paneId, sessionName: pane.sessionName, paneTty: pane.tty,
                cwd: pane.cwd, attachedAt: Date(timeIntervalSince1970: snapshot.capturedAt),
                socketPath: exact, origin: .external))
        }
        let counts = Dictionary(grouping: candidates, by: { $0.sessionId.lowercased() })
        return candidates.filter { counts[$0.sessionId.lowercased()]?.count == 1 }
            .sorted { $0.sessionId < $1.sessionId }
    }
}
