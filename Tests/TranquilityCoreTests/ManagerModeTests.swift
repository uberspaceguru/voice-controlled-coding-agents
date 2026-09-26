import Foundation
import XCTest
@testable import TranquilityCore

final class ManagerModeTests: XCTestCase {
    func testParsesAGateVerdictLine() throws {
        let line = #"{"event":"addressed","t":1789.5,"p":0.98,"intent":"invite_next","ms":155,"text":"Tranquility, invite the next agent"}"#
        let e = try XCTUnwrap(ManagerEvent.parse(Data(line.utf8)))
        XCTAssertEqual(e.event, .addressed)
        XCTAssertEqual(e.p, 0.98)
        XCTAssertEqual(e.intent, "invite_next")
    }

    func testParsesAStageLineAndIgnoresUnknownFields() throws {
        let line = #"{"event":"stage","session":"abc","goal":"ship the CRM","project":"outreach","extra":1}"#
        let e = try XCTUnwrap(ManagerEvent.parse(Data(line.utf8)))
        XCTAssertEqual(e.event, .stage)
        XCTAssertEqual(e.goal, "ship the CRM")
    }

    /// "Yobi1, what's on today?" handed to the app (25 Sep): the hand's
    /// session, its words and its name, for the card to answer on.
    func testAnAskForARightHandParses() throws {
        let line = #"{"event":"ask","session":"e781aff1-defa","name":"Yobi1","text":"what's on today?"}"#
        let e = try XCTUnwrap(ManagerEvent.parse(Data(line.utf8)))
        XCTAssertEqual(e.event, .ask)
        XCTAssertEqual(e.session, "e781aff1-defa")
        XCTAssertEqual(e.name, "Yobi1")
        XCTAssertEqual(e.text, "what's on today?")
        XCTAssertEqual(ManagerConfig.environment(base: [:])["TB_RIGHT_HAND_CARDS"], "1",
                       "the manager only hands a turn to a host that says it can speak on the card")
    }

    /// The manager asked the hand; the app speaks its answer on the hand's card.
    func testAnAnswerForARightHandsCardParses() throws {
        let line = #"{"event":"answer","session":"ac03daf5","name":"Director","text":"Ahmed, nine things need you."}"#
        let e = try XCTUnwrap(ManagerEvent.parse(Data(line.utf8)))
        XCTAssertEqual(e.event, .answer)
        XCTAssertEqual(e.text, "Ahmed, nine things need you.")
        XCTAssertNil(ManagerConfig.environment(base: [:])["TB_DEFAULT_INTERLOCUTOR"],
                     "only Tranquility Base Director talks to Director by default")
    }

    func testReadyParses() throws {
        XCTAssertEqual(try XCTUnwrap(ManagerEvent.parse(Data(#"{"event":"ready"}"#.utf8))).event, .ready)
    }

    func testQuietParses() throws {
        XCTAssertEqual(try XCTUnwrap(ManagerEvent.parse(Data(#"{"event":"quiet"}"#.utf8))).event, .quiet)
    }

    func testHearingAndErrorParse() throws {
        XCTAssertEqual(try XCTUnwrap(ManagerEvent.parse(Data(#"{"event":"hearing"}"#.utf8))).event, .hearing)
        let e = try XCTUnwrap(ManagerEvent.parse(Data(#"{"event":"error","reason":"tbase missing"}"#.utf8)))
        XCTAssertEqual(e.reason, "tbase missing")
    }

    func testAnUnknownEventKindIsNotAnEvent() {
        XCTAssertNil(ManagerEvent.parse(Data(#"{"event":"dance"}"#.utf8)))
        XCTAssertNil(ManagerEvent.parse(Data("not json".utf8)))
    }

    func testCommandDefaultsBesideTheCheckoutAndHonoursConfig() throws {
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("hq-\(UUID().uuidString).json")
        XCTAssertTrue(ManagerConfig.command(config: tmp).first?.hasSuffix("tb-voice/server/run.sh") ?? false)
        try #"{"manager":{"command":["/usr/local/bin/tb-voice","--quiet"]}}"#.write(to: tmp, atomically: true, encoding: .utf8)
        XCTAssertEqual(ManagerConfig.command(config: tmp), ["/usr/local/bin/tb-voice", "--quiet"])
    }

    func testEnvironmentMarksTheHostAndExtendsPath() {
        let env = ManagerConfig.environment(base: ["PATH": "/x"])
        XCTAssertEqual(env["TB_HOST"], "app")
        XCTAssertTrue(env["PATH"]?.hasSuffix(":/x") ?? false)
        XCTAssertTrue(env["PATH"]?.contains("/.local/bin") ?? false)
    }

    /// The child's links reach the lane that started it (23 Sep): the
    /// scheme is handed over, and it beats one inherited from the user's
    /// shell, which is how a `tbdev` link once went looking for a Dev app
    /// that was not running while Prod was.
    func testEnvironmentHandsTheChildThisLanesScheme() {
        let env = ManagerConfig.environment(base: ["TB_URL_SCHEME": "tbdev"], scheme: "tranquilitybase")
        XCTAssertEqual(env["TB_URL_SCHEME"], "tranquilitybase")
        XCTAssertEqual(ManagerConfig.environment(base: [:], scheme: "tbdev")["TB_URL_SCHEME"], "tbdev")
    }

    func testThePreferredSchemeIsTheLanesOwn() {
        let dev = ["tranquilitybase", "voicedispatch", "tbdev"]
        XCTAssertEqual(AppIdentity.preferredScheme(among: dev, channel: .development), "tbdev")
        XCTAssertEqual(AppIdentity.preferredScheme(among: dev, channel: .production), "tranquilitybase",
                       "a Prod build that happens to claim tbdev still writes its own name")
        XCTAssertEqual(AppIdentity.preferredScheme(among: ["tranquilitybase", "voicedispatch"], channel: .development),
                       "tranquilitybase", "a Dev bundle without tbdev falls back to the shared scheme")
        XCTAssertEqual(AppIdentity.preferredScheme(among: [], channel: .test), "tranquilitybase")
    }
}

/// The local child's audio through this app's WebRTC engine: this app's own
/// setting, off unless asked for, and never read by an app without its own
/// folder (Prod reads hq.json, and its bot must not change).
final class LocalManagerAudioTests: XCTestCase {
    private func settings(_ json: String?) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("manager-\(UUID().uuidString).json")
        if let json { try Data(json.utf8).write(to: url) }
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }

    func testOffUnlessAskedFor() throws {
        XCTAssertEqual(ManagerConfig.localAudio(settings: try settings(nil), ownFolder: true), .child)
        XCTAssertEqual(ManagerConfig.localAudio(settings: try settings("{}"), ownFolder: true), .child)
        XCTAssertEqual(ManagerConfig.localAudio(settings: try settings(#"{"audio":"loud"}"#), ownFolder: true), .child)
        XCTAssertEqual(ManagerConfig.localAudio(settings: try settings("not json"), ownFolder: true), .child)
    }

    func testOnWhenAskedForInAnAppWithItsOwnFolder() throws {
        XCTAssertEqual(ManagerConfig.localAudio(settings: try settings(#"{"audio":"webrtc"}"#), ownFolder: true), .webrtc)
        XCTAssertEqual(ManagerConfig.localAudio(settings: try settings(#"{"audio":"WebRTC"}"#), ownFolder: true), .webrtc)
    }

    func testAnAppWithoutItsOwnFolderNeverReadsIt() throws {
        XCTAssertEqual(ManagerConfig.localAudio(settings: try settings(#"{"audio":"webrtc"}"#), ownFolder: false), .child)
    }

    func testTheChildIsToldWhereAndHow() {
        let env = ManagerConfig.localWebRTCEnvironment(port: 50123, token: "tok")
        XCTAssertEqual(env, ["TB_AUDIO": "webrtc", "TB_WEBRTC_PORT": "50123", "TB_WEBRTC_TOKEN": "tok"])
        XCTAssertNil(ManagerConfig.environment(base: [:])["TB_AUDIO"], "the default child keeps its own audio")
    }

    func testAFreeLoopbackPort() throws {
        let port = try XCTUnwrap(ManagerConfig.freeLoopbackPort())
        XCTAssertTrue((1024..<65536).contains(port))
    }
}
