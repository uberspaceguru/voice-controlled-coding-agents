import XCTest
@testable import TranquilityCore

/// Ahmed's panel, 24 Sep (RIGHT-HANDS-UX.md and its two drawings): four rows
/// in his order, a dot for "needs me" and nothing else on a row, Director
/// opening like an accordion (one line, three items, more…), Go to Agent in
/// Ghostty.
final class RightHandsPanelTests: XCTestCase {
    private let director = "ac03daf5-bd0a-42a7-91b2-fe789e3f8a1a"
    private let yobi = "e781aff1-defa-4367-a184-437d093ca87e"
    private let s3po = "2b973845-36c8-4c7d-8d31-18b6bc13821c"
    private let stranger = "c4ca4238-a0b9-4382-8dcc-509a6f75849b"

    private var roster: RightHands.Roster {
        RightHands.Roster(hands: [
            RightHands.Hand(name: "Director", session: director, projects: ["director", "--json", "status"],
                            ask: ["director", "ask", "{text}"]),
            RightHands.Hand(name: "Yobi1", session: yobi),
            RightHands.Hand(name: "Sys-3PO", session: s3po),
            RightHands.Hand(name: "TeamChat Manager"),
        ])
    }

    private var card: RightHands.Rollup {
        RightHands.Rollup(projects: [
            .init(name: "Wispr", state: .needsYou, line: "Decision on the insertion fix"),
            .init(name: "Memory", state: .needsYou, line: "Which store to keep"),
            .init(name: "To-do list", state: .needsYou, line: ""),
            .init(name: "GA fab", state: .moving, line: "Writing the harness"),
        ], needsYou: 5)
    }

    private func live(_ id: String, status: String? = "idle") -> LiveSession {
        var s = LiveSession(pid: 100, sessionId: id)
        s.cwd = "/tmp/x"
        s.status = status
        return s
    }

    private func verdict(expanded: String? = nil, cards: [String: RightHands.Rollup]? = nil,
                         waiting: [WaitingSession] = []) -> [SessionRow] {
        let resolved = roster.resolve(sessions: [], ownership: [])
        return GridAssembler.rows(GridAssembler.RowInputs(
            waiting: waiting, known: [yobi, s3po, stranger].map {
                WaitingSession(sessionId: $0, latestId: 1, createdAtMs: 0, hookEvent: .stop)
            },
            discovered: [], liveById: [yobi: live(yobi), s3po: live(s3po), stranger: live(stranger)],
            boundaries: [:], switchedOff: [], switchedOn: [],
            evidence: { _, _ in nil }, isHeadless: { _ in false }, family: { [$0] },
            supersedesWaiting: { _, _ in false }, isInFlight: { _ in false },
            recordedTurns: [yobi, s3po], rightHands: resolved.ids,
            brains: [director: "Director"], handOrder: resolved.order, handNames: resolved.names,
            cards: cards ?? [director: card], expanded: expanded)).rows
    }

    // MARK: - Four rows

    func testThePlaceholderIsAHandInTheRostersOrder() {
        let resolved = roster.resolve(sessions: [], ownership: [])
        XCTAssertEqual(resolved.order, [director, yobi, s3po, "hand:teamchat-manager"])
        XCTAssertEqual(resolved.names["hand:teamchat-manager"], "TeamChat Manager")
        XCTAssertTrue(resolved.hands["hand:teamchat-manager"]?.isPlaceholder ?? false)
    }

    func testTheGridIsFourRowsInAhmedsOrderAndNothingElse() {
        let rows = verdict()
        let grid = SessionRow.gridRows(rows, capacity: 20, floor: 8)
        XCTAssertEqual(grid.map(\.name), ["Director", "Yobi1", "Sys-3PO", "TeamChat Manager"])
        XCTAssertTrue(grid.allSatisfy { $0.aux.isEmpty }, "a dot, and nothing else on the row")
        XCTAssertEqual(SessionRow.shownCount(rows, capacity: 20, floor: 8), 4)
        XCTAssertTrue(rows.contains { $0.id == stranger && $0.switchedOff }, "everyone else is filed, one page away")
    }

    func testADotMeansNeedsMe() {
        let rows = verdict(waiting: [WaitingSession(sessionId: yobi, latestId: 2, createdAtMs: 0, hookEvent: .stop)])
        let lamp = Dictionary(rows.filter(\.pinned).map { ($0.name, $0.lamp) }, uniquingKeysWith: { a, _ in a })
        XCTAssertEqual(lamp["Director"], .ready, "Director's card says five things need him")
        XCTAssertEqual(lamp["Yobi1"], .ready, "a turn waiting is something for him")
        XCTAssertEqual(lamp["Sys-3PO"], .running, "idle is a hollow dot")
        XCTAssertEqual(lamp["TeamChat Manager"], .unlit, "a placeholder is greyed")
        for row in rows where row.pinned && row.lamp == .ready {
            XCTAssertEqual(row.read, .unread, "\(row.name): a dot is solid; an opened green draws as a ring")
        }
        let quiet = verdict(cards: [director: RightHands.Rollup(projects: [], needsYou: 0)])
        XCTAssertEqual(quiet.first { $0.name == "Director" }?.lamp, .running)
        let cold = verdict(cards: [:])
        XCTAssertEqual(cold.first { $0.name == "Director" }?.lamp, .running,
                       "before the first card arrives there is no dot to claim")
    }

    func testAPlaceholderTapIsRefusedNotRevived() {
        let placeholder = verdict().first { $0.name == "TeamChat Manager" }!
        XCTAssertFalse(placeholder.revivable)
        XCTAssertEqual(SessionRow.action(for: placeholder), .none)
    }

    // MARK: - The accordion

    func testDirectorOpensToALineThreeItemsAndMore() {
        let grid = SessionRow.gridRows(verdict(expanded: director), capacity: 20, floor: 8)
        XCTAssertEqual(grid.map(\.name), [
            "Director", "5 things need you",
            "Wispr: Decision on the insertion fix", "Memory: Which store to keep", "To-do list",
            "more…", "Yobi1", "Sys-3PO", "TeamChat Manager"])
        XCTAssertEqual(grid[1].parentId, director)
        XCTAssertEqual(grid[2].lamp, .running, "a line is not an agent: no status dot (25 Sep)")
        XCTAssertEqual(grid[2].read, .none)
        XCTAssertEqual(grid[5].lamp, .running)
        for line in grid[1...5] {
            XCTAssertEqual(SessionRow.action(for: line), .announce, "every line is a door, never a terminal")
        }
    }

    func testTheLineIdsRoundTrip() {
        typealias A = RightHands.Accordion
        for part in [A.Part.summary, .item(1), .item(3), .more] {
            let id = A.id(director, part)
            XCTAssertEqual(A.part(of: id)?.parent, director)
            XCTAssertEqual(A.part(of: id)?.part, part)
        }
        XCTAssertNil(A.part(of: director))
        XCTAssertNil(A.part(of: director + "#item-x"))
    }

    func testWhatATapSays() {
        typealias A = RightHands.Accordion
        XCTAssertEqual(A.sentence(card), "5 things need you; first, Wispr: Decision on the insertion fix.")
        XCTAssertEqual(A.itemSentence(card.projects[0]), "Wispr needs you. Decision on the insertion fix.")
        XCTAssertEqual(A.itemSentence(card.projects[2]), "To-do list needs you.")
        XCTAssertEqual(A.explainRequest(card.projects[0]), "tell me more about Wispr",
                       "by name, so it binds whatever order Director last listed things in")
        XCTAssertEqual(A.summary(RightHands.Rollup(projects: [], needsYou: 1)), "1 thing needs you")
        XCTAssertEqual(A.sentence(RightHands.Rollup(projects: [], needsYou: 0)), "Nothing needs you.")
    }

    // MARK: - The card cache

    func testTheCacheRefreshesOnlyWhatIsStaleAndNeverTwiceAtOnce() {
        let cache = RightHands.CardCache()
        let now = Date()
        XCTAssertEqual(cache.claimStale(["a", "b"], maxAge: 30, now: now), ["a", "b"])
        XCTAssertEqual(cache.claimStale(["a", "b"], maxAge: 30, now: now), [], "claimed ones are in flight")
        cache.put(card, for: "a", at: now); cache.release("a"); cache.release("b")
        XCTAssertEqual(cache.claimStale(["a", "b"], maxAge: 30, now: now.addingTimeInterval(10)), ["b"])
        cache.release("b")
        XCTAssertEqual(cache.claimStale(["a"], maxAge: 30, now: now.addingTimeInterval(31)), ["a"])
        XCTAssertEqual(cache.card(for: "a")?.needsYou, 5)
    }

    // MARK: - Go to Agent

    func testGoToAgentIsAGhosttyAttach() {
        XCTAssertEqual(GhosttyDoor.openArguments(socket: "fleet", session: "w-a17"),
                       ["-na", "Ghostty", "--args", "-e", "tmux", "-L", "fleet", "attach", "-t", "w-a17"])
        XCTAssertEqual(GhosttyDoor.socket(for: "w-a17", hasSession: { sock, _ in sock == "fleet" }), "fleet")
        XCTAssertEqual(GhosttyDoor.socket(for: "x", hasSession: { sock, _ in sock == "default" }), "default")
        XCTAssertNil(GhosttyDoor.socket(for: "x", hasSession: { _, _ in false }))
        XCTAssertEqual(GhosttyDoor.candidateSockets.first, Tmux.socketName, "this app's own socket first")
    }

    // MARK: - Director's own panel lines (25 Sep)

    private let status = """
    {"groups": {"needs_you": [{"name": "w-a19", "session_id": "s1"}], "working": [], "idle": []},
     "needs": {"summary": "Seven things need you: approval for TypeSafe, a model for TeamChat iOS, one more.",
               "lines": [{"line": "Code hygiene: approval needed to send private repository…", "full": "x"},
                         {"line": "TeamChat: switch model for TeamChat iOS?"},
                         {"line": "React web app: switch model for React parity?"},
                         {"line": "YobiWork: a decision, waiting 6 days"},
                         {"line": "Yobi1: something to do, waiting since yesterday"},
                         {"line": "Wispr: something to do, waiting 7 days"}]}}
    """

    func testDirectorsPanelLinesAreTheItemsAndItsSentenceTheSummary() throws {
        let card = try XCTUnwrap(RightHands.Rollup.parse(Data(status.utf8)))
        XCTAssertEqual(card.panelSummary, "Seven things need you: approval for TypeSafe, a model for TeamChat iOS, one more.")
        XCTAssertEqual(card.panelLines.count, 6)
        let rows = RightHands.Accordion.rows(parent: director, card: card,
                                             said: "Ahmed, seven things need you; first, Code hygiene.")
        XCTAssertEqual(rows.map(\.name), [
            "Ahmed, seven things need you; first, Code hygiene.",
            "Code hygiene: approval needed to send private repository\u{2026}",
            "TeamChat: switch model for TeamChat iOS?",
            "React web app: switch model for React parity?",
            "more\u{2026}"])
        XCTAssertEqual(RightHands.Accordion.rows(parent: director, card: card).first?.name,
                       card.panelSummary, "without a said sentence, Director's panel summary")
    }

    func testMoreRevealsWhatDirectorNumbersAndThenIsGone() throws {
        let card = try XCTUnwrap(RightHands.Rollup.parse(Data(status.utf8)))
        let all = RightHands.Accordion.rows(parent: director, card: card, all: true)
        XCTAssertEqual(all.count, 1 + RightHands.Accordion.most, "the summary and five lines")
        XCTAssertFalse(all.contains { $0.name == "more\u{2026}" }, "nothing more to show")
    }

    func testAnItemIsAskedAboutByNumber() {
        XCTAssertEqual(RightHands.Accordion.explainRequest(number: 2), "tell me more about number 2")
    }
}
