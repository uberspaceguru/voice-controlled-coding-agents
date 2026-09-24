import XCTest
@testable import TranquilityCore

/// The ear's half of the right-hands (23 Sep): what ⌃⌥ picks, what the badge
/// counts, and what a director's card says, with a roster handed in and
/// never a file read from this machine.
final class RightHandsCoordinatorTests: XCTestCase {
    private var tmpDir: URL!
    private var store: QueueStore!

    private let director = "87469f47-f2b2-410e-9ac5-58c6363a19f3"
    private let yobi = "e781aff1-defa-4367-a184-437d093ca87e"
    private let stranger = "c4ca4238-a0b9-4382-8dcc-509a6f75849b"

    override func setUpWithError() throws {
        tmpDir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("vd-rh-coord-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
        store = try QueueStore(url: tmpDir.appendingPathComponent("queue.sqlite"))
        RightHands.publish(nil)
    }

    override func tearDownWithError() throws {
        store = nil
        RightHands.publish(nil)
        try? FileManager.default.removeItem(at: tmpDir)
    }

    // MARK: - Doubles

    struct FixedSummary: SummaryProvider {
        let name = "fixed"
        let isConfigured = true
        func brief(for request: SummaryRequest) async throws -> SessionBrief {
            SessionBrief(topic: "the turn", happened: "a model summary",
                         recap: "A model summary of the turn.")
        }
    }

    final class SilentSpeech: SpeechProvider, @unchecked Sendable {
        let name = "silent"
        let isConfigured = true
        var isSpeaking = false
        var spoken: [String] = []
        func speak(_ text: SanitizedSpokenText, onWord: (@Sendable (Range<Int>) -> Void)?) async throws {
            spoken.append(text.text)
        }
        func stop() {}
    }

    struct FakeAgents: ClaudeAgentsReading {
        let live: [LiveSession]
        func sessions() -> [LiveSession]? { live }
    }

    struct StubOwnership: SessionOwnershipStore {
        let records: [SessionOwnershipRecord]
        func record(_ r: SessionOwnershipRecord) {}
        func current(sessionId: String) -> SessionOwnershipRecord? { records.first { $0.sessionId == sessionId } }
        func remove(sessionId: String) {}
        func all() -> [SessionOwnershipRecord] { records }
        func rekey(from oldSessionId: String, to newSessionId: String, expectedPid: Int) -> SessionOwnershipRecord? { nil }
    }

    private func live(_ id: String, cwd: String) -> LiveSession {
        LiveSession(pid: Int(ProcessInfo.processInfo.processIdentifier), sessionId: id, cwd: cwd,
                    status: "idle", name: "p", waitingFor: nil)
    }

    private func coordinator(roster: RightHands.Roster?, speech: SilentSpeech = SilentSpeech(),
                             ownership: [SessionOwnershipRecord] = []) -> Coordinator {
        Coordinator(
            store: store,
            summarizer: SummarizerChain(providers: [FixedSummary()]),
            speech: SpeechChain(preferred: speech, fallback: speech),
            gate: InterruptGate(minimumIdleSeconds: 0, signals: .quiescent),
            agents: FakeAgents(live: [live(director, cwd: "/tmp/Director"),
                                      live(yobi, cwd: "/tmp/Yobi1-OS"),
                                      live(stranger, cwd: "/tmp/elsewhere")]),
            ownership: StubOwnership(records: ownership),
            rightHands: { roster })
    }

    private func append(session: String, cwd: String, at ms: Int64, message: String = "done") throws {
        _ = try store.insert(event: QueuedEvent(
            createdAtMs: ms, hookEvent: .stop, sessionId: session,
            promptId: UUID().uuidString, cwd: cwd, lastAssistantMessage: message, tty: "ttys001"))
    }

    // MARK: - The ear

    /// No roster: everyone, newest first, exactly as before.
    func testWithoutARosterTheStrangerIsNext() throws {
        try append(session: director, cwd: "/tmp/Director", at: 1_000)
        try append(session: stranger, cwd: "/tmp/elsewhere", at: 2_000)
        let c = coordinator(roster: nil)
        XCTAssertEqual(try c.nextToAnnounce()?.sessionId, stranger)
        XCTAssertEqual(try c.waitingCount(), 2)
        XCTAssertEqual(try c.attended().count, 2)
    }

    /// With one: the stranger is still WAITING (the grid's band, `tbase
    /// status`), but never next, never counted, never replayed.
    func testWithARosterOnlyHandsReachTheEar() throws {
        try append(session: director, cwd: "/tmp/Director", at: 1_000)
        try append(session: stranger, cwd: "/tmp/elsewhere", at: 2_000)
        try append(session: yobi, cwd: "/tmp/Yobi1-OS", at: 3_000)
        let roster = RightHands.Roster(hands: [
            RightHands.Hand(name: "Director", cwd: "/tmp/Director"),
            RightHands.Hand(name: "Yobi1", tmux: "y1-cc"),
        ])
        let c = coordinator(roster: roster, ownership: [
            SessionOwnershipRecord(sessionId: yobi, harness: "claude-code", pid: 1, sessionName: "y1-cc"),
        ])
        XCTAssertEqual(try c.waiting().map(\.sessionId), [yobi, stranger, director],
                       "waiting() is what the agents claim, and stays whole")
        XCTAssertEqual(try c.attended().map(\.sessionId), [yobi, director])
        XCTAssertEqual(try c.nextToAnnounce()?.sessionId, yobi)
        XCTAssertEqual(try c.waitingCount(), 2, "the badge is a hail, and is cut like the ear")
        // The walk over an all-heard stack stays inside the roster too.
        try store.advanceCursor(sessionId: yobi, heardThrough: try XCTUnwrap(c.attended().first).latestId)
        XCTAssertEqual(try c.nextToAnnounce()?.sessionId, director)
        XCTAssertEqual(try c.nextToReplay(after: director)?.sessionId, yobi,
                       "the replay wraps within the hands, never onto the stranger")
    }

    /// An empty roster is a roster: the user named nobody, and the ear is
    /// quiet. This is distinct from no file, which is everyone.
    func testAnEmptyRosterSilencesTheEar() throws {
        try append(session: director, cwd: "/tmp/Director", at: 1_000)
        let c = coordinator(roster: RightHands.Roster(hands: []))
        XCTAssertNil(try c.nextToAnnounce())
        XCTAssertEqual(try c.waitingCount(), 0)
        XCTAssertEqual(try c.waiting().count, 1)
    }

    // MARK: - The director's card

    private func rollupRoster() throws -> RightHands.Roster {
        let path = tmpDir.appendingPathComponent("rollup.json")
        try """
        {"projects": [
          {"name": "Yobi1 design", "state": "needs you", "line": "Your call on the nav."},
          {"name": "Firebase", "state": "moving", "line": "Migration running."}]}
        """.write(to: path, atomically: true, encoding: .utf8)
        return RightHands.Roster(hands: [
            RightHands.Hand(name: "Director", cwd: "/tmp/Director", rollup: path.path),
        ])
    }

    /// A pick on the director's card speaks the projects, not the turn, and
    /// no model is asked.
    func testTheDirectorsCardIsTheRollup() async throws {
        try append(session: director, cwd: "/tmp/Director", at: 1_000, message: "I coordinated seventeen things.")
        let speech = SilentSpeech()
        let c = coordinator(roster: try rollupRoster(), speech: speech)
        RightHands.publish(RightHands.Resolved(ids: [director], names: [director: "Director"]))
        let outcome = try await c.announceNext(only: director, ignoringGate: true)
        guard case .spoke(let announcement) = outcome else { return XCTFail("\(outcome)") }
        XCTAssertEqual(announcement.brief.topic, "Director")
        XCTAssertEqual(announcement.brief.happened, "2 projects: 1 needs you, 1 moving.")
        XCTAssertEqual(announcement.brief.question, "Yobi1 design: Your call on the nav.")
        XCTAssertEqual(speech.spoken.count, 1)
        XCTAssertTrue(speech.spoken[0].contains("Yobi1 design needs you"), speech.spoken[0])
        XCTAssertFalse(speech.spoken[0].contains("seventeen"), "the turn is not the card")
        // The brief is persisted like any other, so the hub and `tbase brief` carry it.
        let stored = try XCTUnwrap(store.storedBrief(sessionId: director, eventRowid: announcement.event.latestId))
        XCTAssertEqual(stored.provider, "rollup")
        XCTAssertEqual(try ManagerJSON.brief(store: store, sessionId: director)?.recap, announcement.brief.recap)
    }

    /// The rollup moves between Stops. The card reads it NOW: a second pick
    /// after the file changed speaks the new projects, with no new turn.
    func testTheCardReadsTheRollupAsItStands() async throws {
        try append(session: director, cwd: "/tmp/Director", at: 1_000)
        let speech = SilentSpeech()
        let roster = try rollupRoster()
        let c = coordinator(roster: roster, speech: speech)
        RightHands.publish(RightHands.Resolved(ids: [director], names: [director: "Director"]))
        _ = try await c.announceNext(only: director, ignoringGate: true)
        try #"{"projects": [{"name": "Firebase", "state": "ready", "line": "Migration is green."}]}"#
            .write(toFile: roster.hands[0].rollup!, atomically: true, encoding: .utf8)
        _ = try await c.announceNext(only: director, ignoringGate: true)
        XCTAssertEqual(speech.spoken.count, 2)
        XCTAssertTrue(speech.spoken[1].contains("Firebase is ready"), speech.spoken[1])
        XCTAssertFalse(speech.spoken[1].contains("Yobi1"), speech.spoken[1])
    }

    /// A hand without a rollup, and a rollup that will not read, both get
    /// the ordinary summary: the card never goes silent over a file.
    func testAnUnreadableRollupFallsBackToTheTurn() async throws {
        try append(session: director, cwd: "/tmp/Director", at: 1_000)
        let speech = SilentSpeech()
        let roster = RightHands.Roster(hands: [
            RightHands.Hand(name: "Director", cwd: "/tmp/Director",
                            rollup: tmpDir.appendingPathComponent("absent.json").path),
        ])
        let c = coordinator(roster: roster, speech: speech)
        let outcome = try await c.announceNext(only: director, ignoringGate: true)
        guard case .spoke(let announcement) = outcome else { return XCTFail("\(outcome)") }
        XCTAssertEqual(announcement.brief.happened, "a model summary")
    }

    /// `prepareNext` does not prepare a rollup — there is nothing to take off
    /// the critical path — and with `summarizeOthers` it prepares the next
    /// stranger too, for a director that reads every brief.
    func testPrepareNextSkipsTheRollupAndCanPrepareTheOthers() async throws {
        try append(session: director, cwd: "/tmp/Director", at: 2_000)
        try append(session: stranger, cwd: "/tmp/elsewhere", at: 1_000)
        var roster = try rollupRoster()
        let c = coordinator(roster: roster)
        try await c.prepareNext()
        let directorTurn = try XCTUnwrap(store.latestStop(for: director))
        let directorPrepared = await c.prepared.has(director, latest: directorTurn.latestId)
        XCTAssertFalse(directorPrepared)
        let strangerTurn = try XCTUnwrap(store.latestStop(for: stranger))
        let strangerPrepared = await c.prepared.has(stranger, latest: strangerTurn.latestId)
        XCTAssertFalse(strangerPrepared, "off by default: a summary is a model call per Stop")

        roster.summarizeOthers = true
        let d = coordinator(roster: roster)
        try await d.prepareNext()
        let strangerPreparedNow = await d.prepared.has(stranger, latest: strangerTurn.latestId)
        XCTAssertTrue(strangerPreparedNow)
        XCTAssertNil(try d.nextToAnnounce().flatMap { $0.sessionId == stranger ? $0 : nil },
                     "prepared for `tbase brief`, never for the ear")
    }
}
