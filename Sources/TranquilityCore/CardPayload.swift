import Foundation

/// What the Director card shows while Director's sentence plays (M26, 26 Sep,
/// Ahmed: "it can show me things as the agent talks"). Director returns one with
/// each spoken turn (`card` in `director --json ask`); the voice hands it to the
/// app as a `card` event, a JSON string; the card shows it until the next turn.
///
///     {"kind": "list", "title": "What needs you",
///      "rows": [{"n": 1, "project": "YobiWispr", "title": "run this command?", "age": "3 h", "agent": "w-a20-2"}]}
///     {"kind": "item", "n": 2, "project": "TeamChat", "title": "desktop refresh fix",
///      "question": "Run this command? git worktree list", "agent": "w-a21",
///      "reply": "approve" | "answer" | null, "action_id": 12}
///     {"kind": "action", "id": 12, "what": "…", "state": "in_progress", "target": "w-a21",
///      "started_at": 1790380210000, "finished_at": null}
///     {"kind": "screen", "agent": "w-a21", "lines": ["$ git status", "…"]}
///     {"kind": "none"}
public enum CardPayload: Equatable, Sendable {
    public struct Row: Equatable, Sendable {
        public var n: Int?
        public var project: String?
        public var title: String
        public var age: String?
        public var agent: String?
        public init(n: Int? = nil, project: String? = nil, title: String, age: String? = nil, agent: String? = nil) {
            self.n = n; self.project = project; self.title = title; self.age = age; self.agent = agent
        }
    }
    public struct Item: Equatable, Sendable {
        public enum Reply: String, Sendable { case approve, answer }
        public var n: Int?
        public var project: String?
        public var title: String
        public var question: String?
        public var agent: String?
        public var reply: Reply?
        public var actionId: Int?
        public init(n: Int? = nil, project: String? = nil, title: String, question: String? = nil,
                    agent: String? = nil, reply: Reply? = nil, actionId: Int? = nil) {
            self.n = n; self.project = project; self.title = title; self.question = question
            self.agent = agent; self.reply = reply; self.actionId = actionId
        }
    }

    case list(title: String?, rows: [Row])
    case item(Item)
    case action(PendingActions.Action)
    case screen(agent: String?, lines: [String])

    /// The most rows and screen lines the card draws.
    public static let mostRows = 6
    public static let mostLines = 18

    /// Nil for "none", nothing, or anything that is not one of the four kinds.
    public static func parse(json: String?) -> CardPayload? {
        guard let json, let data = json.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        return parse(obj)
    }

    public static func parse(_ o: [String: Any]) -> CardPayload? {
        func text(_ v: Any?) -> String? {
            guard let s = (v as? String)?.trimmingCharacters(in: .whitespacesAndNewlines), !s.isEmpty else { return nil }
            return s
        }
        func int(_ v: Any?) -> Int? { (v as? NSNumber)?.intValue }
        switch (o["kind"] as? String)?.lowercased() {
        case "list":
            let rows = (o["rows"] as? [[String: Any]] ?? []).compactMap { r -> Row? in
                guard let title = text(r["title"]) ?? text(r["line"]) else { return nil }
                return Row(n: int(r["n"]), project: text(r["project"]), title: title, age: text(r["age"]),
                           agent: text(r["agent"]))
            }
            return rows.isEmpty ? nil : .list(title: text(o["title"]), rows: Array(rows.prefix(mostRows)))
        case "item":
            guard let title = text(o["title"]) ?? text(o["question"]) else { return nil }
            return .item(Item(n: int(o["n"]), project: text(o["project"]), title: title, question: text(o["question"]),
                              agent: text(o["agent"]), reply: text(o["reply"]).flatMap(Item.Reply.init(rawValue:)),
                              actionId: int(o["action_id"])))
        case "action":
            var row = o
            if row["what"] == nil { row["what"] = o["title"] }
            guard let data = try? JSONSerialization.data(withJSONObject: [row]),
                  let action = PendingActions.parse(data)?.first else { return nil }
            return .action(action)
        case "screen":
            let lines = (o["lines"] as? [String]) ?? (text(o["text"])?.components(separatedBy: "\n") ?? [])
            return lines.isEmpty ? nil : .screen(agent: text(o["agent"]), lines: Array(lines.suffix(mostLines)))
        default:
            return nil
        }
    }

    /// What Approve sends for an item: one explicit yes for that item and nothing else.
    public static func approveText(_ item: Item) -> String {
        let what = [item.project, item.title].compactMap { $0 }.joined(separator: ": ")
        if let id = item.actionId { return "Yes, I approve action \(id): \(what)." }
        if let n = item.n { return "Yes, I approve number \(n): \(what)." }
        return "Yes, I approve \(what)."
    }

    /// The card's buttons, as row ids the panel's tap path carries. The
    /// prefix keeps them apart from session ids and the accordion's ids.
    public enum Door: Equatable, Sendable {
        /// Ghostty on the agent's tmux session.
        case goTo(agent: String)
        /// The agent's pane, where its question is asked.
        case answer(agent: String)
        /// The explicit yes for what the card shows (its item or its action).
        case approve

        public static let prefix = "card#"

        public var id: String {
            switch self {
            case .goTo(let agent): return "\(Self.prefix)goto:\(agent)"
            case .answer(let agent): return "\(Self.prefix)answer:\(agent)"
            case .approve: return "\(Self.prefix)approve"
            }
        }

        public init?(id: String) {
            guard id.hasPrefix(Self.prefix) else { return nil }
            let rest = id.dropFirst(Self.prefix.count)
            if rest == "approve" { self = .approve; return }
            guard let colon = rest.firstIndex(of: ":") else { return nil }
            let agent = String(rest[rest.index(after: colon)...])
            guard !agent.isEmpty else { return nil }
            switch rest[..<colon] {
            case "goto": self = .goTo(agent: agent)
            case "answer": self = .answer(agent: agent)
            default: return nil
            }
        }
    }

    /// The yes Approve sends, or nil when the card shows nothing waiting on him.
    public var approveText: String? {
        switch self {
        case .item(let item) where item.reply == .approve: return Self.approveText(item)
        case .action(let a) where a.state == .awaitingAhmed: return PendingActions.approveText(a)
        default: return nil
        }
    }

    /// Director's answer to a press: its sentence and its next card (the raw
    /// `card_json`, which the app treats exactly like a `card` event).
    public struct Answer: Equatable, Sendable {
        public var reply: String
        public var card: String?
    }

    /// `director --json ask <text> --named` in the card's thread. Blocking:
    /// call it detached. `run` is the seam.
    public static func ask(_ text: String, thread: String,
                           run: (String, [String], TimeInterval) -> Result<String, ScriptError> = {
                               Subprocess.run($0, $1, timeout: $2)
                           }) -> Result<Answer, RightHands.AskFailure> {
        let argv = ["--json", "ask", text, "--named", "--channel", "tranquility", "--external-id", thread]
        guard let program = RightHands.executable("director") else { return .failure(.failed("director is not on this Mac")) }
        switch run(program, argv, 60) {
        case .success(let out):
            let obj = (try? JSONSerialization.jsonObject(with: Data(out.utf8))) as? [String: Any]
            let reply = ((obj?["reply"] as? String) ?? out).trimmingCharacters(in: .whitespacesAndNewlines)
            var card: String?
            if let raw = obj?["card_json"] {
                if let s = raw as? String { card = s }
                else if let d = try? JSONSerialization.data(withJSONObject: raw) { card = String(decoding: d, as: UTF8.self) }
            }
            return .success(Answer(reply: reply, card: card))
        case .failure(let error):
            return .failure(.failed(error.message.isEmpty ? "director failed" : error.message))
        }
    }
}
