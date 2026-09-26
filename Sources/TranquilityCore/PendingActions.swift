import Foundation

/// Director's pending actions, on the Director card (25 Sep night, Ahmed).
///
/// Every voice request that becomes an action (an approval waiting on him, a
/// dispatch, a restart, a lookup, anything longer than a few seconds) is one
/// row in Director's store, `pending_actions`, listed by `director --json
/// actions`. Long work lives here, never in voice: the voice acknowledges once
/// and the card carries the facts, one row per action with its state, an
/// Approve button when it waits on him, and Go to Agent.
public enum PendingActions {
    public struct Action: Equatable, Sendable {
        public enum State: String, Sendable {
            case awaitingAhmed = "awaiting_ahmed", queued, inProgress = "in_progress", done, failed
        }
        public var id: Int
        public var what: String
        public var state: State
        /// The agent it is about, as Director names it (a tmux session: "w-a21").
        public var target: String?
        public var itemId: String?
        public var requestedAt: Date?
        public var startedAt: Date?
        public var finishedAt: Date?

        public init(id: Int, what: String, state: State, target: String? = nil, itemId: String? = nil,
                    requestedAt: Date? = nil, startedAt: Date? = nil, finishedAt: Date? = nil) {
            self.id = id; self.what = what; self.state = state; self.target = target; self.itemId = itemId
            self.requestedAt = requestedAt; self.startedAt = startedAt; self.finishedAt = finishedAt
        }

        public var isOpen: Bool { state == .awaitingAhmed || state == .queued || state == .inProgress }
    }

    /// `director --json actions`: a list of rows, or {"actions": [...]}. Rows
    /// with no id, no words or an unknown state are skipped. Nil when it is not
    /// JSON at all.
    public static func parse(_ data: Data) -> [Action]? {
        guard let object = try? JSONSerialization.jsonObject(with: data) else { return nil }
        let rows: [[String: Any]]
        if let list = object as? [[String: Any]] { rows = list }
        else if let dict = object as? [String: Any], let list = dict["actions"] as? [[String: Any]] { rows = list }
        else { return nil }
        func date(_ v: Any?) -> Date? {
            guard let ms = (v as? NSNumber)?.doubleValue, ms > 0 else { return nil }
            return Date(timeIntervalSince1970: ms / 1000)
        }
        func text(_ v: Any?) -> String? {
            guard let s = (v as? String)?.trimmingCharacters(in: .whitespacesAndNewlines), !s.isEmpty else { return nil }
            return s
        }
        return rows.compactMap { r in
            guard let id = (r["id"] as? NSNumber)?.intValue, let what = text(r["what"]),
                  let state = (r["state"] as? String).flatMap(Action.State.init(rawValue:)) else { return nil }
            return Action(id: id, what: what, state: state, target: text(r["target"]), itemId: text(r["item_id"]),
                          requestedAt: date(r["requested_at"]), startedAt: date(r["started_at"]),
                          finishedAt: date(r["finished_at"]))
        }
    }

    /// The state in plain words, with its time.
    public static func chip(_ a: Action, clock: (Date) -> String = hhmm) -> String {
        switch a.state {
        case .awaitingAhmed: return "awaiting your approval"
        case .queued: return "queued"
        case .inProgress: return a.startedAt.map { "in progress since \(clock($0))" } ?? "in progress"
        case .done: return a.finishedAt.map { "done at \(clock($0))" } ?? "done"
        case .failed: return a.finishedAt.map { "failed at \(clock($0))" } ?? "failed"
        }
    }

    public static func hhmm(_ date: Date) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "HH:mm"
        return f.string(from: date)
    }

    /// What the card shows: open actions first (waiting on him, then in
    /// progress, then queued), then the finished ones, newest first; `most` in all.
    public static let most = 6
    public static func shown(_ actions: [Action], most: Int = most) -> [Action] {
        let rank: [Action.State: Int] = [.awaitingAhmed: 0, .inProgress: 1, .queued: 2, .failed: 3, .done: 4]
        return Array(actions.sorted {
            let (a, b) = (rank[$0.state] ?? 9, rank[$1.state] ?? 9)
            if a != b { return a < b }
            if $0.isOpen { return ($0.requestedAt ?? .distantPast) < ($1.requestedAt ?? .distantPast) }
            return ($0.finishedAt ?? .distantPast) > ($1.finishedAt ?? .distantPast)
        }.prefix(most))
    }

    /// The one thing Approve sends: an explicit yes for that action and nothing
    /// else, in Director's own thread, named so its gate does not judge it.
    public static func approveText(_ a: Action) -> String {
        let ref = a.itemId.map { "\(a.what) (\($0))" } ?? a.what
        return "Yes, I approve action \(a.id): \(ref)."
    }

    public static func approveArgv(_ a: Action, thread: String) -> [String] {
        ["director", "--json", "ask", approveText(a), "--named", "--channel", "tranquility", "--external-id", thread]
    }

    /// Send the approval; Director's reply line (its "reply"), or why not.
    /// Blocking: call it detached. `run` is the seam.
    public static func approve(_ a: Action, thread: String,
                               run: (String, [String], TimeInterval) -> Result<String, ScriptError> = {
                                   Subprocess.run($0, $1, timeout: $2)
                               }) -> Result<String, RightHands.AskFailure> {
        guard a.state == .awaitingAhmed else { return .failure(.failed("it is not waiting on you")) }
        let argv = approveArgv(a, thread: thread)
        guard let program = RightHands.executable(argv[0]) else { return .failure(.failed("director is not on this Mac")) }
        switch run(program, Array(argv.dropFirst()), 60) {
        case .success(let out):
            let obj = (try? JSONSerialization.jsonObject(with: Data(out.utf8))) as? [String: Any]
            let reply = ((obj?["reply"] as? String) ?? out).trimmingCharacters(in: .whitespacesAndNewlines)
            return .success(reply)
        case .failure(let error):
            return .failure(.failed(error.message.isEmpty ? "director failed" : error.message))
        }
    }
}
