import Foundation
import XCTest
@testable import TranquilityCore

/// No tmux/process reads or shared registry writes: all evidence is synthetic.
final class ExistingAgentDirectoryTests: XCTestCase {
    private let id = "11111111-1111-4111-8111-111111111111"
    private let socket = "/fixture/foreign server/socket"

    private func snapshot(id: String? = nil, socket: String? = nil, pid: Int = 101,
                          dead: Bool = false) -> TmuxFleet.Snapshot {
        let path = socket ?? self.socket
        let agent = TmuxFleet.Agent(sessionId: id ?? self.id, harness: "codex", pid: pid,
            name: nil, status: nil, identityEvidence: ["open_writer_lock", "live_pid_tty", "pane_process_ancestry"])
        let pane = TmuxFleet.Pane(id: "physical-pane", socketPath: path, sessionName: "existing",
            windowId: "@1", windowName: "work", paneId: "%1", pid: 100, tty: "/dev/ttys001",
            cwd: "/fixture/work", command: "zsh", dead: dead, attachedClientCount: 2,
            agents: [agent], candidateHarnesses: ["codex"], identityStatus: "verified")
        return .init(schemaVersion: 1, snapshotId: "fixture", capturedAt: Date().timeIntervalSince1970,
            servers: [.init(socketPath: path, status: "ok")], panes: [pane], warnings: [])
    }

    private final class Scan: @unchecked Sendable {
        let lock = NSLock()
        var value: TmuxFleet.Snapshot
        var count = 0
        init(_ value: TmuxFleet.Snapshot) { self.value = value }
        func read(_ sockets: [String]) -> TmuxFleet.Snapshot {
            lock.withLock { count += 1; return value }
        }
        func replace(_ snapshot: TmuxFleet.Snapshot) { lock.withLock { value = snapshot } }
    }

    func testVerifiedWorkerBecomesExternalExactAddressWithoutLifecycleOwnership() throws {
        let records = ExistingAgentDirectory.records(in: snapshot())
        let record = try XCTUnwrap(records.first)
        XCTAssertEqual(records.count, 1)
        XCTAssertTrue(record.isExternal)
        XCTAssertEqual(record.socketPath, socket)
        XCTAssertEqual(record.pane?.stableTarget, "%1", "existing multipane sessions must target the exact pane")
        XCTAssertTrue(record.pane?.isExternal == true)
        XCTAssertEqual(record.pid, 101, "agent PID must not become pane leader PID")
    }

    func testDisplayReadsNeverScanAndActionsNeverReuseCachedIdentity() throws {
        let scan = Scan(snapshot())
        let directory = ExistingAgentDirectory(scan: { scan.read($0) })
        XCTAssertEqual(directory.records(), [])
        XCTAssertEqual(directory.cachedRecords, [])
        XCTAssertEqual(scan.count, 0)
        let initial = try XCTUnwrap(directory.record(sessionId: id))
        XCTAssertEqual(scan.count, 1)
        _ = directory.records(fresh: false)
        _ = directory.refresh(force: false)
        XCTAssertEqual(scan.count, 1)
        scan.replace(snapshot(socket: "/fixture/another/socket", pid: 202))
        XCTAssertFalse(directory.verifies(sessionId: id, pid: 101, pane: try XCTUnwrap(initial.pane)))
        XCTAssertEqual(scan.count, 2)
        XCTAssertEqual(directory.cachedRecords.first?.pid, 202)
    }

    func testDuplicateConversationAcrossServersIsAmbiguous() {
        var combined = snapshot()
        let other = snapshot(socket: "/fixture/server-b/socket", pid: 202)
        combined.servers += other.servers
        combined.panes += other.panes
        XCTAssertEqual(ExistingAgentDirectory.records(in: combined), [])
    }

    func testMultipleAgentsInOnePaneAreNotDispatchTargets() {
        var observed = snapshot()
        observed.panes[0].agents.append(.init(sessionId: "other", harness: "claude-code", pid: 102,
            name: nil, status: nil, identityEvidence: ["registry", "live_pid_tty"]))
        XCTAssertEqual(ExistingAgentDirectory.records(in: observed), [])
    }

    func testDeadUnknownUnverifiedAndPartialServerEvidenceAreExcluded() {
        XCTAssertEqual(ExistingAgentDirectory.records(in: snapshot(dead: true)), [])
        var observed = snapshot()
        observed.panes[0].identityStatus = "unresolved"
        XCTAssertEqual(ExistingAgentDirectory.records(in: observed), [])
        observed = snapshot()
        observed.servers[0].status = "error"
        XCTAssertEqual(ExistingAgentDirectory.records(in: observed), [])
        observed = snapshot()
        observed.panes.append(observed.panes[0])
        XCTAssertEqual(ExistingAgentDirectory.records(in: observed), [])
    }

    func testLocatingExternalDoesNotPersistOwnership() throws {
        let fixture = snapshot()
        let directory = ExistingAgentDirectory(scan: { _ in fixture })
        let path = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let store = FileSessionOwnershipStore(fileURL: path)
        defer { try? FileManager.default.removeItem(at: path) }
        let location = AgentLedger.locate(sessionId: id, store: store, existing: directory)
        XCTAssertEqual(location.pane?.socketPath, socket)
        XCTAssertTrue(location.pane?.isExternal == true)
        XCTAssertEqual(store.all(), [])
        XCTAssertFalse(FileManager.default.fileExists(atPath: path.path))
    }

    func testAmbiguousExternalCannotFallBackToLegacyAdoptionOrRevival() {
        var fixture = snapshot()
        let duplicate = snapshot(socket: "/fixture/second/socket", pid: 202)
        fixture.servers += duplicate.servers
        fixture.panes += duplicate.panes
        let frozen = fixture
        let directory = ExistingAgentDirectory(scan: { _ in frozen })
        let path = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let store = FileSessionOwnershipStore(fileURL: path)
        defer { try? FileManager.default.removeItem(at: path) }
        let location = AgentLedger.locate(sessionId: id, pid: 101, store: store, existing: directory)
        guard case .unknown = location else { return XCTFail(location.summary) }
        XCTAssertTrue(store.all().isEmpty)
        XCTAssertFalse(FileManager.default.fileExists(atPath: path.path))
    }

    func testOwnershipRoundTripKeepsExternalProvenanceAndOldRecordsDecode() throws {
        let external = try XCTUnwrap(ExistingAgentDirectory.records(in: snapshot()).first)
        let roundTrip = try JSONDecoder().decode(SessionOwnershipRecord.self, from: JSONEncoder().encode(external))
        XCTAssertEqual(roundTrip, external)
        let legacy = SessionOwnershipRecord(sessionId: "legacy", harness: "codex", pid: 11)
        XCTAssertFalse(try JSONDecoder().decode(SessionOwnershipRecord.self,
            from: JSONEncoder().encode(legacy)).isExternal)
    }
}
