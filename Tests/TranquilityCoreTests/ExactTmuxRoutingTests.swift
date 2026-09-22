import Foundation
import XCTest
@testable import TranquilityCore

final class ExactTmuxRoutingTests: XCTestCase {
    private func pane(_ socket: String, id: String = "%1") -> TmuxPaneAddress {
        .init(socketName: "tb", paneId: id, sessionName: "same-name", paneTty: "/dev/ttys001",
              socketPath: socket, isExternal: true)
    }

    func testExactSocketWinsOverCollidingLegacyNameWithoutShellExpansion() {
        XCTAssertEqual(Tmux.socketArguments(socket: "tb", socketPath: "/fixture/a b/socket"),
                       ["-S", "/fixture/a b/socket"])
        XCTAssertNil(Tmux.socketArguments(socket: "tb", socketPath: "relative"))
        XCTAssertNil(Tmux.socketArguments(socket: "tb", socketPath: "/fixture/bad\npath"))
        XCTAssertEqual(Tmux.socketArguments(socket: "tb", socketPath: nil), ["-L", "tb"])
        XCTAssertEqual(Tmux.socketArguments(socket: nil, socketPath: nil), [])
    }

    func testExactAddressSurvivesOwnershipAndMultipaneTargets() {
        let a = pane("/fixture/socket-a")
        let b = pane("/fixture/socket-b")
        XCTAssertNotEqual(a.routingKey, b.routingKey)
        XCTAssertNotEqual(a.routingKey, pane("/fixture/socket-a", id: "%2").routingKey)
        XCTAssertEqual(a.stableTarget, "%1")
        XCTAssertEqual(TmuxPaneAddress(socketName: "tb", paneId: "%1", sessionName: "owned",
            paneTty: "/dev/ttys1").stableTarget, "owned")
    }

    func testPaneLockIsExclusiveAndDifferentServersDoNotCollide() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("pane-lock-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: dir) }
        let a = pane("/fixture/socket-a"), b = pane("/fixture/socket-b")
        let held = try XCTUnwrap(PaneDispatchLock.acquire(pane: a, timeout: 0, directory: dir))
        defer { PaneDispatchLock.release(held) }
        XCTAssertNil(PaneDispatchLock.acquire(pane: a, timeout: 0, directory: dir))
        let other = try XCTUnwrap(PaneDispatchLock.acquire(pane: b, timeout: 0, directory: dir))
        PaneDispatchLock.release(other)
    }

    func testEachSendUsesItsOwnServerBuffer() {
        let names = (0..<100).map { _ in TmuxTransport.dispatchBufferName() }
        XCTAssertEqual(Set(names).count, 100)
        XCTAssertFalse(names.contains("tb-dispatch"))
        XCTAssertTrue(names.allSatisfy { $0.hasPrefix("tb-dispatch-") })
    }

    func testExternalComposerPreservesHumanDraftMatchingTextAndUnreadableState() {
        XCTAssertTrue(TmuxTransport.mayUseComposer(line: .empty, external: true))
        let occupied: [TmuxTransport.PromptLine] = [.holds(ours: false), .holds(ours: true), .unreadable]
        for line in occupied {
            XCTAssertFalse(TmuxTransport.mayUseComposer(line: line, external: true))
            XCTAssertTrue(TmuxTransport.mayUseComposer(line: line, external: false))
        }
        // External cleanup must not read/clear a box at all.
        TmuxTransport.clearBox(pane: pane("/fixture/socket-a"), glyph: ">") {
            XCTFail("External composer cleanup should never inspect or change user text")
            return "> important draft"
        }
    }

    func testExactOwnedRecordCannotMatchAnotherSocketOrFallBackToUnhosted() {
        let record = SessionOwnershipRecord(sessionId: "worker", harness: "codex", pid: 42,
            paneId: "%1", sessionName: "same-name", paneTty: "/dev/ttys001", socketPath: "/fixture/socket-a")
        let other = AgentLedger.PaneRow(socketName: nil, sessionName: "same-name", paneId: "%1",
            paneTty: "/dev/ttys001", socketPath: "/fixture/socket-b")
        var facts = AgentLedger.Facts(record: record, registry: nil, pidHint: nil,
            inventories: [(nil, .listed([other]))], isAlive: { $0 == 42 }, ttyOf: { _ in "/dev/ttys001" },
            exactInventories: ["/fixture/socket-a": .listed([])])
        let absent = AgentLedger.decide(sessionId: "worker", harness: nil, facts: facts)
        guard case .unknown = absent.location else { return XCTFail(absent.location.summary) }
        XCTAssertNil(absent.adopt)
        let expected = AgentLedger.PaneRow(socketName: nil, sessionName: "same-name", paneId: "%1",
            paneTty: "/dev/ttys001", socketPath: "/fixture/socket-a")
        facts.exactInventories = ["/fixture/socket-a": .listed([expected])]
        let found = AgentLedger.decide(sessionId: "worker", harness: nil, facts: facts)
        XCTAssertEqual(found.location.pane?.socketPath, "/fixture/socket-a")
        XCTAssertNil(found.adopt)
    }

    func testExternalRecordNeverEntersLegacyAdoption() {
        let record = SessionOwnershipRecord(sessionId: "external", harness: "codex", pid: 42,
            paneId: "%1", sessionName: "same-name", paneTty: "/dev/ttys001",
            socketPath: "/fixture/socket-a", origin: .external)
        let facts = AgentLedger.Facts(record: record, registry: nil, pidHint: nil,
            inventories: [], isAlive: { _ in true }, ttyOf: { _ in "/dev/ttys001" })
        let decision = AgentLedger.decide(sessionId: "external", harness: nil, facts: facts)
        guard case .unknown = decision.location else { return XCTFail(decision.location.summary) }
        XCTAssertNil(decision.adopt)
    }

    func testReusedPaneIdentityOnDifferentTtyCannotReplaceRetainedAttachment() {
        let record = SessionOwnershipRecord(sessionId: "worker", harness: "codex", pid: 42,
            paneId: "%1", sessionName: "same-name", paneTty: "/dev/ttys001", socketPath: "/fixture/socket-a")
        let reused = AgentLedger.PaneRow(socketName: nil, sessionName: "same-name", paneId: "%1",
            paneTty: "/dev/ttys999", socketPath: "/fixture/socket-a")
        let facts = AgentLedger.Facts(record: record, registry: nil, pidHint: nil,
            inventories: [], isAlive: { $0 == 42 }, ttyOf: { _ in "/dev/ttys999" },
            exactInventories: ["/fixture/socket-a": .listed([reused])])
        let decision = AgentLedger.decide(sessionId: "worker", harness: nil, facts: facts)
        guard case .unknown = decision.location else { return XCTFail(decision.location.summary) }
        XCTAssertNil(decision.adopt)
    }
}
