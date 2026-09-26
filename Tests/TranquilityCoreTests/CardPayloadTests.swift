import XCTest
@testable import TranquilityCore

/// M26, 26 Sep (Ahmed: "it can show me things as the agent talks"): each
/// Director turn carries what its card shows under the sentence.
final class CardPayloadTests: XCTestCase {

    func testAListKeepsItsRowsAndDropsRowsWithNoTitle() {
        let json = #"{"kind":"list","title":"What needs you","rows":[{"n":1,"project":"YobiWispr","title":"run this command?","age":"3 h","agent":"w-a20-2"},{"n":2,"project":"x"},{"line":"a bare line"}]}"#
        guard case .list(let title, let rows)? = CardPayload.parse(json: json) else { return XCTFail("a list") }
        XCTAssertEqual(title, "What needs you")
        XCTAssertEqual(rows, [.init(n: 1, project: "YobiWispr", title: "run this command?", age: "3 h", agent: "w-a20-2"),
                              .init(title: "a bare line")])
        let many = #"{"kind":"list","rows":["# + (1...9).map { #"{"title":"t\#($0)"}"# }.joined(separator: ",") + "]}"
        guard case .list(_, let capped)? = CardPayload.parse(json: many) else { return XCTFail("a list") }
        XCTAssertEqual(capped.count, CardPayload.mostRows)
    }

    func testAnItemCarriesTheFullQuestionAndWhoAnswersIt() {
        let json = #"{"kind":"item","n":2,"project":"TeamChat","title":"desktop refresh fix","question":"Run this command? git worktree list","agent":"w-a21","reply":"approve","action_id":12}"#
        guard case .item(let item)? = CardPayload.parse(json: json) else { return XCTFail("an item") }
        XCTAssertEqual(item.question, "Run this command? git worktree list")
        XCTAssertEqual(item.reply, .approve)
        XCTAssertEqual(CardPayload.parse(json: json)?.approveText,
                       "Yes, I approve action 12: TeamChat: desktop refresh fix.")
        var noAction = item; noAction.actionId = nil
        XCTAssertEqual(CardPayload.approveText(noAction), "Yes, I approve number 2: TeamChat: desktop refresh fix.")
        let theirs = #"{"kind":"item","title":"a question for Yobi1","reply":"someone"}"#
        XCTAssertNil(CardPayload.parse(json: theirs)?.approveText, "not his: no Approve")
    }

    func testAnActionIsAPendingActionsRow() {
        let json = #"{"kind":"action","id":12,"what":"Restart the TeamChat worker","state":"in_progress","target":"w-a21","started_at":1790380210000}"#
        guard case .action(let a)? = CardPayload.parse(json: json) else { return XCTFail("an action") }
        XCTAssertEqual(a.state, .inProgress)
        XCTAssertEqual(a.target, "w-a21")
        XCTAssertTrue(PendingActions.chip(a).hasPrefix("in progress"))
        XCTAssertNil(CardPayload.parse(json: json)?.approveText, "in progress: nothing to approve")
        let titled = #"{"kind":"action","id":3,"title":"Merge it","state":"awaiting_ahmed"}"#
        XCTAssertEqual(CardPayload.parse(json: titled)?.approveText, "Yes, I approve action 3: Merge it.")
    }

    func testAScreenKeepsItsLastLines() {
        let lines = (1...30).map { "line \($0)" }
        let obj: [String: Any] = ["kind": "screen", "agent": "w-a21", "lines": lines]
        guard case .screen(let agent, let shown)? = CardPayload.parse(obj) else { return XCTFail("a screen") }
        XCTAssertEqual(agent, "w-a21")
        XCTAssertEqual(shown, Array(lines.suffix(CardPayload.mostLines)))
        guard case .screen(_, let fromText)? = CardPayload.parse(json: #"{"kind":"screen","text":"a\nb"}"#) else {
            return XCTFail("text is split into lines")
        }
        XCTAssertEqual(fromText, ["a", "b"])
    }

    func testNoneAndNonsenseClearTheCard() {
        for json in [#"{"kind":"none"}"#, "", "not json", #"{"kind":"chart"}"#, #"{"kind":"list","rows":[]}"#, #"[1]"#] {
            XCTAssertNil(CardPayload.parse(json: json), json)
        }
        XCTAssertNil(CardPayload.parse(json: nil))
    }

    func testDoorIdsRoundTripAndStayApartFromOtherRows() {
        for door in [CardPayload.Door.goTo(agent: "w-a21"), .answer(agent: "yobi:0.1"), .approve] {
            XCTAssertEqual(CardPayload.Door(id: door.id), door)
        }
        XCTAssertNil(CardPayload.Door(id: "ac03daf5-bd0a-42a7-91b2-fe789e3f8a1a"))
        XCTAssertNil(CardPayload.Door(id: "card#goto:"))
        XCTAssertNil(CardPayload.Door(id: "card#open:w-a21"))
    }

    func testApproveAsksInTheThreadAndHandsBackTheNextCard() {
        var ran: [[String]] = []
        let r = CardPayload.ask("Yes, I approve action 12: Merge it.", thread: "ac03") { _, args, _ in
            ran.append(args)
            return .success(#"{"reply":"Approved; merging.","card_json":{"kind":"action","id":12,"what":"Merge it","state":"in_progress"}}"#)
        }
        XCTAssertEqual(ran, [["--json", "ask", "Yes, I approve action 12: Merge it.", "--named", "--channel", "tranquility",
                              "--external-id", "ac03"]])
        let answer = try? r.get()
        XCTAssertEqual(answer?.reply, "Approved; merging.")
        guard case .action(let a)? = CardPayload.parse(json: answer?.card) else { return XCTFail("the next card") }
        XCTAssertEqual(a.state, .inProgress)
    }

    func testTheCardEventDecodes() throws {
        let line = #"{"event":"card","t":1.0,"session":"ac03","name":"Director","card":"{\"kind\":\"none\"}"}"#
        let e = try JSONDecoder().decode(ManagerEvent.self, from: Data(line.utf8))
        XCTAssertEqual(e.event, .card)
        XCTAssertEqual(e.card, #"{"kind":"none"}"#)
    }
}
