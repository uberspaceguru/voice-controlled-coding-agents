import XCTest
@testable import TranquilityCore

final class FleetLiveTests: XCTestCase {
    private struct EmptyOwnership: SessionOwnershipStore {
        func record(_ record: SessionOwnershipRecord) { XCTFail("display must not import ownership") }
        func current(sessionId: String) -> SessionOwnershipRecord? { nil }
        func remove(sessionId: String) { XCTFail("display must not remove ownership") }
        func all() -> [SessionOwnershipRecord] { [] }
    }

    func testVerifiedClaudeMissingFromRegistryRemainsVisible() {
        let external = SessionOwnershipRecord(sessionId: "claude-a", harness: "claude-code", pid: 20,
            paneId: "%1", sessionName: "existing", origin: .external)
        let result = FleetLive.sessions(registry: [], ownership: EmptyOwnership(), records: [external])
        XCTAssertEqual(result.map(\.sessionId), ["claude-a"])
        XCTAssertEqual(result.first?.harness, "claude-code")
        XCTAssertNil(result.first?.status, "live process evidence must not invent idle readiness")
    }

    func testMatchingClaudeRegistryAndFleetAppearOnceWithRegistryStatus() {
        let registry = LiveSession(harness: "claude-code", pid: 20, sessionId: "claude-a", cwd: "/work",
            status: "busy", name: "Worker", waitingFor: nil)
        let external = SessionOwnershipRecord(sessionId: "claude-a", harness: "claude-code", pid: 20,
            sessionName: "existing", origin: .external)
        let result = FleetLive.sessions(registry: [registry], ownership: EmptyOwnership(), records: [external])
        XCTAssertEqual(result.count, 1)
        XCTAssertEqual(result.first?.status, "busy")
        XCTAssertEqual(result.first?.name, "Worker")
    }

    func testConflictingClaudeRegistryAndFleetAreNotDisplayedAsOneVerifiedWorker() {
        let registry = LiveSession(harness: "claude-code", pid: 20, sessionId: "claude-a", cwd: nil,
            status: "idle", name: nil, waitingFor: nil)
        let external = SessionOwnershipRecord(sessionId: "claude-a", harness: "claude-code", pid: 21,
            origin: .external)
        XCTAssertTrue(FleetLive.sessions(registry: [registry], ownership: EmptyOwnership(), records: [external]).isEmpty)
    }

    func testCombinesExistingWorkersWithoutImportingOwnership() {
        let live = LiveSession(harness: "codex", pid: 10, sessionId: "a", cwd: "/a", status: nil, name: "Named", waitingFor: nil)
        let a = SessionOwnershipRecord(sessionId: "a", harness: "codex", pid: 10, sessionName: "one", origin: .external)
        let b = SessionOwnershipRecord(sessionId: "b", harness: "codex", pid: 11, paneId: "%1", sessionName: "two", origin: .external)
        let result = FleetLive.merging([live], records: [a, b])
        XCTAssertEqual(result.map(\.sessionId), ["a", "b"])
        XCTAssertEqual(result[0].name, "Named")
        XCTAssertEqual(result[1].name, "two / %1")
        XCTAssertNil(result[1].status)
    }

    func testConflictingProcessesDoNotBecomeAnArbitraryTarget() {
        let live = LiveSession(harness: "codex", pid: 10, sessionId: "a", cwd: nil, status: nil, name: nil, waitingFor: nil)
        let external = SessionOwnershipRecord(sessionId: "a", harness: "codex", pid: 11, origin: .external)
        XCTAssertTrue(FleetLive.merging([live], records: [external]).isEmpty)
    }
}
