import CryptoKit
import Foundation

/// A human terminal is an additional view onto existing agents. A grouped
/// session provides its own current window; active-pane isolates its cursor;
/// ignore-size keeps it out of the original windows' size calculation.
/// Group membership changes, but panes/processes/layouts are not recreated.
enum TmuxTerminalView {
    struct Source: Equatable, Sendable {
        let socketPath: String
        let sessionID: String
        let sessionName: String
        let windowID: String
        let paneID: String
        let tty: String
        let pid: Int
        let directory: String
        let zoomed: Bool
        let active: Bool

        var fingerprint: String {
            SHA256.hash(data: Data([socketPath, sessionID, windowID, paneID, tty, String(pid)]
                .joined(separator: "\0").utf8)).map { String(format: "%02x", $0) }.joined()
        }
    }

    struct Client: Equatable, Sendable {
        let pid: Int
        let tty: String
        let created: String
        let sessionID: String
        let windowID: String
        let paneID: String
        let flags: Set<String>
    }

    struct View: Equatable, Sendable {
        let sessionID: String
        let name: String
    }

    static let sourceFormat = ["socket_path", "session_id", "session_name", "window_id", "pane_id",
        "pane_tty", "pane_pid", "pane_current_path", "window_zoomed_flag", "pane_active", "pane_dead"]
        .map { "#{\($0)}" }.joined(separator: "\t")
    static let clientFormat = ["client_pid", "client_tty", "client_created", "session_id", "window_id",
        "pane_id", "client_flags"].map { "#{\($0)}" }.joined(separator: "\t")

    static func validID(_ value: String, prefix: Character) -> Bool {
        value.first == prefix && !value.dropFirst().isEmpty && value.dropFirst().allSatisfy(\.isNumber)
    }

    static func parseSource(_ output: String, expected: TmuxPaneAddress) -> Source? {
        let f = output.split(separator: "\t", omittingEmptySubsequences: false).map(String.init)
        guard f.count == 11, let socket = TmuxFleet.canonicalSocket(f[0]),
              validID(f[1], prefix: "$"), validID(f[3], prefix: "@"), validID(f[4], prefix: "%"),
              !f[2].isEmpty, !f[2].contains("\n"), f[5].hasPrefix("/dev/"),
              let pid = Int(f[6]), pid > 0, f[7].hasPrefix("/"),
              ["0", "1"].contains(f[8]), ["0", "1"].contains(f[9]), f[10] == "0",
              expected.paneId.isEmpty || expected.paneId == f[4],
              expected.sessionName.isEmpty || expected.sessionName == f[2],
              expected.paneTty.isEmpty || expected.paneTty == f[5],
              expected.socketPath == nil || TmuxFleet.canonicalSocket(expected.socketPath!) == socket else { return nil }
        return Source(socketPath: socket, sessionID: f[1], sessionName: f[2], windowID: f[3],
                      paneID: f[4], tty: f[5], pid: pid, directory: f[7], zoomed: f[8] == "1", active: f[9] == "1")
    }

    static func parseClients(_ output: String) -> [Client]? {
        var clients: [Client] = []
        for line in output.split(separator: "\n") {
            let f = line.split(separator: "\t", omittingEmptySubsequences: false).map(String.init)
            guard f.count == 7, let pid = Int(f[0]), pid > 0, f[1].hasPrefix("/dev/"),
                  Double(f[2]) != nil, validID(f[3], prefix: "$"), validID(f[4], prefix: "@"),
                  validID(f[5], prefix: "%") else { return nil }
            clients.append(Client(pid: pid, tty: f[1], created: f[2], sessionID: f[3], windowID: f[4],
                                  paneID: f[5], flags: Set(f[6].split(separator: ",").map(String.init))))
        }
        return clients
    }

    static func parseReceipt(_ output: String) -> (pid: Int, tty: String)? {
        let f = output.trimmingCharacters(in: .whitespacesAndNewlines).split(separator: "\t", omittingEmptySubsequences: false)
        guard f.count == 2, let pid = Int(f[0]), pid > 0, f[1].hasPrefix("/dev/"),
              !f[1].contains("\n"), f[1].count < 256 else { return nil }
        return (pid, String(f[1]))
    }

    // list-clients pane_id is window.active, NOT a client active-pane read.
    // Exact initial selection is acknowledged by our attaching command queue.
    // Reuse additionally requires a one-pane window, removing that ambiguity.
    static func matches(_ client: Client, source: Source, view: View, pid: Int, tty: String,
                        created: String? = nil) -> Bool {
        client.pid == pid && client.tty == tty && (created == nil || client.created == created)
            && client.sessionID == view.sessionID && client.windowID == source.windowID
            && client.flags.isSuperset(of: ["active-pane", "ignore-size"])
    }

    static func read(_ args: [String], source: Source, timeout: TimeInterval = 2) -> Result<String, ScriptError> {
        Tmux.run(["-N"] + args, socketPath: source.socketPath, timeout: timeout)
    }

    static func resolve(_ pane: TmuxPaneAddress) -> Source? {
        let target = pane.paneId.isEmpty ? "=" + pane.sessionName
            : pane.sessionName.isEmpty ? pane.paneId : "=" + pane.sessionName + ":." + pane.paneId
        guard case .success(let text) = Tmux.run(["-N", "display-message", "-p", "-t", target, sourceFormat],
            socket: pane.socketName, socketPath: pane.socketPath, timeout: 2) else { return nil }
        return parseSource(text, expected: pane)
    }

    /// The source session's selection, and the shared target window's layout,
    /// zoom and active pane. No titles, screen contents, or environment reads.
    static func originalState(_ source: Source) -> String? {
        guard case .success(let selection) = read(["display-message", "-p", "-t", source.sessionID,
            "#{window_id}\t#{pane_id}"], source: source),
              case .success(let window) = read(["display-message", "-p", "-t", source.windowID,
            "#{window_layout}\t#{window_zoomed_flag}\t#{pane_id}"], source: source) else { return nil }
        let a = selection.split(separator: "\t", omittingEmptySubsequences: false).map(String.init)
        let b = window.split(separator: "\t", omittingEmptySubsequences: false).map(String.init)
        guard a.count == 2, validID(a[0], prefix: "@"), validID(a[1], prefix: "%"),
              b.count == 3, !b[0].isEmpty, ["0", "1"].contains(b[1]), validID(b[2], prefix: "%") else { return nil }
        return selection + "\n" + window
    }

    static func clients(_ source: Source) -> [Client]? {
        guard case .success(let result) = read(["list-clients", "-F", clientFormat], source: source) else { return nil }
        return parseClients(result)
    }

    static func hasSizingAnchor(_ clients: [Client], sourceSessionID: String) -> Bool {
        // Require a client that actually contains the original windows. An
        // unrelated session is not evidence about their sizing calculation.
        clients.contains { $0.sessionID == sourceSessionID
            && $0.flags.isDisjoint(with: ["ignore-size", "control-mode", "suspended", "dead"]) }
    }

    static func preservesSizeOnAttach(_ source: Source, clients: [Client]) -> Bool {
        guard case .success(let policies) = read(["list-windows", "-t", source.sessionID,
            "-F", "#{window-size}\t#{aggressive-resize}"], source: source) else { return false }
        return sizingPoliciesAllowAttach(policies, sourceSessionID: source.sessionID, clients: clients)
    }

    static func sizingPoliciesAllowAttach(_ policies: String, sourceSessionID: String, clients: [Client]) -> Bool {
        let rows = policies.split(separator: "\n")
        let anchored = hasSizingAnchor(clients, sourceSessionID: sourceSessionID)
        return !rows.isEmpty && rows.allSatisfy { row in
            let fields = row.split(separator: "\t", omittingEmptySubsequences: false)
            guard fields.count == 2, ["manual", "smallest", "largest", "latest"].contains(String(fields[0])),
                  ["0", "1", "off", "on"].contains(String(fields[1])) else { return false }
            // Manual windows ignore client sizing. For automatic windows,
            // aggressive-resize could exclude the source client's other
            // windows, so every automatic window must have it disabled.
            return fields[0] == "manual" || (anchored && ["0", "off"].contains(String(fields[1])))
        }
    }

    static func ensureView(_ source: Source) -> View? {
        // Reuse only our marked, detached view. An attached view without an
        // in-memory terminal receipt may belong to another still-open tab.
        guard case .success(let listing) = read(["list-sessions", "-F",
            "#{session_id}\t#{session_name}\t#{@tb-view-source}\t#{session_attached}"], source: source) else { return nil }
        for line in listing.split(separator: "\n").sorted() {
            let f = line.split(separator: "\t", omittingEmptySubsequences: false).map(String.init)
            if f.count == 4, validID(f[0], prefix: "$"), f[1].hasPrefix("tb-view-"),
               f[2] == source.fingerprint, f[3] == "0" {
                let view = View(sessionID: f[0], name: f[1])
                if verifyView(view, source: source) { return view }
            }
        }
        let name = "tb-view-" + String(source.fingerprint.prefix(12)) + "-" + UUID().uuidString.prefix(8).lowercased()
        // -N prevents an absent/restarted socket from spawning a new server
        // and executing its configuration. The original session is ID-exact.
        guard case .success(let result) = read(["new-session", "-d", "-s", name, "-t", source.sessionID,
            "-P", "-F", "#{session_id}\t#{session_name}", ";", "set-option", "-t", name,
            "@tb-view-source", source.fingerprint], source: source) else { return nil }
        let f = result.split(separator: "\t", omittingEmptySubsequences: false).map(String.init)
        guard f.count == 2, validID(f[0], prefix: "$"), f[1] == name else { return nil }
        let view = View(sessionID: f[0], name: name)
        return verifyView(view, source: source) ? view : nil
    }

    static func verifyView(_ view: View, source: Source) -> Bool {
        guard view.sessionID != source.sessionID, view.name.hasPrefix("tb-view-"),
              case .success(let marker) = read(["show-options", "-qv", "-t", view.sessionID, "@tb-view-source"], source: source),
              marker == source.fingerprint,
              case .success(let pane) = read(["display-message", "-p", "-t", view.sessionID + ":." + source.paneID,
                "#{window_id}\t#{pane_id}\t#{pane_tty}\t#{pane_pid}\t#{pane_dead}"], source: source) else { return false }
        return pane == [source.windowID, source.paneID, source.tty, String(source.pid), "0"].joined(separator: "\t")
    }

    /// select-pane MUST run in the attaching client's command queue, after
    /// active-pane is set. An external select-pane or switch-client would
    /// change the shared window's active pane even when the client has flags.
    /// -Z preserves zoom. A hidden pane in a zoomed window is refused earlier.
    static func attachArguments(source: Source, view: View) -> [String] {
        ["-N", "-S", source.socketPath, "attach-session", "-E", "-f", "ignore-size,active-pane",
         "-t", view.sessionID, ";", "select-window", "-t", view.sessionID + ":" + source.windowID,
         ";", "select-pane", "-Z", "-t", view.sessionID + ":." + source.paneID]
    }

    static func hasSinglePane(_ source: Source) -> Bool {
        guard case .success(let count) = read(["display-message", "-p", "-t", source.windowID,
            "#{window_panes}"], source: source) else { return false }
        return count == "1"
    }

    static func selectionCompleted(receiptPath: String, source: Source, view: View) -> Bool {
        guard let text = try? String(contentsOfFile: receiptPath + ".selected", encoding: .utf8) else { return false }
        return text == [view.sessionID, source.windowID, source.paneID].joined(separator: "\t")
    }

    static func command(binary: String, source: Source, view: View, receiptPath: String) -> String {
        let completed = [view.sessionID, source.windowID, source.paneID].joined(separator: "\t")
        let acknowledge = "umask 077; printf '%s' " + SessionLauncher.shellQuoted(completed)
            + " > " + SessionLauncher.shellQuoted(receiptPath + ".selected")
        // A failed command stops the remaining command sequence, so this file
        // means the client completed our selection commands. Its content is an
        // accepted-target receipt, not a fictitious per-client pane format.
        let argv = ["/usr/bin/env", "-u", "TMUX", "-u", "TMUX_TMPDIR", binary]
            + attachArguments(source: source, view: view) + [";", "run-shell", acknowledge]
        let program = "set -eu\numask 077\nprintf '%s\\t%s\\n' \"$$\" \"$(/usr/bin/tty)\" > "
            + SessionLauncher.shellQuoted(receiptPath) + "\nexec " + argv.map(SessionLauncher.shellQuoted).joined(separator: " ")
        return "/bin/sh -c " + SessionLauncher.shellQuoted(program)
    }
}
