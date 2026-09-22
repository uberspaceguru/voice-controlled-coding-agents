import Foundation
import XCTest
@testable import TranquilityCore

/// Explicitly opt in; creates only a private -S server with /dev/null config.
/// No Ghostty/Terminal Apple events and no discovery of user tmux sockets.
final class TmuxTerminalViewIntegrationTests: XCTestCase {
    func testDisposableViewPreservesOriginalSelectionLayoutZoomAndClients() throws {
        guard ProcessInfo.processInfo.environment["TB_RUN_TMUX_VIEW_INTEGRATION"] == "1" else {
            throw XCTSkip("Set TB_RUN_TMUX_VIEW_INTEGRATION=1 for disposable tmux/PTY integration")
        }
        let binary = try XCTUnwrap(Tmux.resolveBinary())
        let root = URL(fileURLWithPath: "/tmp/tbv-" + UUID().uuidString.prefix(12), isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false,
                                               attributes: [.posixPermissions: 0o700])
        let socket = root.appendingPathComponent("socket").path
        var environment = ProcessInfo.processInfo.environment
        environment.removeValue(forKey: "TMUX")
        environment.removeValue(forKey: "TMUX_TMPDIR")
        environment["TERM"] = "xterm-256color"
        var children: [Process] = []
        var inputs: [Pipe] = []
        defer {
            // This server was created solely by this test at this nonce path.
            _ = Subprocess.run(binary, ["-N", "-S", socket, "kill-server"], environment: environment, timeout: 2)
            for child in children where child.isRunning { child.terminate() }
            for input in inputs { try? input.fileHandleForWriting.close() }
            try? FileManager.default.removeItem(at: root)
        }
        func tmux(_ arguments: [String]) throws -> String {
            try Subprocess.run(binary, ["-S", socket, "-f", "/dev/null"] + arguments,
                               environment: environment, timeout: 3).get()
        }
        func startClient(_ command: String) throws {
            let process = Process(), input = Pipe()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/script")
            process.arguments = ["-q", "/dev/null", "/bin/sh", "-c", command]
            process.environment = environment
            process.standardInput = input
            process.standardOutput = FileHandle.nullDevice
            process.standardError = FileHandle.nullDevice
            try process.run()
            children.append(process); inputs.append(input)
        }
        func eventually(_ condition: () -> Bool) -> Bool {
            let deadline = ProcessInfo.processInfo.systemUptime + 4
            repeat {
                if condition() { return true }
                Thread.sleep(forTimeInterval: 0.05)
            } while ProcessInfo.processInfo.systemUptime < deadline
            return false
        }

        let session = try tmux(["new-session", "-d", "-s", "original", "-x", "100", "-y", "30",
                                "-P", "-F", "#{session_id}", "/bin/cat"])
        let firstPane = try tmux(["new-window", "-d", "-t", session, "-P", "-F", "#{pane_id}", "/bin/cat"])
        let target = try tmux(["split-window", "-d", "-t", firstPane, "-P", "-F", "#{pane_id}", "/bin/cat"])
        let pane = TmuxPaneAddress(socketName: nil, paneId: target, sessionName: "original", paneTty: "",
                                  socketPath: socket, isExternal: true)
        var source = try XCTUnwrap(TmuxTerminalView.resolve(pane))
        // A user-like client is already attached; focus must not detach it.
        let originalAttach = [binary, "-N", "-S", socket, "attach-session", "-E", "-t", session]
            .map(SessionLauncher.shellQuoted).joined(separator: " ")
        try startClient("/bin/stty cols 100 rows 31; exec " + originalAttach)
        XCTAssertTrue(eventually { (TmuxTerminalView.clients(source)?.count ?? 0) == 1 })
        let originalClient = try XCTUnwrap(TmuxTerminalView.clients(source)?.first)
        let originalState = try XCTUnwrap(TmuxTerminalView.originalState(source))
        let view = try XCTUnwrap(TmuxTerminalView.ensureView(source))
        XCTAssertEqual(TmuxTerminalView.originalState(source), originalState)
        XCTAssertEqual(TmuxTerminalView.ensureView(source), view, "An unused proven view is reused")

        let receipt = root.appendingPathComponent("client").path
        try startClient(TmuxTerminalView.command(binary: binary, source: source, view: view, receiptPath: receipt))
        XCTAssertTrue(eventually { TmuxTerminalView.selectionCompleted(receiptPath: receipt, source: source, view: view) })
        let text = try String(contentsOfFile: receipt, encoding: .utf8)
        let recorded = try XCTUnwrap(TmuxTerminalView.parseReceipt(text))
        XCTAssertTrue(eventually {
            TmuxTerminalView.clients(source)?.contains(where: {
                TmuxTerminalView.matches($0, source: source, view: view, pid: recorded.pid, tty: recorded.tty)
            }) == true
        })
        XCTAssertEqual(TmuxTerminalView.originalState(source), originalState)
        XCTAssertTrue(TmuxTerminalView.clients(source)?.contains(originalClient) == true)
        XCTAssertFalse(TmuxTerminalView.hasSinglePane(source), "Multipane client selection cannot be inferred from list-clients")

        // Zoom an original window only inside this disposable server. A view
        // onto its currently visible pane must preserve that zoom and layout.
        _ = try tmux(["select-pane", "-t", target])
        _ = try tmux(["resize-pane", "-Z", "-t", target])
        source = try XCTUnwrap(TmuxTerminalView.resolve(pane))
        XCTAssertTrue(source.zoomed)
        XCTAssertTrue(source.active)
        let zoomState = try XCTUnwrap(TmuxTerminalView.originalState(source))
        let zoomView = try XCTUnwrap(TmuxTerminalView.ensureView(source))
        let zoomReceipt = root.appendingPathComponent("zoom-client").path
        try startClient(TmuxTerminalView.command(binary: binary, source: source, view: zoomView, receiptPath: zoomReceipt))
        XCTAssertTrue(eventually { TmuxTerminalView.selectionCompleted(receiptPath: zoomReceipt, source: source, view: zoomView) })
        XCTAssertEqual(TmuxTerminalView.originalState(source), zoomState)
        XCTAssertTrue(TmuxTerminalView.clients(source)?.contains(originalClient) == true)
    }
}
