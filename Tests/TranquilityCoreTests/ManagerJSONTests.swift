import Foundation
import XCTest
@testable import TranquilityCore

/// The manager's read contract. A change in shape here is a change the Python
/// side must see, so the shape is pinned by test rather than by reading output.
final class ManagerJSONTests: XCTestCase {
    private var tmpDir: URL!
    private var store: QueueStore!

    override func setUpWithError() throws {
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("manager-json-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
        store = try QueueStore(url: tmpDir.appendingPathComponent("queue.sqlite"))
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tmpDir)
    }

    private func seed(session: String = "sess-1", goal: String? = "ship the outreach CRM") throws -> Int64 {
        let brief = SessionBrief(
            topic: "outreach CRM", goal: goal, happened: "Tests pass on the reducer.",
            nextStep: "merge", question: "merge?", rationale: "the reducer was the bug",
            findings: "reducer dropped the last row", solution: "guard the empty case",
            recap: "Reducer fixed, tests green.", proposal: "Merge it?")
        let rowid = try XCTUnwrap(store.insert(
            event: QueuedEvent(
                createdAtMs: Int64(Date().timeIntervalSince1970 * 1000), hookEvent: .stop,
                sessionId: session, promptId: UUID().uuidString, cwd: "/tmp/kopi-outreach",
                lastAssistantMessage: "Tests pass. Merge?", tty: "ttys001"),
            brief: brief, provider: "test"))
        return rowid
    }

    func testBriefCarriesTheLadderInOrderWithEmptiesSkipped() throws {
        _ = try seed()
        let brief = try XCTUnwrap(ManagerJSON.brief(store: store, sessionId: "sess-1"))
        XCTAssertEqual(brief.project, "kopi-outreach")
        XCTAssertEqual(brief.goal, "ship the outreach CRM")
        XCTAssertEqual(brief.why, "the reducer was the bug")
        XCTAssertEqual(brief.rungs.map(\.kind), ["goal", "findings", "solution", "why", "message"])
        XCTAssertFalse(brief.rungs.contains { $0.spoken.isEmpty })
    }

    func testBriefWithoutAGoalHasNoGoalRung() throws {
        _ = try seed(session: "sess-2", goal: nil)
        let brief = try XCTUnwrap(ManagerJSON.brief(store: store, sessionId: "sess-2"))
        XCTAssertNil(brief.goal)
        XCTAssertEqual(brief.rungs.first?.kind, "findings")
    }

    func testBriefIsNilForAnUnknownSession() throws {
        XCTAssertNil(try ManagerJSON.brief(store: store, sessionId: "nope"))
    }

    func testStatusListsWaitingSessionsWithGoal() throws {
        _ = try seed()
        let status = try ManagerJSON.status(store: store)
        XCTAssertEqual(status.waiting.count, 1)
        XCTAssertEqual(status.waiting.first?.goal, "ship the outreach CRM")
        XCTAssertEqual(status.unannounced, 1)
        XCTAssertFalse(status.waiting.first?.heard ?? true)
    }

    func testTargetsJoinGoalAndWaitingOntoLiveSessions() throws {
        _ = try seed()
        let live = LiveSession(pid: 4242, sessionId: "sess-1", cwd: "/tmp/kopi-outreach",
                               status: "waiting", name: "outreach", waitingFor: nil)
        let targets = ManagerJSON.targets(store: store, live: [live], isEnrolled: { _, _ in true })
        XCTAssertEqual(targets.count, 1)
        XCTAssertEqual(targets.first?.project, "kopi-outreach")
        XCTAssertEqual(targets.first?.goal, "ship the outreach CRM")
        XCTAssertEqual(targets.first?.waiting, true)
        XCTAssertEqual(targets.first?.enrolled, true)
        XCTAssertFalse(targets.first?.name?.isEmpty ?? true)
        XCTAssertNil(targets.first?.rightHand, "no roster: the key is absent, and the manager reads everyone")
    }

    /// With a roster every row says whether it is a hand, so the manager can
    /// make the grid's cut without a second read; and the name is the pinned
    /// one, so "Director" is what the manager says and hears.
    func testTargetsAndStatusCarryTheRightHandFlagAndThePinnedName() throws {
        _ = try seed()
        _ = try seed(session: "sess-2", goal: "something else")
        let hands = RightHands.Resolved(ids: ["sess-1"], names: ["sess-1": "Director"])
        let live = [
            LiveSession(pid: 4242, sessionId: "sess-1", cwd: "/tmp/kopi-outreach", status: "idle", name: "outreach", waitingFor: nil),
            LiveSession(pid: 4243, sessionId: "sess-2", cwd: "/tmp/other", status: "idle", name: "other", waitingFor: nil),
        ]
        GridAssembler.pinnedNames = { hands.names[$0] }
        defer { GridAssembler.pinnedNames = { RightHands.pinnedName(for: $0) } }
        let targets = ManagerJSON.targets(store: store, live: live, isEnrolled: { _, _ in true }, rightHands: hands)
        XCTAssertEqual(targets.map(\.rightHand), [true, false])
        XCTAssertEqual(targets.first?.name, "Director")
        let status = try ManagerJSON.status(store: store, rightHands: hands)
        XCTAssertEqual(Set(status.waiting.map { "\($0.sessionId):\($0.rightHand == true)" }), ["sess-1:true", "sess-2:false"])
        // The encoded key is what tb-voice reads.
        XCTAssertTrue(ManagerJSON.encode(targets).contains(#""rightHand":true"#))
    }

    func testEncodingIsStableAndSorted() throws {
        let rung = ManagerJSON.Rung(kind: "goal", spoken: "ship it")
        XCTAssertEqual(ManagerJSON.encode(rung), #"{"kind":"goal","spoken":"ship it"}"#)
    }

    func testRungByKindReadsTheStoredLadder() throws {
        _ = try seed()
        let rung = try XCTUnwrap(ManagerJSON.rung(store: store, sessionId: "sess-1", kind: "solution"))
        XCTAssertEqual(rung.kind, .solution)
        XCTAssertTrue(rung.spoken.text.contains("guard the empty case"))
        XCTAssertNil(try ManagerJSON.rung(store: store, sessionId: "sess-1", kind: "nope"))
    }
}
