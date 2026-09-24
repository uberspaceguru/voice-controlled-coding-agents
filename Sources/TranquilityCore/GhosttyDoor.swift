import Foundation

/// Go to Agent opens Ghostty, never Terminal (ruled 24 Sep, Ahmed):
/// `open -na Ghostty --args -e tmux -L <socket> attach -t <session>`.
///
/// Also the reason Go to Agent no longer MOVES a session it finds in someone
/// else's tmux. Director runs its fleet on its own sockets (`fleet`, the
/// default server); the old path read such a pane as hand-started and moved
/// it into this app's socket, which ends the process and resumes it. An
/// attach is a window onto the pane where it already lives.
public enum GhosttyDoor {
    public static let appPath = "/Applications/Ghostty.app"

    public static var isInstalled: Bool {
        FileManager.default.fileExists(atPath: appPath)
    }

    /// The command, as argv for /usr/bin/open. Pure, for tests.
    public static func openArguments(socket: String, session: String) -> [String] {
        ["-na", "Ghostty", "--args", "-e", "tmux", "-L", socket, "attach", "-t", session]
    }

    /// The sockets a session may live on, in the order they are tried: this
    /// app's own, Director's fleet, then tmux's default server.
    public static let candidateSockets = [Tmux.socketName, "fleet", "default"]

    /// The socket that holds a tmux session by this name, or nil. `hasSession`
    /// is the seam; production asks tmux.
    public static func socket(for session: String, candidates: [String] = candidateSockets,
                              hasSession: (String, String) -> Bool = GhosttyDoor.tmuxHasSession) -> String? {
        candidates.first { hasSession($0, session) }
    }

    public static func tmuxHasSession(socket: String, session: String) -> Bool {
        guard let tmux = ["/opt/homebrew/bin/tmux", "/usr/local/bin/tmux", "/usr/bin/tmux"]
            .first(where: { FileManager.default.isExecutableFile(atPath: $0) }) else { return false }
        if case .success = Subprocess.run(tmux, ["-L", socket, "has-session", "-t", "=" + session], timeout: 3) {
            return true
        }
        return false
    }

    /// The tmux session a live Claude Code session runs in, from its own
    /// registry file (`session:@window.%pane`), then this app's ownership record.
    public static func tmuxSession(forSessionId id: String) -> String? {
        SessionRegistry.all().first(where: { $0.sessionId == id })?.tmuxSessionName
            ?? FileSessionOwnershipStore.shared.current(sessionId: id)?.sessionName
    }

    public enum Outcome: Equatable, Sendable {
        case opened(socket: String, session: String)
        case notInTmux
        case notInstalled
        case failed(String)
    }

    /// Open a Ghostty window attached to the session's pane. Blocking; call
    /// it off the main thread.
    public static func open(sessionId: String) -> Outcome {
        guard isInstalled else { return .notInstalled }
        guard let name = tmuxSession(forSessionId: sessionId),
              let socket = socket(for: name) else { return .notInTmux }
        switch Subprocess.run("/usr/bin/open", openArguments(socket: socket, session: name), timeout: 10) {
        case .success: return .opened(socket: socket, session: name)
        case .failure(let error): return .failed(error.message)
        }
    }
}
