import CryptoKit
import Foundation

/// Read-only discovery of existing local tmux panes. A snapshot is observation,
/// never adoption, enrollment, dispatch permission, or a reason to resume/kill.
/// Blocks on bounded subprocess reads; callers must run off the main actor.
public enum TmuxFleet {
    public struct Snapshot: Codable, Sendable, Equatable {
        public let schemaVersion: Int
        public let snapshotId: String
        public let capturedAt: Double
        public var servers: [Server]
        public var panes: [Pane]
        public var warnings: [String]
    }

    public struct Server: Codable, Sendable, Equatable {
        public let socketPath: String
        /// ok (including an empty server), unavailable, error, or skipped.
        public var status: String
        /// A bounded diagnostic code, never subprocess output or arguments.
        public var error: String?
    }

    public struct Pane: Codable, Sendable, Equatable {
        /// SHA256 of canonical socket path, a NUL separator, and pane ID.
        /// Stable within a server lifetime; not proof against server restart.
        public let id: String
        public let socketPath: String
        public let sessionName: String
        public let windowId: String
        public let windowName: String
        public let paneId: String
        /// Pane leader, which may be a shell rather than the coding agent.
        public let pid: Int
        public let tty: String
        public let cwd: String
        /// tmux pane_current_command, never argv or terminal content.
        public let command: String
        public let dead: Bool
        /// Clients attached to this pane's session, not proof it is visible.
        public let attachedClientCount: Int
        public var agents: [Agent]
        public var candidateHarnesses: [String]
        /// verified session-to-process association, unresolved, or none
        /// (ordinary shell with no candidate). Never dispatch readiness.
        public var identityStatus: String
    }

    public struct Agent: Codable, Sendable, Equatable {
        public let sessionId: String
        public let harness: String
        public let pid: Int
        public let name: String?
        public let status: String?
        public let identityEvidence: [String]
    }

    struct ProcessRow: Equatable {
        let pid: Int
        let parent: Int
        let tty: String?
        let command: String
    }

    struct CodexFiles {
        /// UUID writer-lock files open in this exact process. macOS lsof
        /// commonly leaves the lock field blank; this is file-descriptor
        /// association evidence, not proof of an OS lock being held.
        var locks: Set<String> = []
        var rollouts: [String: String] = [:]
    }

    static let maximumSockets = 64
    static let maximumCandidates = 128
    static let maximumPaneRows = 4096
    static let paneFormat = ["session_name", "window_id", "window_name", "pane_id", "pane_pid",
                             "pane_tty", "pane_current_path", "pane_current_command", "pane_dead",
                             "session_attached"].map { "#{\($0)}" }.joined(separator: "\t")

    public static func scan(extraSockets: [String] = []) -> Snapshot {
        let started = ProcessInfo.processInfo.systemUptime
        let paths = discoveredSockets(extraSockets: extraSockets)
        var snapshot = Snapshot(schemaVersion: 1, snapshotId: UUID().uuidString,
                                capturedAt: Date().timeIntervalSince1970,
                                servers: [], panes: [], warnings: [])
        if extraSockets.contains(where: { canonicalSocket($0) == nil }) {
            snapshot.warnings.append("relative_or_invalid_socket_ignored")
        }
        guard let binary = Tmux.resolveBinary() else {
            snapshot.servers = paths.map { Server(socketPath: $0, status: "unavailable", error: "tmux_unavailable") }
            snapshot.warnings.append("tmux_unavailable")
            return snapshot
        }
        var environment = ProcessInfo.processInfo.environment
        environment.removeValue(forKey: "TMUX")
        environment.removeValue(forKey: "TMUX_TMPDIR")
        environment["LC_ALL"] = "en_US.UTF-8"
        for (index, path) in paths.enumerated() {
            guard index < maximumSockets, ProcessInfo.processInfo.systemUptime - started < 10 else {
                snapshot.servers.append(Server(socketPath: path, status: "skipped", error: "scan_budget"))
                continue
            }
            // Exact sockets only. list-panes cannot create a server or session;
            // no Tmux.run helper that rewrites socket provenance is involved.
            let result = Subprocess.run(binary, ["-S", path, "list-panes", "-a", "-F", paneFormat],
                                        environment: environment, timeout: 1.5)
            let inventory = serverInventory(socketPath: path, result: result)
            snapshot.servers.append(inventory.0)
            snapshot.panes.append(contentsOf: inventory.1)
        }
        guard !snapshot.panes.isEmpty else { return snapshot }
        // One process table, executable names only. No argv, environment,
        // scrollback, or other user payload is retained in the snapshot.
        let processResult = Subprocess.run("/bin/ps", ["-axo", "pid=,ppid=,tty=,stat=,comm="], timeout: 3)
        let processes: [Int: ProcessRow]
        switch processResult {
        case .success(let output):
            guard let parsed = parseProcesses(output) else {
                snapshot.warnings.append("malformed_process_inventory")
                identify(&snapshot.panes, processes: [:], registry: [], ownership: [], codex: [:], metadata: [:])
                return snapshot
            }
            processes = parsed
        case .failure(let error):
            snapshot.warnings.append(error.timedOut ? "process_query_timeout" : "process_inventory_unavailable")
            identify(&snapshot.panes, processes: [:], registry: [], ownership: [], codex: [:], metadata: [:])
            return snapshot
        }
        let registry = SessionRegistry.all()
        let ownership = FileSessionOwnershipStore.shared.all() // plain read, no reconciliation writes
        let candidates = codexCandidates(panes: snapshot.panes, processes: processes, ownership: ownership)
        var codex: [Int: CodexFiles] = [:]
        var metadata: [String: CodexRollout.SessionMeta] = [:]
        if candidates.count > maximumCandidates { snapshot.warnings.append("identity_candidate_budget") }
        let selected = Array(candidates.sorted().prefix(maximumCandidates))
        if !selected.isEmpty {
            // lsof's mandatory p fields preserve PID provenance across this
            // batch. Only exact lock/rollout paths are parsed; all other open
            // filenames are discarded without being logged or serialized.
            let result = Subprocess.run("/usr/sbin/lsof",
                ["-n", "-P", "-a", "-p", selected.map(String.init).joined(separator: ","), "-Fpnfl"], timeout: 3)
            if case .success(let files) = result {
                codex = parseCodexFiles(files, allowedPids: Set(selected),
                                        locks: CodexRollout.threadWriterLocksDirectory.path,
                                        sessions: CodexRollout.sessionsDirectory.path)
                metadata = readMetadata(codex, warnings: &snapshot.warnings)
            } else {
                snapshot.warnings.append("conversation_identity_unavailable")
            }
        }
        identify(&snapshot.panes, processes: processes, registry: registry,
                 ownership: ownership, codex: codex, metadata: metadata)
        return snapshot
    }

    // MARK: Socket provenance and parsers (pure seams used by fixtures)

    static func canonicalSocket(_ path: String) -> String? {
        guard path.hasPrefix("/"), !path.contains("\0"), !path.contains("\n"), path.utf8.count <= 4096 else { return nil }
        return URL(fileURLWithPath: path).standardizedFileURL.resolvingSymlinksInPath().path
    }

    static func socketFromEnvironment(_ value: String?) -> String? {
        guard let value, let last = value.lastIndex(of: ","),
              let previous = value[..<last].lastIndex(of: ","),
              Int(value[value.index(after: previous)..<last]) != nil,
              Int(value[value.index(after: last)...]) != nil else { return nil }
        return canonicalSocket(String(value[..<previous]))
    }

    static func socketCandidates(uid: UInt32, support: String, tmux: String?,
                                 extra: [String], listed: (String) -> [String]) -> [String] {
        let roots = ["/tmp/tmux-\(uid)", support + "/tmux-\(uid)"]
        var candidates = roots.flatMap(listed)
        // Include standard endpoints even when absent so an unreachable server
        // is distinguishable from a successfully queried empty server.
        candidates += [roots[0] + "/default", roots[1] + "/tb"]
        if let current = socketFromEnvironment(tmux) { candidates.append(current) }
        candidates += extra
        return Array(Set(candidates.compactMap(canonicalSocket))).sorted()
    }

    static func discoveredSockets(extraSockets: [String]) -> [String] {
        let fm = FileManager.default
        return socketCandidates(uid: getuid(), support: Tmux.socketDirectory.path,
                                tmux: ProcessInfo.processInfo.environment["TMUX"], extra: extraSockets) { directory in
            guard let entries = try? fm.contentsOfDirectory(atPath: directory) else { return [] }
            return entries.sorted().compactMap { name in
                let path = (directory as NSString).appendingPathComponent(name)
                guard let attributes = try? fm.attributesOfItem(atPath: path),
                      attributes[.type] as? FileAttributeType == .typeSocket,
                      (attributes[.ownerAccountID] as? NSNumber)?.uint32Value == getuid() else { return nil }
                return path
            }
        }
    }

    static func paneIdentity(socketPath: String, paneId: String) -> String {
        SHA256.hash(data: Data((socketPath + "\0" + paneId).utf8))
            .map { String(format: "%02x", $0) }.joined()
    }

    static func serverInventory(socketPath: String, result: Result<String, ScriptError>) -> (Server, [Pane]) {
        switch result {
        case .success(let output):
            guard let panes = parsePanes(output, socketPath: socketPath) else {
                return (Server(socketPath: socketPath, status: "error", error: "malformed_pane_inventory"), [])
            }
            return (Server(socketPath: socketPath, status: "ok", error: nil), panes)
        case .failure(let error):
            if TmuxOwnership.serverHoldsNoPanes(error.message) {
                return (Server(socketPath: socketPath, status: "ok", error: nil), [])
            }
            let absent = TmuxOwnership.serverIsAbsent(error.message)
            return (Server(socketPath: socketPath, status: absent ? "unavailable" : "error",
                           error: error.timedOut ? "pane_query_timeout" : absent ? "socket_unavailable" : "pane_query_failed"), [])
        }
    }

    static func parsePanes(_ output: String, socketPath: String) -> [Pane]? {
        if output.isEmpty { return [] }
        let lines = output.split(separator: "\n", omittingEmptySubsequences: true)
        guard lines.count <= maximumPaneRows, output.utf8.count <= 4 * 1024 * 1024 else { return nil }
        var result: [String: Pane] = [:]
        // Linked windows repeat a physical pane under several sessions. Pick
        // a deterministic representative; attachment count refers to that
        // session only. Socket + pane remains the physical identity.
        for line in lines.sorted() {
            let fields = line.split(separator: "\t", omittingEmptySubsequences: false).map(String.init)
            guard fields.count == 10, !fields[0].isEmpty, fields[1].hasPrefix("@"),
                  fields[3].hasPrefix("%"), Int(fields[3].dropFirst()) != nil,
                  let pid = Int(fields[4]), pid > 0 || (pid == 0 && fields[8] == "1"),
                  fields[5].hasPrefix("/dev/") || (fields[5].isEmpty && fields[8] == "1"),
                  ["0", "1"].contains(fields[8]),
                  let clients = Int(fields[9]), clients >= 0 else { return nil }
            if let prior = result[fields[3]] {
                guard prior.pid == pid, prior.tty == fields[5], prior.windowId == fields[1] else { return nil }
                continue
            }
            result[fields[3]] = Pane(id: paneIdentity(socketPath: socketPath, paneId: fields[3]),
                               socketPath: socketPath, sessionName: fields[0], windowId: fields[1],
                               windowName: fields[2], paneId: fields[3], pid: pid, tty: fields[5],
                               cwd: fields[6], command: fields[7], dead: fields[8] == "1",
                               attachedClientCount: clients, agents: [], candidateHarnesses: [],
                               identityStatus: "unresolved")
        }
        return result.values.sorted { $0.paneId < $1.paneId }
    }

    static func parseProcesses(_ output: String) -> [Int: ProcessRow]? {
        // A successful system-wide ps includes at least ps itself. An empty
        // capture is missing evidence, not proof that every process vanished.
        guard !output.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        var result: [Int: ProcessRow] = [:]
        for line in output.split(separator: "\n") {
            let fields = line.split(whereSeparator: { $0 == " " || $0 == "\t" })
            guard fields.count >= 5, let pid = Int(fields[0]), let parent = Int(fields[1]) else { return nil }
            if fields[3].contains("Z") { continue }
            let tty = String(fields[2])
            result[pid] = ProcessRow(pid: pid, parent: parent,
                tty: tty == "??" || tty == "?" ? nil : tty.hasPrefix("/dev/") ? tty : "/dev/" + tty,
                command: (fields.dropFirst(4).joined(separator: " ") as NSString).lastPathComponent)
        }
        return result
    }

    static func process(_ pid: Int, belongsTo pane: Pane, processes: [Int: ProcessRow]) -> Bool {
        guard !pane.dead, let candidate = processes[pid], candidate.tty == pane.tty else { return false }
        var here = pid
        var visited = Set<Int>()
        for _ in 0..<64 {
            if here == pane.pid { return true }
            guard visited.insert(here).inserted, let next = processes[here], next.parent > 0 else { return false }
            here = next.parent
        }
        return false
    }

    static func candidateHarness(_ command: String) -> String? {
        switch (command as NSString).lastPathComponent {
        case "claude", "claude-code", "claude.exe": return "claude-code"
        case "codex": return "codex"
        default: return nil
        }
    }

    static func matchingRecord(_ record: SessionOwnershipRecord, pane: Pane,
                               processes: [Int: ProcessRow]) -> Bool {
        record.paneId == pane.paneId && record.sessionName == pane.sessionName
            && record.paneTty == pane.tty && process(record.pid, belongsTo: pane, processes: processes)
    }

    static func codexCandidates(panes: [Pane], processes: [Int: ProcessRow],
                                ownership: [SessionOwnershipRecord]) -> Set<Int> {
        var result = Set<Int>()
        for pane in panes where !pane.dead {
            for row in processes.values where candidateHarness(row.command) == "codex"
                && process(row.pid, belongsTo: pane, processes: processes) { result.insert(row.pid) }
            for record in ownership where record.harness == "codex"
                && matchingRecord(record, pane: pane, processes: processes) { result.insert(record.pid) }
        }
        return result
    }

    static func parseCodexFiles(_ output: String, allowedPids: Set<Int>, locks: String,
                                sessions: String) -> [Int: CodexFiles] {
        var current: Int?
        var currentName: String?
        var result: [Int: CodexFiles] = [:]
        let lockPrefix = URL(fileURLWithPath: locks).standardizedFileURL.path + "/"
        let sessionPrefix = URL(fileURLWithPath: sessions).standardizedFileURL.path + "/"
        func finishFile() {
            guard let pid = current, let rawPath = currentName else { return }
            let path = URL(fileURLWithPath: rawPath).standardizedFileURL.path
            if path.hasPrefix(lockPrefix), path.hasSuffix(".lock") {
                let id = String(path.dropFirst(lockPrefix.count).dropLast(5))
                if UUID(uuidString: id) != nil { result[pid, default: CodexFiles()].locks.insert(id.lowercased()) }
            } else if path.hasPrefix(sessionPrefix), path.hasSuffix(".jsonl") {
                let name = URL(fileURLWithPath: path).deletingPathExtension().lastPathComponent
                let id = name.split(separator: "-").suffix(5).joined(separator: "-")
                if UUID(uuidString: id) != nil { result[pid, default: CodexFiles()].rollouts[id.lowercased()] = path }
            }
        }
        for line in output.split(separator: "\n") {
            if line.first == "p" {
                finishFile()
                current = Int(line.dropFirst()).flatMap { allowedPids.contains($0) ? $0 : nil }
                currentName = nil
            } else if line.first == "f" {
                finishFile()
                currentName = nil
            } else if line.first == "n" {
                currentName = String(line.dropFirst())
            }
        }
        finishFile()
        return result
    }

    static func readMetadata(_ files: [Int: CodexFiles], warnings: inout [String]) -> [String: CodexRollout.SessionMeta] {
        let wanted = Set(files.values.flatMap(\.locks))
        var paths: [String: String] = [:]
        for file in files.values { paths.merge(file.rollouts) { first, _ in first } }
        let missing = wanted.subtracting(paths.keys)
        if !missing.isEmpty, let walker = FileManager.default.enumerator(at: CodexRollout.sessionsDirectory,
            includingPropertiesForKeys: nil, options: [.skipsHiddenFiles]) {
            let start = ProcessInfo.processInfo.systemUptime
            var visited = 0
            for case let url as URL in walker {
                visited += 1
                if visited > 20_000 || ProcessInfo.processInfo.systemUptime - start > 2 {
                    warnings.append("conversation_metadata_budget")
                    break
                }
                guard url.pathExtension == "jsonl" else { continue }
                let id = url.deletingPathExtension().lastPathComponent.split(separator: "-").suffix(5).joined(separator: "-").lowercased()
                if missing.contains(id) { paths[id] = url.path }
                if wanted.isSubset(of: Set(paths.keys)) { break }
            }
        }
        var result: [String: CodexRollout.SessionMeta] = [:]
        for id in wanted.sorted().prefix(maximumCandidates * 4) {
            guard let path = paths[id], let meta = CodexRollout.meta(rollout: URL(fileURLWithPath: path)),
                  meta.sessionId.lowercased() == id else { continue }
            result[id] = meta
        }
        return result
    }

    static func identify(_ panes: inout [Pane], processes: [Int: ProcessRow], registry: [SessionRegistry.Entry],
                         ownership: [SessionOwnershipRecord], codex: [Int: CodexFiles],
                         metadata: [String: CodexRollout.SessionMeta]) {
        for index in panes.indices {
            let pane = panes[index]
            guard !pane.dead else { panes[index].identityStatus = "none"; continue }
            var candidates = Set<String>()
            var agents: [Agent] = []
            if let harness = candidateHarness(pane.command) { candidates.insert(harness) }
            let occupants = processes.values.filter { process($0.pid, belongsTo: pane, processes: processes) }
            for row in occupants {
                if let harness = candidateHarness(row.command) { candidates.insert(harness) }
                let claims = registry.filter { $0.pid == row.pid && $0.kind != "bg" && $0.kind != "background" }
                if !claims.isEmpty { candidates.insert("claude-code") }
                if Set(claims.map(\.sessionId)).count == 1, let entry = claims.max(by: { ($0.updatedAt ?? 0) < ($1.updatedAt ?? 0) }),
                   entry.tmux == nil || (entry.paneId == pane.paneId && entry.tmuxSessionName == pane.sessionName),
                   candidateHarness(row.command) == "claude-code" || row.command == "node" {
                    agents.append(Agent(sessionId: entry.sessionId, harness: "claude-code", pid: row.pid,
                                        name: entry.name, status: entry.status,
                                        identityEvidence: ["harness_registry", "live_pid_tty", "pane_process_ancestry"]))
                }
                let records = ownership.filter { $0.harness == "codex" && $0.pid == row.pid
                    && matchingRecord($0, pane: pane, processes: processes) }
                if !records.isEmpty { candidates.insert("codex") }
                guard candidateHarness(row.command) == "codex" || !records.isEmpty,
                      let files = codex[row.pid], !files.locks.isEmpty,
                      files.locks.allSatisfy({ metadata[$0] != nil }) else { continue }
                let roots = files.locks.filter { metadata[$0]?.isSubagent == false }
                // Several open roots, missing metadata, or a subagent-only
                // process is ambiguous; neither newest nor remembered wins.
                guard roots.count == 1, let id = roots.first else { continue }
                agents.append(Agent(sessionId: id, harness: "codex", pid: row.pid, name: nil, status: nil,
                                    identityEvidence: ["open_writer_lock", "unique_non_subagent_metadata",
                                                       "live_pid_tty", "pane_process_ancestry"]))
            }
            panes[index].agents = agents.sorted { ($0.sessionId, $0.pid) < ($1.sessionId, $1.pid) }
            panes[index].candidateHarnesses = candidates.sorted()
            let shell = ["sh", "zsh", "bash", "fish", "dash", "ksh", "nu"].contains((pane.command as NSString).lastPathComponent)
            panes[index].identityStatus = !agents.isEmpty ? "verified" : candidates.isEmpty && shell ? "none" : "unresolved"
        }
    }
}
