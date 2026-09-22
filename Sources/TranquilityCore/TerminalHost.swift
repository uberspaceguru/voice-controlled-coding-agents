import AppKit
import Foundation

/// The terminal application is presentation, not agent ownership. Changing
/// this preference never moves, resumes, or terminates a coding agent.
public enum TerminalHost {
    public enum Choice: String, Codable, CaseIterable, Sendable {
        case automatic, ghostty, terminal

        public var label: String {
            switch self {
            case .automatic: return "Automatic"
            case .ghostty: return "Ghostty"
            case .terminal: return "Terminal"
            }
        }

        public var bundleIdentifier: String {
            self == .ghostty ? "com.mitchellh.ghostty" : "com.apple.Terminal"
        }
    }

    nonisolated(unsafe) public static var fileURL = QueueStore.supportDirectory
        .appendingPathComponent("terminal-host.json")

    private struct Stored: Codable { var terminal: Choice }

    public static var preference: Choice {
        get {
            guard let data = try? Data(contentsOf: fileURL),
                  let value = try? JSONDecoder().decode(Stored.self, from: data) else { return .automatic }
            return value.terminal
        }
        set { try? save(newValue) }
    }

    public static func save(_ choice: Choice) throws {
        try FileManager.default.createDirectory(at: fileURL.deletingLastPathComponent(),
                                               withIntermediateDirectories: true)
        try JSONEncoder().encode(Stored(terminal: choice)).write(to: fileURL, options: .atomic)
    }

    /// Read-only capability checks. They neither launch an app nor send an
    /// Apple event. An explicit Ghostty choice never silently opens Terminal.
    static func ghosttySupportsScripting() -> Bool {
        guard let app = NSWorkspace.shared.urlForApplication(withBundleIdentifier: Choice.ghostty.bundleIdentifier)
        else { return false }
        return FileManager.default.fileExists(atPath: app
            .appendingPathComponent("Contents/Resources/Ghostty.sdef").path)
    }

    public static func resolvedChoice() -> Choice {
        resolve(preference, ghosttyRunning: !NSRunningApplication.runningApplications(
            withBundleIdentifier: Choice.ghostty.bundleIdentifier).isEmpty,
            ghosttyScriptable: ghosttySupportsScripting())
    }

    static func resolve(_ preference: Choice, ghosttyRunning: Bool, ghosttyScriptable: Bool) -> Choice {
        guard preference == .automatic else { return preference }
        return ghosttyRunning && ghosttyScriptable ? .ghostty : .terminal
    }

    public static var automationBundleIdentifier: String { resolvedChoice().bundleIdentifier }

    static func literal(_ value: String) -> String {
        "\"" + value.replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
            .replacingOccurrences(of: "\r", with: "\\r")
            .replacingOccurrences(of: "\n", with: "\\n")
            .replacingOccurrences(of: "\t", with: "\\t") + "\""
    }

    /// Launch only in a newly created surface. No paste/key events, front-tab
    /// guesses, or user shell configuration changes are involved.
    static func openScript(host: Choice, command: String, directory: String) -> String {
        if host == .ghostty {
            return """
            tell application id "com.mitchellh.ghostty"
              set cfg to new surface configuration
              set command of cfg to \(literal(command))
              set initial working directory of cfg to \(literal(directory))
              set wait after command of cfg to false
              if (count of windows) > 0 then
                set createdTab to new tab in front window with configuration cfg
                set createdTerminal to focused terminal of createdTab
              else
                set createdWindow to new window with configuration cfg
                set createdTerminal to focused terminal of selected tab of createdWindow
              end if
              set createdID to id of createdTerminal
              focus createdTerminal
              return "ok|" & createdID
            end tell
            """
        }
        return """
        tell application id "com.apple.Terminal"
          set createdTab to do script \(literal(command))
          set createdWindow to first window whose selected tab is createdTab
          set createdID to id of createdWindow
          activate
          return "ok|" & createdID
        end tell
        """
    }

    /// Call only after tmux proves the retained client receipt still shows
    /// the requested pane. Existence/title/cwd alone are not that proof.
    static func focusScript(host: Choice, surfaceID: String) -> String? {
        if host == .ghostty {
            guard UUID(uuidString: surfaceID) != nil else { return nil }
            return """
            if application id "com.mitchellh.ghostty" is not running then return "notfound"
            tell application id "com.mitchellh.ghostty"
              if not (exists terminal id \(literal(surfaceID))) then return "notfound"
              focus terminal id \(literal(surfaceID))
              return "ok"
            end tell
            """
        }
        guard let id = Int(surfaceID), id > 0 else { return nil }
        return """
        if application id "com.apple.Terminal" is not running then return "notfound"
        tell application id "com.apple.Terminal"
          if not (exists window id \(id)) then return "notfound"
          if (count of tabs of window id \(id)) is 0 then return "notfound"
          set index of window id \(id) to 1
          activate
          return "ok"
        end tell
        """
    }

    static func surfaceID(from result: String, host: Choice) -> String? {
        let fields = result.trimmingCharacters(in: .whitespacesAndNewlines).split(separator: "|", omittingEmptySubsequences: false)
        guard fields.count == 2, fields[0] == "ok" else { return nil }
        let id = String(fields[1])
        return focusScript(host: host, surfaceID: id) == nil ? nil : id
    }

    static func failureMessage(_ error: ScriptError, host: Choice) -> String {
        if error.message.contains("-1743") || error.message.contains("Not authorized to send Apple events") {
            return "Allow Tranquility Base to control \(host.label) in Privacy & Security → Automation, then try again."
        }
        if host == .ghostty && (error.message.contains("-1708") || error.message.contains("-10827")) {
            return "Ghostty scripting is unavailable. Ghostty 1.3 or newer with macos-applescript enabled is required."
        }
        return error.message
    }
}
