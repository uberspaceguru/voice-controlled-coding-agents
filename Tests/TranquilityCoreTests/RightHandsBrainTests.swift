import XCTest
@testable import TranquilityCore

/// A right-hand with a brain (24 Sep): Director is a command, not a pane. Its
/// card is `director --json status`, what the user says to it is answered by
/// `director ask`, and its row stands ready whether or not a process runs
/// under its id.
final class RightHandsBrainTests: XCTestCase {
    private var tmpDir: URL!
    private var store: QueueStore!
    private let director = "87469f47-f2b2-410e-9ac5-58c6363a19f3"

    override func setUpWithError() throws {
        tmpDir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("vd-rh-brain-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
        store = try QueueStore(url: tmpDir.appendingPathComponent("queue.sqlite"))
        RightHands.publish(nil)
        BrainTransport.answered = nil
    }

    override func tearDownWithError() throws {
        store = nil
        RightHands.publish(nil)
        BrainTransport.answered = nil
        try? FileManager.default.removeItem(at: tmpDir)
    }

    // MARK: - The file

    func testAskAndProjectsParseFromTheFile() throws {
        let data = Data("""
        {"hands": [{"name": "Director", "session": "\(director)",
          "projects": ["director", "--json", "status"],
          "ask": ["director", "ask", "{text}", "--channel", "tranquility", "--external-id", "{conversation}"]}]}
        """.utf8)
        guard case .success(let roster) = RightHands.parse(data) else { return XCTFail("did not parse") }
        let hand = try XCTUnwrap(roster.hands.first)
        XCTAssertTrue(hand.asks)
        XCTAssertTrue(hand.hasCard)
        XCTAssertEqual(hand.projects, ["director", "--json", "status"])
        let resolved = roster.resolve(sessions: [], ownership: [])
        XCTAssertEqual(resolved.hands[director]?.name, "Director")
    }

    func testTheTemplateIsFilledAsOneArgumentPerSlot() {
        let argv = RightHands.fill(["director", "ask", "{text}", "--external-id", "{conversation}"],
                                   text: "ship it; rm -rf / \"now\"", conversation: "c-1", session: director)
        XCTAssertEqual(argv, ["director", "ask", "ship it; rm -rf / \"now\"", "--external-id", "c-1"],
                       "the words are one argv element and never reach a shell")
    }

    func testAskRunsTheTemplateAndTrimsTheAnswer() {
        let hand = RightHands.Hand(name: "Director", ask: ["/bin/echo", "heard:", "{text}"])
        XCTAssertEqual(RightHands.ask(hand, text: "what needs me?", conversation: "c", session: director),
                       .success("heard: what needs me?"))
        XCTAssertEqual(RightHands.ask(RightHands.Hand(name: "Yobi1"), text: "x", conversation: "c", session: "s"),
                       .failure(.notABrain))
        let missing = RightHands.Hand(ask: ["no-such-program-anywhere", "{text}"])
        guard case .failure(.failed(let why)) = RightHands.ask(missing, text: "x", conversation: "c", session: "s")
        else { return XCTFail("a missing program must fail with its reason") }
        XCTAssertTrue(why.contains("not on this Mac"), why)
    }

    // MARK: - Director's status as a card

    private let status = """
    {"groups": {
      "needs_you": [
        {"name": "w-a17", "worker_note": "a Granola key stored via `security add-generic-password -s x` in the Keychain.", "reasons": ["Jev: waiting on Ahmed (ahmed_action, 100%)"]},
        {"name": "S3P0-cursor", "reasons": ["Jev: waiting on Ahmed (ahmed_decision, 100%)"], "last_message": "The backlog page is ready for your review."},
        {"name": "y1-cc", "pending_question": "Should I backfill June too?"}],
      "working": [{"name": "GA-react-cc", "reasons": ["leading 5 live teammate(s)"]}],
      "stuck": [{"name": "w-a20"}], "idle": [{"name": "a"}, {"name": "b"}]},
     "work": {}}
    """

    func testDirectorStatusReadsAsACard() throws {
        let rollup = try XCTUnwrap(RightHands.Rollup.parse(Data(status.utf8)))
        XCTAssertEqual(rollup.projects.map(\.name), ["w-a17", "S3P0-cursor", "y1-cc", "GA-react-cc"])
        XCTAssertEqual(rollup.projects.map(\.state), [.needsYou, .needsYou, .needsYou, .moving])
        XCTAssertEqual(rollup.projects[0].line, "a Granola key stored via a command in the Keychain",
                       "a command in backticks is for reading, not hearing")
        XCTAssertEqual(rollup.projects[1].line, "The backlog page is ready for your review",
                       "the judge's bookkeeping is not a line; the agent's own last word is")
        XCTAssertEqual(rollup.projects[2].line, "Should I backfill June too?")
        XCTAssertEqual(rollup.projects[3].line, "leading 5 live teammate(s)")
        let brief = rollup.brief(topic: "Director")
        XCTAssertEqual(brief.happened, "3 need you, 1 working, 1 stuck, 2 idle.",
                       "the whole fleet is counted, not only the five shown")
        XCTAssertTrue(brief.spokenText().hasPrefix("Director. 3 need you, 1 working, 1 stuck, 2 idle. w-a17 needs you."),
                      brief.spokenText())
    }

    func testTheProjectsCommandIsTheCard() throws {
        let file = tmpDir.appendingPathComponent("status.json")
        try status.write(to: file, atomically: true, encoding: .utf8)
        let hand = RightHands.Hand(name: "Director", rollup: "/nonexistent.json", projects: ["/bin/cat", file.path])
        XCTAssertEqual(RightHands.card(for: hand)?.projects.count, 4, "the command outranks the file")
        let broken = RightHands.Hand(name: "Director", projects: ["/usr/bin/false"])
        XCTAssertNil(RightHands.card(for: broken))
    }

    // MARK: - A reply is asked, and the answer comes back

    struct FakeAgents: ClaudeAgentsReading {
        func sessions() -> [LiveSession]? { [] }
    }

    final class Heard: @unchecked Sendable {
        let lock = NSLock()
        var lines: [(String, String, String)] = []
        func add(_ l: (String, String, String)) { lock.lock(); lines.append(l); lock.unlock() }
    }

    func testATypedReplyToABrainIsAskedAndAnswered() async throws {
        _ = try store.insert(event: QueuedEvent(
            createdAtMs: 1_000, hookEvent: .stop, sessionId: director, promptId: "p",
            cwd: "/tmp/Director", lastAssistantMessage: "done", tty: nil))
        let roster = RightHands.Roster(hands: [
            RightHands.Hand(name: "Director", session: director, ask: ["/bin/echo", "Director heard:", "{text}", "in", "{conversation}"]),
        ])
        let c = Coordinator(store: store,
                            enrolment: EnrolmentRegistry(url: tmpDir.appendingPathComponent("enrolled.json")),
                            agents: FakeAgents(),
                            ownership: RightHandsCoordinatorTests.StubOwnership(records: []),
                            readinessGrace: 0, rightHands: { roster })
        let heard = Heard()
        BrainTransport.answered = { heard.add(($0, $1, $2)) }
        guard case .readyToSend(let id, _, _, _) = try await c.submitTypedReply(text: "what needs me?", to: director)
        else { return XCTFail("the typed reply was not prepared") }
        let outcome = try await c.confirmAndSend(utteranceId: id)
        guard case .dispatched(_, _, let session, _) = outcome else { return XCTFail("\(outcome)") }
        XCTAssertEqual(session, director)
        XCTAssertEqual(heard.lines.count, 1)
        XCTAssertEqual(heard.lines.first?.1, "Director")
        XCTAssertTrue(heard.lines.first?.2.hasPrefix("Director heard:") ?? false)
        XCTAssertTrue(heard.lines.first?.2.hasSuffix("in \(director)") ?? false,
                      "the conversation is the hand's own session id, one thread across card and voice")
        XCTAssertEqual(try store.utterances(limit: 5).first { $0.id == id }?.status, .confirmed)
    }

    // MARK: - The row and the target

    func testABrainStandsReadyWithOrWithoutARow() {
        let dead = WaitingSession(sessionId: director, latestId: 1, createdAtMs: 0, hookEvent: .stop)
        func rows(_ waiting: [WaitingSession]) -> [SessionRow] {
            GridAssembler.rows(GridAssembler.RowInputs(
                waiting: waiting, known: [], discovered: [], liveById: [:],
                boundaries: [:], switchedOff: [], switchedOn: [],
                evidence: { _, _ in nil }, isHeadless: { _ in false }, family: { [$0] },
                supersedesWaiting: { _, _ in false }, isInFlight: { _ in false },
                recordedTurns: [], rightHands: [director], brains: [director: "Director"])).rows
        }
        let withTurn = rows([dead])
        XCTAssertEqual(withTurn.count, 1)
        XCTAssertEqual(withTurn[0].lamp, .ready, "no process is not dead for a command")
        XCTAssertFalse(withTurn[0].revivable, "reviving would start a Claude session, which is not Director")
        XCTAssertEqual(withTurn[0].name, "Director")
        XCTAssertEqual(SessionRow.action(for: withTurn[0]), .announce, "the tap opens the card")
        let withoutAnything = rows([])
        XCTAssertEqual(withoutAnything.map(\.id), [director])
        XCTAssertEqual(withoutAnything[0].lamp, .ready)
    }

    func testABrainIsATargetEvenWithNoProcess() {
        let resolved = RightHands.Resolved(
            ids: [director], names: [director: "Director"],
            hands: [director: RightHands.Hand(name: "Director", session: director, ask: ["director", "ask", "{text}"])])
        let targets = ManagerJSON.targets(store: store, live: [], isEnrolled: { _, _ in false }, rightHands: resolved)
        XCTAssertEqual(targets.map(\.name), ["Director"])
        XCTAssertEqual(targets.first?.asks, true)
        XCTAssertEqual(targets.first?.rightHand, true)
    }

    /// Opening the card of a hand that has never stopped under its id still
    /// reads its projects: the card is not a turn.
    func testThePickOfACardWithNoTurnSpeaksTheProjects() async throws {
        let file = tmpDir.appendingPathComponent("status.json")
        try status.write(to: file, atomically: true, encoding: .utf8)
        let roster = RightHands.Roster(hands: [
            RightHands.Hand(name: "Director", session: director, projects: ["/bin/cat", file.path]),
        ])
        let speech = RightHandsCoordinatorTests.SilentSpeech()
        let c = Coordinator(store: store, speech: SpeechChain(preferred: speech, fallback: speech),
                            gate: InterruptGate(minimumIdleSeconds: 0, signals: .quiescent),
                            agents: FakeAgents(), ownership: RightHandsCoordinatorTests.StubOwnership(records: []),
                            rightHands: { roster })
        let outcome = try await c.announceNext(only: director, ignoringGate: true)
        guard case .spoke(let announcement) = outcome else { return XCTFail("\(outcome)") }
        XCTAssertEqual(announcement.brief.happened, "3 need you, 1 working, 1 stuck, 2 idle.")
        XCTAssertEqual(speech.spoken.count, 1)
    }
}
