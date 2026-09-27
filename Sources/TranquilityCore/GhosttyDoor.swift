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
        ["-na", "Ghostty", "--args", "-e", "tmux"] + socketFlag(socket) + ["attach", "-t", session]
    }

    /// A socket by name (`-L`) or, when it is a path, by path (`-S`).
    static func socketFlag(_ socket: String) -> [String] {
        socket.hasPrefix("/") ? ["-S", socket] : ["-L", socket]
    }

    /// Prod Tranquility's private tmux server, which holds the sessions it started (the Director session among
    /// them, 26 Sep: Go to Agent and "open it" could not reach any of them).
    public static var prodSocket: String {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/VoiceDispatch/tmux/tmux-\(getuid())/tb").path
    }

    /// The sockets a session may live on, in the order they are tried: this
    /// app's own, Director's fleet, tmux's default server, then Prod's.
    public static var candidateSockets: [String] { [Tmux.socketName, "fleet", "default", prodSocket] }

    /// The socket that holds a tmux session by this name, or nil. `hasSession`
    /// is the seam; production asks tmux.
    public static func socket(for session: String, candidates: [String] = candidateSockets,
                              hasSession: (String, String) -> Bool = GhosttyDoor.tmuxHasSession) -> String? {
        candidates.first { hasSession($0, session) }
    }

    public static func tmuxHasSession(socket: String, session: String) -> Bool {
        guard let tmux = ["/opt/homebrew/bin/tmux", "/usr/local/bin/tmux", "/usr/bin/tmux"]
            .first(where: { FileManager.default.isExecutableFile(atPath: $0) }) else { return false }
        if case .success = Subprocess.run(tmux, socketFlag(socket) + ["has-session", "-t", "=" + session], timeout: 3) {
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
        guard let name = tmuxSession(forSessionId: sessionId) else {
            return isInstalled ? .notInTmux : .notInstalled
        }
        return open(tmuxSession: name)
    }

    /// The same, by tmux session name (a pending action names its agent, 25 Sep).
    public static func open(tmuxSession name: String) -> Outcome {
        guard isInstalled else { return .notInstalled }
        guard let socket = socket(for: name) else { return .notInTmux }
        switch Subprocess.run("/usr/bin/open", openArguments(socket: socket, session: name), timeout: 10) {
        case .success: return .opened(socket: socket, session: name)
        case .failure(let error): return .failed(error.message)
        }
    }
}
