import XCTest
@testable import TranquilityCore

/// Tranquility Base Director (25 Sep): its own app beside Prod. Its own
/// folder, its own scheme, no hooks of its own (so Director's word lights the
/// dots), and a reply to Director with no turn stored.
final class DirectorAppTests: XCTestCase {
    private let director = "ac03daf5-bd0a-42a7-91b2-fe789e3f8a1a"
    private let yobi = "e781aff1-defa-4367-a184-437d093ca87e"

    func testTheFolderIsProdsUnlessTheBundleNamesAPlainOne() {
        XCTAssertEqual(QueueStore.supportFolder(named: nil), "VoiceDispatch")
        XCTAssertEqual(QueueStore.supportFolder(named: ""), "VoiceDispatch")
        XCTAssertEqual(QueueStore.supportFolder(named: "VoiceDispatch-Director"), "VoiceDispatch-Director")
        XCTAssertEqual(QueueStore.supportFolder(named: "../VoiceDispatch"), "VoiceDispatch", "no paths")
        XCTAssertEqual(QueueStore.supportFolder(named: ".hidden"), "VoiceDispatch")
    }

    /// Input Monitoring is optional in the Director app (25 Sep): its tap
    /// runs only once granted and never beside Prod; Prod's own tap is
    /// unchanged; a build with neither never listens.
    func testTheOptionHoldNeverRunsBesideProd() {
        XCTAssertTrue(AppIdentity.mayListenGlobally(granted: true, prodRunning: true,
                                                    enabled: true, optional: false))
        XCTAssertFalse(AppIdentity.mayListenGlobally(granted: false, prodRunning: false,
                                                     enabled: true, optional: false))
        XCTAssertTrue(AppIdentity.mayListenGlobally(granted: true, prodRunning: false,
                                                    enabled: false, optional: true))
        XCTAssertFalse(AppIdentity.mayListenGlobally(granted: true, prodRunning: true,
                                                     enabled: false, optional: true))
        XCTAssertFalse(AppIdentity.mayListenGlobally(granted: false, prodRunning: false,
                                                     enabled: false, optional: true))
        XCTAssertFalse(AppIdentity.mayListenGlobally(granted: true, prodRunning: false,
                                                     enabled: false, optional: false))
    }

    /// The Whisper key's summons (25 Sep): the link parses, and Director is
    /// asked on channel summons, with the context as JSON, in the given thread.
    func testASummonsLinkBecomesADirectorAskWithItsContext() throws {
        let url = try XCTUnwrap(URL(string:
            "tbdirector://summon?to=director&text=what%20is%20ready&app=com.mitchellh.ghostty&pane=fleet/%258"))
        guard case let .summon(s) = DeepLink.parse(url) else { return XCTFail("not a summons") }
        XCTAssertEqual(s, DeepLink.Summons(to: "director", text: "what is ready",
                                           app: "com.mitchellh.ghostty", pane: "fleet/%8"))
        XCTAssertEqual(RightHands.summonsArgv(s, thread: "ac03daf5"),
                       ["director", "ask", "what is ready", "--channel", "summons", "--external-id", "ac03daf5",
                        "--context", #"{"app":"com.mitchellh.ghostty","pane":"fleet\/%8"}"#])
        if case .summon = DeepLink.parse(URL(string: "tbdirector://summon?to=yobi1")!) {
            XCTFail("a summons with no words is not one")
        }
        guard case let .summon(y) = DeepLink.parse(URL(string: "tbdirector://summon?to=Yobi1&text=hi")!) else {
            return XCTFail("yobi1")
        }
        XCTAssertEqual(y.to, "yobi1")
    }

    func testItsOwnSchemeWins() {
        XCTAssertEqual(AppIdentity.preferredScheme(among: ["tbdirector"], channel: .director, own: "tbdirector"),
                       "tbdirector")
        XCTAssertEqual(AppIdentity.preferredScheme(among: ["tranquilitybase", "voicedispatch"], channel: .production),
                       "tranquilitybase", "Prod unchanged")
        XCTAssertEqual(AppChannel(rawValue: "director"), .director)
    }

    func testDirectorsNeedsListLightsAHandsDot() throws {
        let status = Data("""
        {"groups": {"needs_you": [{"name": "y1-cc", "session_id": "\(yobi)", "pending_question": "June too?"}],
                    "working": [], "idle": []}}
        """.utf8)
        let card = try XCTUnwrap(RightHands.Rollup.parse(status))
        XCTAssertEqual(card.needsSessions, [yobi])
        var idle = LiveSession(pid: 1, sessionId: yobi)
        idle.status = "idle"
        let rows = GridAssembler.rows(GridAssembler.RowInputs(
            waiting: [], known: [WaitingSession(sessionId: yobi, latestId: 1, createdAtMs: 0, hookEvent: .stop)],
            discovered: [], liveById: [yobi: idle], boundaries: [:], switchedOff: [], switchedOn: [],
            evidence: { _, _ in nil }, isHeadless: { _ in false }, family: { [$0] },
            supersedesWaiting: { _, _ in false }, isInFlight: { _ in false },
            recordedTurns: [yobi], rightHands: [director, yobi], brains: [director: "Director"],
            handOrder: [director, yobi], handNames: [director: "Director", yobi: "Yobi1"],
            cards: [director: card])).rows
        let yobiRow = try XCTUnwrap(rows.first { $0.id == yobi })
        XCTAssertEqual(yobiRow.lamp, .ready, "no waiting turn here, but Director says Yobi1 needs him")
        XCTAssertEqual(yobiRow.read, .unread)
    }

    struct NoAgents: ClaudeAgentsReading { func sessions() -> [LiveSession]? { [] } }

    final class Heard: @unchecked Sendable {
        var lines: [String] = []
    }

    func testAReplyToDirectorNeedsNoStoredTurn() async throws {
        let tmp = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("vd-director-app-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmp) }
        let store = try QueueStore(url: tmp.appendingPathComponent("queue.sqlite"))
        let roster = RightHands.Roster(hands: [
            RightHands.Hand(name: "Director", session: director, ask: ["/bin/echo", "heard:", "{text}"]),
        ])
        let c = Coordinator(store: store,
                            enrolment: EnrolmentRegistry(url: tmp.appendingPathComponent("enrolled.json")),
                            agents: NoAgents(), ownership: RightHandsCoordinatorTests.StubOwnership(records: []),
                            readinessGrace: 0, rightHands: { roster })
        let heard = Heard()
        BrainTransport.answered = { _, _, line in heard.lines.append(line) }
        defer { BrainTransport.answered = nil }
        XCTAssertTrue(try store.allKnownSessions().isEmpty, "a fresh folder: nothing stored")
        guard case .readyToSend(let id, _, _, _) = try await c.submitTypedReply(text: "what needs me?", to: director)
        else { return XCTFail("a brain with no stored turn must still take a reply") }
        guard case .dispatched = try await c.confirmAndSend(utteranceId: id) else { return XCTFail("not sent") }
        XCTAssertEqual(heard.lines, ["heard: what needs me?"])
        guard case .noTarget = try await c.submitTypedReply(text: "hi", to: yobi) else {
            return XCTFail("a session with no turn and no brain still has nowhere to go")
        }
    }
}
