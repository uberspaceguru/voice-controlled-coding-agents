import Foundation
import XCTest
@testable import TranquilityCore

final class TmuxTerminalViewTests: XCTestCase {
    private func source(socket: String = "/fixture/server socket", pid: Int = 101) -> TmuxTerminalView.Source {
        .init(socketPath: socket, sessionID: "$1", sessionName: "original", windowID: "@2", paneID: "%3",
              tty: "/dev/ttys001", pid: pid, directory: "/fixture/project", zoomed: false, active: false)
    }
    private let view = TmuxTerminalView.View(sessionID: "$4", name: "tb-view-fixture")

    func testSourceRequiresExactSocketPaneTTYAndLivingLeader() throws {
        let expected = TmuxPaneAddress(socketName: nil, paneId: "%3", sessionName: "original", paneTty: "/dev/ttys001",
            socketPath: "/fixture/server socket", isExternal: true)
        let valid = "/fixture/server socket\t$1\toriginal\t@2\t%3\t/dev/ttys001\t101\t/fixture/project\t0\t0\t0"
        XCTAssertEqual(TmuxTerminalView.parseSource(valid, expected: expected), source())
        for bad in [valid.replacingOccurrences(of: "%3", with: "%8"),
                    valid.replacingOccurrences(of: "original", with: "stranger"),
                    valid.replacingOccurrences(of: "ttys001", with: "ttys002"),
                    valid.replacingOccurrences(of: "/fixture/server socket", with: "/fixture/other server"),
                    String(valid.dropLast()) + "1"] {
            XCTAssertNil(TmuxTerminalView.parseSource(bad, expected: expected))
        }
    }

    func testSourceFingerprintSeparatesServerAndRecycledProcessIdentities() {
        XCTAssertNotEqual(source().fingerprint, source(socket: "/fixture/other").fingerprint)
        XCTAssertNotEqual(source().fingerprint, source(pid: 202).fingerprint)
        XCTAssertEqual(source().fingerprint, source().fingerprint)
    }

    func testAttachmentUsesExactSocketAndIsolatedViewWithoutDetachmentOrResize() {
        let args = TmuxTerminalView.attachArguments(source: source(), view: view)
        XCTAssertEqual(Array(args.prefix(3)), ["-N", "-S", "/fixture/server socket"])
        XCTAssertTrue(args.contains("-E"))
        XCTAssertTrue(args.contains("ignore-size,active-pane"))
        XCTAssertFalse(args.contains("-d"))
        XCTAssertFalse(args.contains("resize-window"))
        XCTAssertFalse(args.contains("switch-client"))
        XCTAssertFalse(args.contains("kill-session"))
        XCTAssertTrue(args.contains("$4"))
        XCTAssertFalse(args.contains("$1"))
        XCTAssertEqual(Array(args.suffix(5)), [";", "select-pane", "-Z", "-t", "$4:.%3"])
        XCTAssertTrue(args.contains("$4:@2"))
    }

    func testTerminalLaunchReceiptUsesSamePIDAfterExecAndClearsNestedTmuxEnvironment() {
        let command = TmuxTerminalView.command(binary: "/fixture/tmux binary", source: source(), view: view,
                                               receiptPath: "/fixture/private/client's receipt")
        XCTAssertTrue(command.hasPrefix("/bin/sh -c "))
        XCTAssertTrue(command.contains("set -eu"))
        XCTAssertTrue(command.contains("umask 077"))
        XCTAssertTrue(command.contains("$$"))
        XCTAssertTrue(command.contains("/usr/bin/tty"))
        XCTAssertTrue(command.contains("TMUX_TMPDIR"))
        XCTAssertTrue(command.contains("exec "))
        XCTAssertFalse(command.contains("resize-window"))
        XCTAssertFalse(command.contains("attach -d"))
    }

    func testReceiptParserRejectsMissingPIDInvalidTTYAndExtraPayload() {
        let good = TmuxTerminalView.parseReceipt("501\t/dev/ttys002\n")
        XCTAssertEqual(good?.pid, 501)
        XCTAssertEqual(good?.tty, "/dev/ttys002")
        for value in ["501", "-1\t/dev/ttys002", "501\tnot a tty", "501\t/dev/ttys002\textra", "501\t/dev/tty\nother"] {
            XCTAssertNil(TmuxTerminalView.parseReceipt(value))
        }
    }

    func testClientRequiresReceiptIdentityViewWindowAndFlagsButPaneFieldIsGlobal() throws {
        let good = "501\t/dev/ttys002\t123.5\t$4\t@2\t%3\tUTF-8,active-pane,ignore-size"
        let client = try XCTUnwrap(TmuxTerminalView.parseClients(good)?.first)
        XCTAssertTrue(TmuxTerminalView.matches(client, source: source(), view: view,
                                               pid: 501, tty: "/dev/ttys002", created: "123.5"))
        XCTAssertFalse(TmuxTerminalView.matches(client, source: source(), view: view,
                                                pid: 999, tty: "/dev/ttys002"))
        XCTAssertFalse(TmuxTerminalView.matches(client, source: source(), view: view,
                                                pid: 501, tty: "/dev/ttys003"))
        XCTAssertFalse(TmuxTerminalView.matches(client, source: source(), view: view,
                                                pid: 501, tty: "/dev/ttys002", created: "124"))
        let globalOther = try XCTUnwrap(TmuxTerminalView.parseClients(good.replacingOccurrences(of: "%3", with: "%9"))?.first)
        XCTAssertTrue(TmuxTerminalView.matches(globalOther, source: source(), view: view, pid: 501, tty: "/dev/ttys002"))
        for wrong in [good.replacingOccurrences(of: "$4", with: "$1"),
                      good.replacingOccurrences(of: "@2", with: "@8"),
                      good.replacingOccurrences(of: "active-pane,", with: ""),
                      good.replacingOccurrences(of: ",ignore-size", with: "")] {
            let altered = try XCTUnwrap(TmuxTerminalView.parseClients(wrong)?.first)
            XCTAssertFalse(TmuxTerminalView.matches(altered, source: source(), view: view, pid: 501, tty: "/dev/ttys002"))
        }
    }

    func testClientReadFailureDoesNotBecomeAnEmptyInventory() {
        XCTAssertEqual(TmuxTerminalView.parseClients(""), [])
        XCTAssertNil(TmuxTerminalView.parseClients("malformed"))
        XCTAssertNil(TmuxTerminalView.parseClients("501\t/dev/ttys001\tnot time\t$1\t@1\t%1\tactive-pane"))
    }

    func testIgnoreSizeRequiresAnExistingEligibleClient() throws {
        let ordinary = try XCTUnwrap(TmuxTerminalView.parseClients("501\t/dev/ttys002\t123\t$4\t@2\t%3\tUTF-8")?.first)
        XCTAssertTrue(TmuxTerminalView.hasSizingAnchor([ordinary], sourceSessionID: "$4"))
        XCTAssertFalse(TmuxTerminalView.hasSizingAnchor([ordinary], sourceSessionID: "$1"))
        XCTAssertFalse(TmuxTerminalView.hasSizingAnchor([], sourceSessionID: "$4"))
        for flag in ["ignore-size", "control-mode", "suspended", "dead"] {
            let client = TmuxTerminalView.Client(pid: ordinary.pid, tty: ordinary.tty, created: ordinary.created,
                sessionID: ordinary.sessionID, windowID: ordinary.windowID, paneID: ordinary.paneID, flags: [flag])
            XCTAssertFalse(TmuxTerminalView.hasSizingAnchor([client], sourceSessionID: "$4"))
        }
    }

    func testUnrelatedSessionClientDoesNotProtectAutomaticWindows() throws {
        let client = try XCTUnwrap(TmuxTerminalView.parseClients("501\t/dev/ttys002\t123\t$4\t@2\t%3\tUTF-8")?.first)
        XCTAssertFalse(TmuxTerminalView.sizingPoliciesAllowAttach("latest\t0", sourceSessionID: "$1", clients: [client]))
        XCTAssertTrue(TmuxTerminalView.sizingPoliciesAllowAttach("latest\t0\nsmallest\toff", sourceSessionID: "$4", clients: [client]))
        XCTAssertFalse(TmuxTerminalView.sizingPoliciesAllowAttach("latest\t0", sourceSessionID: "$4", clients: []))
    }

    func testAggressiveResizeOnAnyAutomaticWindowRefusesEvenSameSessionAnchor() throws {
        let client = try XCTUnwrap(TmuxTerminalView.parseClients("501\t/dev/ttys002\t123\t$4\t@2\t%3\tUTF-8")?.first)
        for policies in ["latest\t1", "largest\ton", "manual\t0\nsmallest\t1", "latest\t0\nlargest\t1"] {
            XCTAssertFalse(TmuxTerminalView.sizingPoliciesAllowAttach(policies, sourceSessionID: "$4", clients: [client]))
        }
        XCTAssertTrue(TmuxTerminalView.sizingPoliciesAllowAttach("manual\t1\nmanual\ton", sourceSessionID: "$4", clients: []))
        for malformed in ["", "latest", "latest\tunknown", "unknown\t0", "manual\t0\textra"] {
            XCTAssertFalse(TmuxTerminalView.sizingPoliciesAllowAttach(malformed, sourceSessionID: "$4", clients: [client]))
        }
    }

    func testSurfaceCacheUsesPhysicalEndpointAndHostInsteadOfSessionName() throws {
        TerminalWindows.forgetAll(); defer { TerminalWindows.forgetAll() }
        let client = try XCTUnwrap(TmuxTerminalView.parseClients("501\t/dev/ttys002\t123\t$4\t@2\t%3\tactive-pane,ignore-size")?.first)
        let attachment = TerminalWindows.Attachment(host: .ghostty,
            surfaceID: "11111111-1111-4111-8111-111111111111", view: view, client: client)
        TerminalWindows.remember(attachment, for: source().fingerprint)
        XCTAssertEqual(TerminalWindows.attachment(for: source().fingerprint), attachment)
        XCTAssertNil(TerminalWindows.attachment(for: source(socket: "/fixture/other").fingerprint))
        XCTAssertNil(TerminalWindows.attachment(for: source(pid: 999).fingerprint))
        TerminalWindows.forget(endpoint: source().fingerprint)
        XCTAssertNil(TerminalWindows.attachment(for: source().fingerprint))
    }
}
