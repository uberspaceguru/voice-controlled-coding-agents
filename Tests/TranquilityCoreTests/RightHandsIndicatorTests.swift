import XCTest
@testable import TranquilityCore

/// tb-indicators (25 Sep). Ahmed: "it's not just voice; the little
/// indicators that say this agent is waiting on me matter." The same truth as
/// the spoken sentence: a hand's row is filled only when something waits on
/// him, hollow when work is moving, nothing when quiet; the count in the
/// summary is the count of filled lines; each line carries one glyph for what
/// it is (waiting on you, ready to look at, blocked on another agent).
final class RightHandsIndicatorTests: XCTestCase {
    private let director = "ac03daf5-bd0a-42a7-91b2-fe789e3f8a1a"
    private let yobi = "e781aff1-defa-4367-a184-437d093ca87e"
    private let s3po = "2b973845-36c8-4c7d-8d31-18b6bc13821c"

    private func status(needs: [String], working: [String] = [], blocked: [[String: String]] = [],
                        needsYouGroup: Int = 0) -> RightHands.Rollup {
        let object: [String: Any] = [
            "groups": ["needs_you": (0..<needsYouGroup).map { ["name": "a\($0)", "session_id": "s\($0)"] },
                       "working": working.map { ["name": "w", "session_id": $0] },
                       "blocked": blocked, "idle": []],
            "needs": ["summary": needs.isEmpty ? "Nothing needs you." : "\(needs.count) things need you.",
                      "lines": needs.map { ["line": $0, "full": $0, "source": "work_item"] }],
        ]
        return RightHands.Rollup.parse(try! JSONSerialization.data(withJSONObject: object))!
    }

    private func rows(cards: [String: RightHands.Rollup], brains: [String: String]? = nil,
                      expanded: String? = nil, all: Bool = false) -> [SessionRow] {
        let roster = RightHands.Roster(hands: [
            RightHands.Hand(name: "Director", session: director, projects: ["director", "--json", "status"],
                            ask: ["director", "ask", "{text}"]),
            RightHands.Hand(name: "Yobi1", session: yobi),
            RightHands.Hand(name: "Sys-3PO", session: s3po),
        ])
        let resolved = roster.resolve(sessions: [], ownership: [])
        var live = LiveSession(pid: 100, sessionId: yobi); live.cwd = "/tmp/x"; live.status = "idle"
        var live2 = LiveSession(pid: 101, sessionId: s3po); live2.cwd = "/tmp/y"; live2.status = "idle"
        return GridAssembler.rows(GridAssembler.RowInputs(
            waiting: [], known: [yobi, s3po].map {
                WaitingSession(sessionId: $0, latestId: 1, createdAtMs: 0, hookEvent: .stop)
            },
            discovered: [], liveById: [yobi: live, s3po: live2],
            boundaries: [:], switchedOff: [], switchedOn: [],
            evidence: { _, _ in nil }, isHeadless: { _ in false }, family: { [$0] },
            supersedesWaiting: { _, _ in false }, isInFlight: { _ in false },
            recordedTurns: [yobi, s3po], rightHands: resolved.ids,
            brains: brains ?? [director: "Director"], handOrder: resolved.order, handNames: resolved.names,
            cards: cards, expanded: expanded, expandedAll: all)).rows
    }

    private func lamp(_ rows: [SessionRow], _ name: String) -> Lamp? {
        rows.first { $0.pinned && $0.name == name }?.lamp
    }

    // MARK: - The row: filled, hollow, nothing

    func testDirectorsRowIsFilledOnlyByItsNeedsList() {
        // Seven agents in needs_you before the noise rules; Director's needs
        // list after them is empty: no dot.
        let quiet = status(needs: [], needsYouGroup: 7)
        XCTAssertEqual(quiet.needsYou, 0, "the count is Director's needs list, not the raw group")
        XCTAssertEqual(lamp(rows(cards: [director: quiet]), "Director"), .running, "quiet: no dot")

        let moving = status(needs: [], working: ["x"])
        XCTAssertEqual(lamp(rows(cards: [director: moving]), "Director"), .working, "moving: the hollow ring")

        let asks = status(needs: ["Wispr: run this command?"], working: ["x"])
        let row = rows(cards: [director: asks]).first { $0.name == "Director" }
        XCTAssertEqual(row?.lamp, .ready, "filled wins over moving")
        XCTAssertEqual(row?.read, .unread, "and stays solid")
    }

    func testTheSummaryCountIsTheCountOfFilledLines() {
        let lines = (1...9).map { "Project \($0): something" }
        let card = status(needs: lines, blocked: [["name": "w-a3", "worker_note": "waiting on w-a2"]])
        XCTAssertEqual(card.needsYou, 9)
        let open = rows(cards: [director: card], expanded: director, all: true).filter { $0.parentId == director }
        XCTAssertEqual(open.first?.name, "9 things need you.", "Director's own sentence, over the same list")
        let filled = open.filter { $0.lamp == .ready }
        XCTAssertEqual(filled.count, 9, "every waiting line is shown once more… is pressed")
        XCTAssertEqual(RightHands.Accordion.summary(card), "9 things need you")

        let closed = rows(cards: [director: card], expanded: director).filter { $0.parentId == director }
        XCTAssertEqual(closed.map(\.name).last, "7 more…", "three shown, and the rest said as a number")
    }

    func testEachLineCarriesItsKindAndOnlyWaitingIsCounted() {
        var card = status(needs: ["Wispr: a decision"],
                          blocked: [["name": "w-a3", "worker_note": "waiting on w-a2's merge"]])
        card.items += RightHands.Rollup.readyItems(Data("""
            {"items": [{"kind": "worker_done", "title": "React web app: GTM call queue in React, ready to look at."}]}
            """.utf8))
        XCTAssertEqual(card.needsYou, 1, "ready and blocked are never counted")
        let entries = RightHands.Accordion.entries(card)
        XCTAssertEqual(entries.map(\.kind), [.waiting, .ready, .blocked], "waiting, then ready, then blocked")
        XCTAssertEqual(entries[1].line, "React web app: GTM call queue in React", "the glyph says ready; the words need not")
        XCTAssertEqual(entries[2].line, "w-a3: waiting on w-a2's merge")
        let open = RightHands.Accordion.rows(parent: director, card: card, all: true).dropFirst()
        XCTAssertEqual(open.map(\.lamp), [.ready, .working, .running],
                       "the glyphs: ● waiting, ◆ ready, ◌ blocked (NestedRowView)")
    }

    // MARK: - Yobi1 and Sys-3PO: their own status

    func testAHandsOwnStatusCardUsesTheSameThreeStates() throws {
        let waiting = try XCTUnwrap(RightHands.Rollup.parse(Data("""
            {"summary": "Sys-3PO: 1 thing needs you.", "moving": true,
             "items": [{"line": "Sys-3PO: blocked on Director", "kind": "blocked"},
                       {"line": "Mac: swap is thrashing", "kind": "waiting"}]}
            """.utf8)))
        XCTAssertEqual(waiting.needsYou, 1)
        XCTAssertEqual(waiting.items.map(\.kind), [.waiting, .blocked])
        XCTAssertEqual(RightHands.Accordion.summaryText(waiting), "Sys-3PO: 1 thing needs you.")
        let working = try XCTUnwrap(RightHands.Rollup.parse(Data("""
            {"summary": "Yobi1 is working; nothing needs you.", "moving": true, "items": []}
            """.utf8)))
        let quiet = try XCTUnwrap(RightHands.Rollup.parse(Data("""
            {"summary": "Nothing from Yobi1 needs you.", "moving": false, "items": []}
            """.utf8)))
        let brains = [director: "Director", yobi: "Yobi1", s3po: "Sys-3PO"]
        let grid = rows(cards: [s3po: waiting, yobi: working], brains: brains)
        XCTAssertEqual(lamp(grid, "Sys-3PO"), .ready)
        XCTAssertEqual(lamp(grid, "Yobi1"), .working)
        XCTAssertEqual(lamp(rows(cards: [yobi: quiet], brains: brains), "Yobi1"), .running)
    }

    func testWithoutACardAHandIsItsSessionAsDirectorSeesIt() {
        // Prod's roster: Yobi1 has no brain and no card; Director's store
        // says its session is working, then that it waits on Ahmed.
        let working = RightHands.Rollup(projects: [], needsYou: 0, workingSessions: [yobi])
        XCTAssertEqual(lamp(rows(cards: [director: working]), "Yobi1"), .working)
        let asks = RightHands.Rollup(projects: [], needsYou: 0, needsSessions: [yobi])
        XCTAssertEqual(lamp(rows(cards: [director: asks]), "Yobi1"), .ready)
        XCTAssertEqual(lamp(rows(cards: [:]), "Yobi1"), .running, "idle: nothing")
    }

    func testReadyTitlesAreCutAtAWord() {
        XCTAssertEqual(RightHands.Rollup.cut("Yobi1 email: Jev shadow triage, commitments and waiting-on, personal CRM, ready to look at."),
                       "Yobi1 email: Jev shadow triage, commitments and waiting-on…")
    }
}

/// Pending actions on the Director card (25 Sep night): long work, read on the card, never spoken.
final class PendingActionsTests: XCTestCase {
    private let json = """
    {"actions": [
      {"id": 12, "what": "Restart the TeamChat desktop worker with approvals off", "state": "awaiting_ahmed",
       "target": "w-a21", "item_id": "A21", "requested_at": 1790380800000},
      {"id": 11, "what": "Find out what the GPU worker is stuck on", "state": "in_progress", "target": "w-a18",
       "requested_at": 1790380200000, "started_at": 1790380210000},
      {"id": 9, "what": "Stage the Wispr fix", "state": "done", "finished_at": 1790379600000},
      {"id": 8, "what": "", "state": "done"},
      {"id": 7, "what": "Something", "state": "exploded"}
    ]}
    """

    func testParseChipAndOrder() throws {
        let actions = try XCTUnwrap(PendingActions.parse(Data(json.utf8)))
        XCTAssertEqual(actions.map(\.id), [12, 11, 9], "rows with no words or an unknown state are skipped")
        let clock: (Date) -> String = { _ in "21:04" }
        XCTAssertEqual(actions.map { PendingActions.chip($0, clock: clock) },
                       ["awaiting your approval", "in progress since 21:04", "done at 21:04"])
        XCTAssertEqual(PendingActions.shown(actions.reversed()).map(\.id), [12, 11, 9],
                       "waiting on him first, then in progress, then finished")
        XCTAssertEqual(PendingActions.parse(Data("[]".utf8)), [])
        XCTAssertNil(PendingActions.parse(Data("not json".utf8)))
    }

    func testApproveSendsOneExplicitYesForThatActionOnly() throws {
        let a = try XCTUnwrap(PendingActions.parse(Data(json.utf8))?.first)
        XCTAssertEqual(PendingActions.approveArgv(a, thread: "ac03"),
                       ["director", "--json", "ask",
                        "Yes, I approve action 12: Restart the TeamChat desktop worker with approvals off (A21).",
                        "--named", "--channel", "tranquility", "--external-id", "ac03"])
        var ran: [[String]] = []
        let r = PendingActions.approve(a, thread: "ac03") { _, args, _ in
            ran.append(args); return .success(#"{"reply": "Approved; restarting it."}"#)
        }
        XCTAssertEqual(try r.get(), "Approved; restarting it.")
        XCTAssertEqual(ran.count, 1)
        var done = a; done.state = .done
        if case .success = PendingActions.approve(done, thread: "ac03", run: { _, _, _ in .success("") }) {
            XCTFail("only an action waiting on him can be approved")
        }
    }

    func testActionsAreRowsWithDoorsUnderTheHand() {
        var card = RightHands.Rollup(projects: [], needsYou: 0, panelSummary: "Nothing needs you.")
        card.actions = PendingActions.parse(Data(json.utf8)) ?? []
        let rows = RightHands.Accordion.rows(parent: "d", card: card)
        let acts = rows.filter { if case .action = RightHands.Accordion.part(of: $0.id)?.part { return true }; return false }
        XCTAssertEqual(acts.map(\.name).first, "Restart the TeamChat desktop worker with approvals off")
        XCTAssertEqual(acts.map(\.lamp), [.ready, .working, .unlit])
        XCTAssertEqual(acts.first?.detail, "w-a21")
        for part in [RightHands.Accordion.Part.action(12), .approve(12), .goTo(12)] {
            XCTAssertEqual(RightHands.Accordion.part(of: RightHands.Accordion.id("d", part))?.part, part)
        }
    }
}
