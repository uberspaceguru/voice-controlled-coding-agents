import XCTest
@testable import TranquilityCore

/// The grid's half of the right-hands (23 Sep): a row outside the roster is
/// filed whatever its lamp, without the switch's waiting-turn exception,
/// and files nothing for the loop to un-file.
final class RightHandsGridTests: XCTestCase {

    private let A = "8f14e45f-ceea-467a-9eef-2b9c1b2dc9f0"
    private let B = "c4ca4238-a0b9-4382-8dcc-509a6f75849b"
    private let C = "45c48cce-2e2d-4fa8-8aec-0eb4779d1ba9"

    private func waiting(_ id: String, latestId: Int64 = 5) -> WaitingSession {
        WaitingSession(sessionId: id, latestId: latestId, createdAtMs: 0,
                       cwd: "/Users/x/Projects/thing", transcriptPath: "/t.jsonl", hookEvent: .stop)
    }

    private func live(_ id: String, status: String? = nil) -> LiveSession {
        var s = LiveSession(pid: 100, sessionId: id)
        s.cwd = "/Users/x/Projects/thing"
        s.status = status
        return s
    }

    private func inputs(waiting: [WaitingSession], known: [WaitingSession] = [],
                        live: [String: LiveSession], switchedOff: Set<String> = [],
                        rightHands: Set<String>?) -> GridAssembler.RowInputs {
        GridAssembler.RowInputs(
            waiting: waiting, known: known, discovered: [], liveById: live,
            boundaries: [:], switchedOff: switchedOff, switchedOn: [],
            evidence: { _, _ in nil }, isHeadless: { _ in false }, family: { [$0] },
            supersedesWaiting: { _, _ in false }, isInFlight: { _ in false },
            recordedTurns: [], rightHands: rightHands)
    }

    /// Nil is the panel as it always was: two green rows, both on the grid.
    func testNoRosterChangesNothing() {
        let verdict = GridAssembler.rows(inputs(
            waiting: [waiting(A), waiting(B)], live: [A: live(A), B: live(B)], rightHands: nil))
        XCTAssertEqual(verdict.rows.map(\.lamp), [.ready, .ready])
        XCTAssertFalse(verdict.rows.contains { $0.switchedOff })
        XCTAssertEqual(EarconGate.arrivalKeys(verdict.rows), [A, B])
    }

    /// The stranger's WAITING turn is filed. This is the case the lamp switch
    /// cannot express — its whole policy is that a waiting turn un-files —
    /// and the reason the roster is not the switch.
    func testAWaitingStrangerIsFiledAndDoesNotChime() {
        let verdict = GridAssembler.rows(inputs(
            waiting: [waiting(A), waiting(B)], live: [A: live(A), B: live(B)], rightHands: [A]))
        let a = verdict.rows.first { $0.id == A }!
        let b = verdict.rows.first { $0.id == B }!
        XCTAssertEqual(a.lamp, .ready)
        XCTAssertFalse(a.switchedOff)
        XCTAssertTrue(b.switchedOff, "not a hand: filed")
        XCTAssertEqual(b.lamp, .running, "a filed row wears the switched-off lamp, as the list expects")
        XCTAssertEqual(b.read, .unread, "still owed, still bold on the list")
        XCTAssertEqual(EarconGate.arrivalKeys(verdict.rows), [A], "no key, no chime, no count")
        XCTAssertTrue(verdict.clearSwitches.isEmpty, "nothing is written for a stranger")
        XCTAssertEqual(SessionRow.gridRows(verdict.rows, capacity: 10, floor: 1).map(\.id), [A])
    }

    /// An amber stranger — the process says it is blocked — is filed too:
    /// the panel is not for it, and amber is the ask channel that chimes.
    func testABlockedStrangerIsFiledToo() {
        let verdict = GridAssembler.rows(inputs(
            waiting: [waiting(A), waiting(B)],
            live: [A: live(A), B: live(B, status: "waiting")], rightHands: [A]))
        let b = verdict.rows.first { $0.id == B }!
        XCTAssertTrue(b.switchedOff)
        XCTAssertEqual(EarconGate.arrivalKeys(verdict.rows), [A])
    }

    /// A working stranger (blue) is filed; a working hand stays.
    func testAWorkingStrangerIsFiled() {
        let verdict = GridAssembler.rows(inputs(
            waiting: [], known: [waiting(A), waiting(B)],
            live: [A: live(A, status: "busy"), B: live(B, status: "busy")], rightHands: [A]))
        XCTAssertEqual(verdict.rows.first { $0.id == A }?.switchedOff, false)
        XCTAssertEqual(verdict.rows.first { $0.id == B }?.switchedOff, true)
    }

    /// A dead row is left alone: it is on the list by its lamp already, and
    /// `switchedOffCopy` would say it is alive.
    func testADeadStrangerKeepsItsUnlitLamp() {
        let verdict = GridAssembler.rows(inputs(
            waiting: [waiting(B)], live: [:], rightHands: [A]))
        let b = verdict.rows.first { $0.id == B }!
        XCTAssertEqual(b.lamp, .unlit)
        XCTAssertFalse(b.switchedOff)
        XCTAssertTrue(b.revivable)
    }

    /// An empty roster files everyone. Distinct from nil, which files nobody.
    func testAnEmptyRosterFilesEveryone() {
        let verdict = GridAssembler.rows(inputs(
            waiting: [waiting(A), waiting(B)], live: [A: live(A), B: live(B)], rightHands: []))
        XCTAssertTrue(verdict.rows.allSatisfy(\.switchedOff))
        XCTAssertTrue(EarconGate.arrivalKeys(verdict.rows).isEmpty)
    }

    /// The user's own switch still applies to a hand: a hand filed by hand
    /// comes back when it waits, exactly as before, and the loop is told.
    func testTheSwitchStillGovernsAHand() {
        let verdict = GridAssembler.rows(inputs(
            waiting: [waiting(A)], live: [A: live(A)], switchedOff: [A, C], rightHands: [A, C]))
        XCTAssertEqual(verdict.rows.first { $0.id == A }?.switchedOff, false,
                       "a waiting turn turns a hand's lamp back on")
        XCTAssertEqual(verdict.clearSwitches, [A])
    }
}
