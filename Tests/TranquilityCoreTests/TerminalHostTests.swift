import Foundation
import XCTest
@testable import TranquilityCore

final class TerminalHostTests: XCTestCase {
    func testAutomaticUsesAlreadyRunningScriptableGhostty() {
        XCTAssertEqual(TerminalHost.resolve(.automatic, ghosttyRunning: true, ghosttyScriptable: true), .ghostty)
        XCTAssertEqual(TerminalHost.resolve(.automatic, ghosttyRunning: false, ghosttyScriptable: true), .terminal)
        XCTAssertEqual(TerminalHost.resolve(.automatic, ghosttyRunning: true, ghosttyScriptable: false), .terminal)
    }

    func testExplicitChoiceIsNotSilentlyReplaced() {
        XCTAssertEqual(TerminalHost.resolve(.ghostty, ghosttyRunning: false, ghosttyScriptable: false), .ghostty)
        XCTAssertEqual(TerminalHost.resolve(.terminal, ghosttyRunning: true, ghosttyScriptable: true), .terminal)
    }

    func testPreferenceRoundTripsInIsolatedFileAndInvalidValueDefaultsWithoutRewriting() throws {
        let prior = TerminalHost.fileURL
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("terminal-host-test-" + UUID().uuidString)
        defer { TerminalHost.fileURL = prior; try? FileManager.default.removeItem(at: root) }
        TerminalHost.fileURL = root.appendingPathComponent("terminal-host.json")
        XCTAssertEqual(TerminalHost.preference, .automatic)
        XCTAssertFalse(FileManager.default.fileExists(atPath: TerminalHost.fileURL.path))
        try TerminalHost.save(.ghostty)
        XCTAssertEqual(TerminalHost.preference, .ghostty)
        let invalid = Data(#"{"terminal":"unknown"}"#.utf8)
        try invalid.write(to: TerminalHost.fileURL)
        XCTAssertEqual(TerminalHost.preference, .automatic)
        XCTAssertEqual(try Data(contentsOf: TerminalHost.fileURL), invalid)
    }

    func testGhosttyCreatesNewSurfaceAndUsesItsReturnedIdentity() {
        let script = TerminalHost.openScript(host: .ghostty, command: "/bin/sh -c 'exec tmux'", directory: "/fixture/project")
        XCTAssertTrue(script.contains("new tab in front window with configuration cfg"))
        XCTAssertTrue(script.contains("new window with configuration cfg"))
        XCTAssertTrue(script.contains("id of createdTerminal"))
        XCTAssertTrue(script.contains("focus createdTerminal"))
        XCTAssertFalse(script.contains("input text"))
        XCTAssertFalse(script.contains("send key"))
        XCTAssertFalse(script.contains("com.apple.Terminal"))
        XCTAssertFalse(script.contains("split "))
    }

    func testTerminalFallbackAlsoCreatesAndMeasuresANewSurface() {
        let script = TerminalHost.openScript(host: .terminal, command: "safe command", directory: "/fixture")
        XCTAssertTrue(script.contains("set createdTab to do script"))
        XCTAssertTrue(script.contains("first window whose selected tab is createdTab"))
        XCTAssertFalse(script.contains("window 1"))
        XCTAssertFalse(script.contains("do script \"safe command\" in"))
    }

    func testAppleScriptLiteralsPreserveQuotesBackslashesAndNewlines() {
        XCTAssertEqual(TerminalHost.literal("a\"b\\c\nd\te"), "\"a\\\"b\\\\c\\nd\\te\"")
        let script = TerminalHost.openScript(host: .ghostty,
            command: "echo \"closed\"\nnot an AppleScript command", directory: "/fixture/\"quoted\"")
        XCTAssertTrue(script.contains("set command of cfg to \"echo \\\"closed\\\"\\nnot an AppleScript command\""))
    }

    func testGhosttyFocusUsesValidatedSurfaceUUIDWithoutTitleOrCWDHeuristics() throws {
        let id = "11111111-1111-4111-8111-111111111111"
        let script = try XCTUnwrap(TerminalHost.focusScript(host: .ghostty, surfaceID: id))
        XCTAssertTrue(script.contains("focus terminal id \"\(id)\""))
        XCTAssertTrue(script.contains("is not running then return \"notfound\""))
        XCTAssertFalse(script.contains("whose name"))
        XCTAssertFalse(script.contains("working directory"))
        XCTAssertNil(TerminalHost.focusScript(host: .ghostty, surfaceID: "window 1"))
        XCTAssertNil(TerminalHost.focusScript(host: .ghostty, surfaceID: "\"\nclose window 1"))
    }

    func testOpeningRequiresValidReturnedIdentityNotJustAnOKPrefix() {
        let id = "11111111-1111-4111-8111-111111111111"
        XCTAssertEqual(TerminalHost.surfaceID(from: "ok|\(id)\n", host: .ghostty), id)
        XCTAssertEqual(TerminalHost.surfaceID(from: "ok|42", host: .terminal), "42")
        for bad in ["ok", "ok|", "ok|wrong", "ok|42|extra", "failed|42"] {
            XCTAssertNil(TerminalHost.surfaceID(from: bad, host: .ghostty))
        }
    }

    func testPermissionFailureNamesSelectedHostAndDoesNotPromiseFocus() {
        let error = ScriptError(message: "Not authorized to send Apple events (-1743)")
        XCTAssertEqual(TerminalTabFocus.hostOutcome(.failure(error), host: .ghostty, timeout: 5),
                       .failed("Allow Tranquility Base to control Ghostty in Privacy & Security → Automation, then try again."))
        XCTAssertEqual(TerminalTabFocus.hostOutcome(.success(""), host: .ghostty, timeout: 5),
                       .failed("The terminal did not confirm the requested action."))
        XCTAssertEqual(TerminalTabFocus.hostOutcome(.success("notfound"), host: .ghostty, timeout: 5), .tabGone)
    }
    func testCodexAdoptionCannotPromoteUnownedOrAmbiguousTTYIntoOwnership() {
        let external = SessionOwnershipRecord(sessionId: "conversation", harness: "codex", pid: 501,
            paneId: "%1", sessionName: "work", paneTty: "/dev/ttys001", socketPath: "/fixture/socket", origin: .external)
        XCTAssertEqual(SessionLauncher.verifiedObservedCodexPane(holderPIDs: [501], observed: external), external.pane)
        XCTAssertNil(SessionLauncher.verifiedObservedCodexPane(holderPIDs: [501, 502], observed: external))
        XCTAssertNil(SessionLauncher.verifiedObservedCodexPane(holderPIDs: [502], observed: external))
        XCTAssertNil(SessionLauncher.verifiedObservedCodexPane(holderPIDs: [501], observed: nil))
        var unproven = external
        unproven.origin = nil
        XCTAssertNil(SessionLauncher.verifiedObservedCodexPane(holderPIDs: [501], observed: unproven))
    }

}
