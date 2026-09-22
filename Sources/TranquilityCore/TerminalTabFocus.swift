import Foundation

/// Focus a verified tmux endpoint in Ghostty or Terminal. New attachments use
/// an additional grouped view; they do not detach clients or resize windows.
/// Reuse requires the actual terminal surface and a fresh tmux client receipt.
public enum TerminalTabFocus {
    public enum Outcome: Equatable, Sendable {
        case focused
        case tabGone
        case timedOut(seconds: Int)
        case failed(String)
    }

    static func outcome(
        of result: Result<String, ScriptError>, timeout: TimeInterval
    ) -> Outcome {
        switch result {
        case .success(let out) where out.contains("notfound"): return .tabGone
        case .success: return .focused
        case .failure(let e) where e.timedOut: return .timedOut(seconds: Int(timeout))
        case .failure(let e): return .failed(Self.plainWords(for: e.message))
        }
    }

    static func plainWords(for message: String) -> String {
        guard message.contains("-1743") || message.contains("Not authorized to send Apple events")
        else { return message }
        return "Tranquility Base isn't allowed to control Terminal, so it can't open the "
            + "agent's window. Grant it under Privacy & Security → Automation, then try again."
    }

    static let sessionNameCharset = CharacterSet(charactersIn:
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")

    static let humanAttachColumns = 120
    static let humanAttachRows = 40

    // Historical pure script fixture; runtime routes only through focusSync.
    static func attachScript(
        binary: String, socket: String?, tmuxTmpDir: String, sessionName: String
    ) -> String? {
        guard isSessionName(sessionName) else { return nil }
        func tmuxCommand(_ args: String) -> String {
            if let socket {
                return """
                    "env TMUX_TMPDIR=" & quoted form of "\(tmuxTmpDir)" \
                    & " " & quoted form of "\(binary)" & " -L " & quoted form of "\(socket)" \
                    & " \(args) -t " & quoted form of "\(sessionName)"
                    """
            }
            return """
                quoted form of "\(binary)" & " \(args) -t " & quoted form of "\(sessionName)"
                """
        }
        let attach = tmuxCommand("attach-session -E -f ignore-size,active-pane")
        return """
            tell application "Terminal"
              set idsBefore to id of windows
              activate
              set newTab to do script \(attach)
              set wid to ""
              try
                set wid to (id of (first window whose selected tab is newTab)) as text
              end try
              if wid is "" then
                try
                  repeat with w in (id of windows)
                    if (contents of w) is not in idsBefore then
                      set wid to (contents of w) as text
                      exit repeat
                    end if
                  end repeat
                end try
              end if
              return "ok|" & wid
            end tell
            """
    }

    // Historical pure fixture; production never matches a terminal title.
    static func raiseScript(windowId: Int, sessionName: String) -> String? {
        guard isSessionName(sessionName) else { return nil }
        return """
            tell application "Terminal"
              if (exists window id \(windowId)) then
                if (count of tabs of window id \(windowId)) > 0 then
                  if (name of window id \(windowId)) contains "\(sessionName)" then
                    set index of window id \(windowId) to 1
                    activate
                    return "ok"
                  end if
                  return "notfound|stranger"
                end if
              end if
              return "notfound"
            end tell
            """
    }

    static func isSessionName(_ name: String) -> Bool {
        !name.isEmpty && name.count <= 64
            && name.unicodeScalars.allSatisfy { sessionNameCharset.contains($0) }
    }

    static func windowId(fromAttach result: String) -> Int? {
        guard let bar = result.firstIndex(of: "|") else { return nil }
        return Int(result[result.index(after: bar)...]
            .trimmingCharacters(in: .whitespacesAndNewlines))
    }

    @discardableResult
    public static func attachFresh(pane: TmuxPaneAddress,
                                   timeout: TimeInterval = 5) async -> Outcome {
        await focus(pane: pane, timeout: timeout)
    }

    @discardableResult
    public static func attachFreshSync(pane: TmuxPaneAddress) -> Outcome {
        focusSync(pane: pane, timeout: 5)
    }

    public static func focus(tmuxSession name: String, timeout: TimeInterval = 5) async -> Outcome {
        let pane = TmuxPaneAddress(socketName: Tmux.socketName, paneId: "", sessionName: name, paneTty: "")
        return await focus(pane: pane, timeout: timeout)
    }


    public static func focus(tty: String, sessionId: String? = nil,
                             timeout: TimeInterval = 5) async -> Outcome {
        await Task.detached(priority: .userInitiated) {
            let pane: TmuxPaneAddress?
            if let sessionId {
                pane = TmuxOwnership.pane(forSessionId: sessionId, pid: nil)
            } else {
                pane = TmuxOwnership.pane(forTty: tty)
            }
            guard let pane else {
                return Outcome.failed("The agent's exact tmux pane could not be verified, so no terminal was opened.")
            }
            return focusSync(pane: pane, timeout: timeout)
        }.value
    }

    public static func focus(pane: TmuxPaneAddress, sessionId: String? = nil,
                             timeout: TimeInterval = 5) async -> Outcome {
        await Task.detached(priority: .userInitiated) { focusSync(pane: pane, timeout: timeout) }.value
    }

    private static let focusLock = NSLock()

    static func focusSync(pane: TmuxPaneAddress, timeout: TimeInterval) -> Outcome {
        focusLock.lock(); defer { focusLock.unlock() }
        let host = TerminalHost.resolvedChoice()
        if host == .ghostty && !TerminalHost.ghosttySupportsScripting() {
            return .failed("Ghostty 1.3 or newer with AppleScript support is required. The terminal preference was not changed.")
        }
        guard let binary = Tmux.resolveBinary(), let source = TmuxTerminalView.resolve(pane) else {
            return .failed("That exact tmux pane could not be verified. Nothing was moved or restarted.")
        }
        guard !source.zoomed || source.active else {
            return .failed("That pane is hidden by a zoomed tmux window. Unzoom it before opening an additional view.")
        }
        guard let clients = TmuxTerminalView.clients(source) else {
            return .failed("The tmux clients could not be verified. No terminal was opened.")
        }
        if let known = TerminalWindows.attachment(for: source.fingerprint), known.host == host,
           TmuxTerminalView.verifyView(known.view, source: source),
           TmuxTerminalView.hasSinglePane(source),
           clients.contains(where: { TmuxTerminalView.matches($0, source: source, view: known.view,
                pid: known.client.pid, tty: known.client.tty, created: known.client.created) }),
           let script = TerminalHost.focusScript(host: host, surfaceID: known.surfaceID) {
            let result = Subprocess.run("/usr/bin/osascript", ["-e", script], timeout: timeout)
            let raised = hostOutcome(result, host: host, timeout: timeout)
            if raised != .tabGone { return raised }
        }
        TerminalWindows.forget(endpoint: source.fingerprint)

        guard TmuxTerminalView.preservesSizeOnAttach(source, clients: clients) else {
            return .failed("An additional view could resize this session's windows. It requires manual window sizing, or an existing client in the original session with aggressive-resize disabled.")
        }
        guard let original = TmuxTerminalView.originalState(source),
              let view = TmuxTerminalView.ensureView(source),
              TmuxTerminalView.originalState(source) == original else {
            return .failed("An additional tmux view could not be verified without changing the original view.")
        }
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("tb-terminal-" + UUID().uuidString,
                                                                                   isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: false,
                                                   attributes: [.posixPermissions: 0o700])
        } catch { return .failed("Could not create a private terminal receipt.") }
        defer { try? FileManager.default.removeItem(at: folder) }
        let receipt = folder.appendingPathComponent("client")
        let command = TmuxTerminalView.command(binary: binary, source: source, view: view, receiptPath: receipt.path)
        let script = TerminalHost.openScript(host: host, command: command, directory: source.directory)
        let result = Subprocess.run("/usr/bin/osascript", ["-e", script], timeout: timeout)
        guard case .success(let output) = result else { return hostOutcome(result, host: host, timeout: timeout) }
        guard let surfaceID = TerminalHost.surfaceID(from: output, host: host) else {
            return .failed("The terminal opened without a verifiable surface identity.")
        }

        let deadline = ProcessInfo.processInfo.systemUptime + min(3, max(0.1, timeout))
        repeat {
            if let data = try? Data(contentsOf: receipt), data.count < 1024,
               let text = String(data: data, encoding: .utf8), let recorded = TmuxTerminalView.parseReceipt(text),
               TmuxTerminalView.selectionCompleted(receiptPath: receipt.path, source: source, view: view),
               let current = TmuxTerminalView.clients(source),
               let client = current.first(where: { TmuxTerminalView.matches($0, source: source, view: view,
                    pid: recorded.pid, tty: recorded.tty) }) {
                guard TmuxTerminalView.originalState(source) == original else {
                    return .failed("The terminal opened, but the original tmux state changed during verification.")
                }
                TerminalWindows.remember(.init(host: host, surfaceID: surfaceID, view: view, client: client),
                                         for: source.fingerprint)
                if host == .terminal, let id = Int(surfaceID) {
                    TerminalWindows.remember(sessionName: source.sessionName, windowId: id)
                }
                return .focused
            }
            Thread.sleep(forTimeInterval: 0.05)
        } while ProcessInfo.processInfo.systemUptime < deadline
        return .failed("The terminal opened, but its tmux attachment could not be verified. No agent was restarted.")
    }

    static func hostOutcome(_ result: Result<String, ScriptError>, host: TerminalHost.Choice,
                            timeout: TimeInterval) -> Outcome {
        switch result {
        case .success(let value) where value == "notfound": return .tabGone
        case .success(let value) where value == "ok" || TerminalHost.surfaceID(from: value, host: host) != nil:
            return .focused
        case .success: return .failed("The terminal did not confirm the requested action.")
        case .failure(let error) where error.timedOut: return .timedOut(seconds: Int(timeout))
        case .failure(let error): return .failed(TerminalHost.failureMessage(error, host: host))
        }
    }
}
