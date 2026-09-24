import XCTest
@testable import TranquilityCore

/// The right-hands roster: the file, its resolution against what is running,
/// the pinned names, and the director's rollup card. Every case is a rule the
/// 23 Sep brief stated: no file means no change; a hand is matched by id,
/// directory or tmux name; the name in the file is the name everywhere; the
/// rollup is the card.
final class RightHandsTests: XCTestCase {
    private var tmpDir: URL!

    private let director = "87469f47-f2b2-410e-9ac5-58c6363a19f3"
    private let yobi = "e781aff1-defa-4367-a184-437d093ca87e"
    private let s3po = "2b973845-36c8-4c7d-8d31-18b6bc13821c"
    private let stranger = "c4ca4238-a0b9-4382-8dcc-509a6f75849b"

    override func setUpWithError() throws {
        tmpDir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("vd-right-hands-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
        RightHands.publish(nil)
    }

    override func tearDownWithError() throws {
        RightHands.publish(nil)
        RightHands.trace = nil
        try? FileManager.default.removeItem(at: tmpDir)
    }

    private func write(_ json: String, name: String = "right-hands.json") throws -> URL {
        let url = tmpDir.appendingPathComponent(name)
        try json.write(to: url, atomically: true, encoding: .utf8)
        return url
    }

    private func record(_ id: String, tmux: String?, cwd: String? = nil) -> SessionOwnershipRecord {
        SessionOwnershipRecord(sessionId: id, harness: "claude-code", pid: 1,
                               sessionName: tmux, cwd: cwd)
    }

    // MARK: - The file

    /// No file is the whole migration story: the panel as it always was.
    func testNoFileIsNoRoster() {
        XCTAssertNil(RightHands.load(from: tmpDir.appendingPathComponent("absent.json")))
    }

    func testTheObjectShapeParses() throws {
        let url = try write("""
        {"hands": [
          {"name": "Director", "session": "\(director)", "cwd": "~/Code/myAgents/Director",
           "tmux": "DirectorCC", "rollup": "~/.director/rollup.json"},
          {"name": "Yobi1", "tmux": "y1-cc"},
          {"name": "Sys-3PO", "session": "\(s3po)"}],
         "summarizeOthers": true}
        """)
        let roster = try XCTUnwrap(RightHands.load(from: url))
        XCTAssertEqual(roster.hands.count, 3)
        XCTAssertEqual(roster.hands[0].name, "Director")
        XCTAssertEqual(roster.hands[0].rollup, "~/.director/rollup.json")
        XCTAssertEqual(roster.hands[1].tmux, "y1-cc")
        XCTAssertTrue(roster.summarizeOthers)
    }

    /// A file written in a hurry: a bare array of ids and directories.
    func testABareArrayOfStringsParses() throws {
        let url = try write("""
        ["\(director)", "/Users/x/Code/Yobi1-OS", "2b973845"]
        """)
        let roster = try XCTUnwrap(RightHands.load(from: url))
        XCTAssertEqual(roster.hands.map(\.session), [director, nil, "2b973845"])
        XCTAssertEqual(roster.hands.map(\.cwd), [nil, "/Users/x/Code/Yobi1-OS", nil])
        XCTAssertFalse(roster.summarizeOthers)
    }

    /// Malformed fails OPEN, to everyone, and says so: an empty grid from a
    /// typo is a fleet hidden by a comma.
    func testAMalformedFileIsNoRosterAndIsSaidOutLoud() throws {
        final class Said: @unchecked Sendable { var lines: [String] = [] }
        let said = Said()
        RightHands.trace = { said.lines.append($0) }
        let url = try write(#"{"hands": "Director"}"#)
        XCTAssertNil(RightHands.load(from: url))
        XCTAssertEqual(said.lines.count, 1)
        XCTAssertTrue(said.lines[0].contains("unreadable"), said.lines[0])
        XCTAssertEqual(RightHands.parse(Data("not json".utf8)), .failure(.notJSON))
    }

    // MARK: - Matching

    func testAHandMatchesByIdPrefixDirectoryOrTmuxName() {
        let hand = RightHands.Hand(name: "Director", session: director,
                                   cwd: "/Users/x/Director/", tmux: "DirectorCC")
        XCTAssertTrue(hand.matches(sessionId: director, cwd: nil, tmuxSessionName: nil))
        XCTAssertTrue(RightHands.Hand(session: "87469f47").matches(sessionId: director, cwd: nil, tmuxSessionName: nil),
                      "an eight-character prefix names a session, as it does everywhere else")
        XCTAssertFalse(RightHands.Hand(session: "8746").matches(sessionId: director, cwd: nil, tmuxSessionName: nil),
                       "four characters is not a name")
        XCTAssertTrue(hand.matches(sessionId: stranger, cwd: "/Users/x/Director", tmuxSessionName: nil),
                      "a trailing slash is not a different directory")
        XCTAssertFalse(hand.matches(sessionId: stranger, cwd: "/Users/x/Director/sub", tmuxSessionName: nil),
                       "a directory is exact, never a prefix: sub-sessions run under the director")
        XCTAssertTrue(hand.matches(sessionId: stranger, cwd: nil, tmuxSessionName: "DirectorCC"))
        XCTAssertFalse(hand.matches(sessionId: stranger, cwd: nil, tmuxSessionName: "y1-cc"))
    }

    /// The tmux name survives a restart when the id does not: a hand keyed
    /// by tmux name resolves to whatever id ownership currently records.
    func testResolutionFollowsTheTmuxNameThroughOwnership() {
        let roster = RightHands.Roster(hands: [
            RightHands.Hand(name: "Yobi1", tmux: "y1-cc"),
            RightHands.Hand(name: "Sys-3PO", session: s3po),
        ])
        let resolved = roster.resolve(
            sessions: [(id: yobi, cwd: "/Users/x/Yobi1-OS"), (id: stranger, cwd: "/Users/x/Yobi1-OS")],
            ownership: [record(yobi, tmux: "y1-cc"), record(stranger, tmux: "y2-cc")])
        XCTAssertEqual(resolved.ids, [yobi, s3po])
        XCTAssertEqual(resolved.names[yobi], "Yobi1")
        XCTAssertEqual(resolved.names[s3po], "Sys-3PO",
                       "an explicit id is a hand whether or not it is running")
        XCTAssertFalse(resolved.contains(stranger), "y2-cc shares the directory and is not a hand")
    }

    /// Ownership records are a source of sessions too, so a hand nothing
    /// else lists still resolves.
    func testOwnershipAloneResolvesAHand() {
        let roster = RightHands.Roster(hands: [RightHands.Hand(name: "Director", cwd: "/Users/x/Director")])
        let resolved = roster.resolve(sessions: [], ownership: [record(director, tmux: "DirectorCC", cwd: "/Users/x/Director")])
        XCTAssertEqual(resolved.ids, [director])
    }

    // MARK: - Names

    func testThePinnedNameAnswersFromTheSnapshotThenTheFile() throws {
        let roster = RightHands.Roster(hands: [RightHands.Hand(name: "Director", session: director)])
        XCTAssertEqual(RightHands.pinnedName(for: director, roster: roster), "Director",
                       "an explicit id answers before any tick has published")
        XCTAssertNil(RightHands.pinnedName(for: stranger, roster: roster))
        RightHands.publish(RightHands.Resolved(ids: [yobi], names: [yobi: "Yobi1"]))
        XCTAssertEqual(RightHands.pinnedName(for: yobi, roster: nil), "Yobi1")
        XCTAssertNil(RightHands.pinnedName(for: stranger, roster: nil))
    }

    /// The name flows through the one chokepoint every surface uses.
    func testHarnessTitlePrefersThePinnedName() {
        GridAssembler.pinnedNames = { $0 == "abc" ? "Director" : nil }
        defer { GridAssembler.pinnedNames = { RightHands.pinnedName(for: $0) } }
        var live = LiveSession(pid: 1, sessionId: "abc")
        live.name = "Multi-agent tmux coordinator"
        XCTAssertEqual(GridAssembler.harnessTitle(sessionId: "abc", transcriptPath: nil, live: live), "Director")
        XCTAssertEqual(GridAssembler.tabDisplayName(discovered: "an old title", sessionId: "abc",
                                                    callsign: nil, cwd: "/x/y"), "Director")
        var other = LiveSession(pid: 2, sessionId: "def")
        other.name = "Synth Voice agent setup"
        XCTAssertEqual(GridAssembler.harnessTitle(sessionId: "def", transcriptPath: nil, live: other),
                       "Synth Voice agent setup", "no pin, no change")
    }

    func testRollupPathExpandsTheTilde() throws {
        let roster = RightHands.Roster(hands: [RightHands.Hand(name: "Director", session: director, rollup: "~/.director/rollup.json")])
        let path = try XCTUnwrap(RightHands.rollupPath(for: director, roster: roster))
        XCTAssertTrue(path.hasPrefix("/"), path)
        XCTAssertTrue(path.hasSuffix("/.director/rollup.json"), path)
        XCTAssertNil(RightHands.rollupPath(for: stranger, roster: roster))
    }

    // MARK: - The rollup

    private let rollupJSON = """
    {"updatedAt": "2026-09-23T19:40:00Z",
     "projects": [
       {"name": "GA fab", "state": "moving", "line": "Writing the eval harness"},
       {"name": "Yobi1 design", "state": "needs_you", "line": "Your call on the nav."},
       {"name": "Firebase", "state": "ready", "line": "Migration is green."},
       {"name": "Docs", "state": "blocked", "line": "Which template?"},
       {"name": "Six", "state": "moving", "line": ""},
       {"name": "Seven", "state": "moving", "line": "over the cap"},
       {"state": "moving", "line": "nameless, dropped"}]}
    """

    func testTheRollupSortsNeedsYouFirstAndCutsAtFive() throws {
        let rollup = try XCTUnwrap(RightHands.Rollup.parse(Data(rollupJSON.utf8)))
        XCTAssertEqual(rollup.projects.map(\.name), ["Yobi1 design", "Docs", "Firebase", "GA fab", "Six"])
        XCTAssertEqual(rollup.projects.map(\.state), [.needsYou, .needsYou, .ready, .moving, .moving])
        XCTAssertEqual(rollup.updatedAt, "2026-09-23T19:40:00Z")
        XCTAssertNil(RightHands.Rollup.parse(Data(#"{"nope": 1}"#.utf8)))
        XCTAssertEqual(RightHands.Rollup.parse(Data(#"{"projects": []}"#.utf8))?.projects.count, 0,
                       "an empty list is a rollup that says nothing to report")
    }

    func testTheRollupBriefIsTheCard() throws {
        let rollup = try XCTUnwrap(RightHands.Rollup.parse(Data(rollupJSON.utf8)))
        let brief = rollup.brief(topic: "Director")
        XCTAssertEqual(brief.topic, "Director")
        XCTAssertEqual(brief.happened, "5 projects: 2 need you, 1 ready, 2 moving.")
        XCTAssertEqual(brief.question, "Yobi1 design: Your call on the nav.")
        XCTAssertEqual(brief.nextStep, "Say Director, then a project name, to dive in.")
        let spoken = brief.spokenText()
        XCTAssertTrue(spoken.hasPrefix("Director. 5 projects: 2 need you"), spoken)
        XCTAssertTrue(spoken.contains("Yobi1 design needs you. Your call on the nav."), spoken)
        XCTAssertTrue(spoken.contains("Firebase is ready. Migration is green."), spoken)
        XCTAssertTrue(spoken.contains("GA fab is moving. Writing the eval harness."), spoken)
        XCTAssertTrue(spoken.hasSuffix("Six is moving."), spoken)
        XCTAssertFalse(spoken.contains("Seven"), "the sixth project is the list the user asked not to see")
    }

    func testAnEmptyRollupSaysSo() {
        let brief = RightHands.Rollup(projects: []).brief(topic: "Director")
        XCTAssertEqual(brief.spokenText(), "Director. No projects to report.")
        XCTAssertNil(brief.nextStep)
        XCTAssertNil(brief.question)
    }

    func testRollupLoadsFromDisk() throws {
        let url = try write(rollupJSON, name: "rollup.json")
        XCTAssertEqual(RightHands.Rollup.load(path: url.path)?.projects.count, 5)
        XCTAssertNil(RightHands.Rollup.load(path: tmpDir.appendingPathComponent("absent.json").path))
    }
}
