import Foundation

/// The sessions the panel is FOR, when the user has said so.
///
/// Asked for 23 Sep 2026, in the user's words: *"Instead of a listing of every
/// agent, just the agents I'd need to know about, or maybe the higher-level
/// projects. I don't want to be overwhelmed with a long-ass list, but if I
/// summon you, we can dive deeper."*
///
/// One file, `right-hands.json` beside the queue, names the sessions that get
/// the grid, the chime, the badge and ⌃⌥. Everything else still flows through
/// the hooks into the store exactly as before — a director that reads the
/// store sees every session — it just stops reaching the ear and the first
/// page. **When the file is absent nothing changes**, which is the whole
/// migration story: the panel today is the panel with no file.
///
/// Why this is not the lamp switch, dismiss, or enrolment: the switch is spent
/// by the next waiting turn (`LampSwitch.isOff`, by design), dismiss is one
/// turn deep, and enrolment only governs typing. None of them can say "this
/// session is not mine to watch" and have it stay said. Why it is not a hook
/// wrapper or `VOICE_LOOP_MARKER`: those starve the store, and the store is
/// what the director reads.
///
/// A hand is keyed three ways because a session id changes on every restart
/// and a directory is shared by more than one agent: the id (exact), the
/// working directory (exact, never a prefix — the director's sub-sessions run
/// under its own directory), and the tmux session name the ownership record
/// carries (`sessionName`), which survives a restart. Any one match is a hand.
public enum RightHands {

    /// One right-hand, as the file describes it. Every key optional; an entry
    /// with none of `session`, `cwd`, `tmux` matches nothing and is kept only
    /// so the file round-trips.
    public struct Hand: Codable, Equatable, Sendable {
        /// The spoken and drawn name, pinned. Overrides the harness title
        /// (`GridAssembler.harnessTitle`), so "Director" is what the grid,
        /// the card, `tbase targets` and the manager's key terms all say.
        public var name: String?
        public var session: String?
        public var cwd: String?
        public var tmux: String?
        /// A JSON file this hand keeps current with the projects it is
        /// running (`Rollup`). When set, the hand's card and brief show
        /// those projects instead of its last turn.
        public var rollup: String?
        /// A command that prints the hand's projects, run each time the card
        /// opens: `["director", "--json", "status"]`. Outranks `rollup`. Its
        /// output is read by `Rollup.parse`, which takes either the rollup
        /// shape or Director's own status JSON.
        public var projects: [String]?
        /// The hand's BRAIN: a command that answers what the user said to it,
        /// instead of typing the words into its pane (24 Sep). `{text}` is the
        /// words, `{conversation}` the thread they belong to, `{session}` the
        /// hand's session id:
        /// `["director", "ask", "{text}", "--channel", "tranquility", "--external-id", "{conversation}"]`.
        /// Its stdout, trimmed, is the line the hand speaks back.
        public var ask: [String]?
        /// A command that prints what is ready for the user to look at
        /// (`["director", "--json", "ready"]`, 25 Sep): its items join the
        /// card as "ready" lines, never counted as needing the user.
        public var ready: [String]?

        public init(name: String? = nil, session: String? = nil, cwd: String? = nil,
                    tmux: String? = nil, rollup: String? = nil,
                    projects: [String]? = nil, ask: [String]? = nil, ready: [String]? = nil) {
            self.name = name
            self.session = session
            self.cwd = cwd
            self.tmux = tmux
            self.rollup = rollup
            self.projects = projects
            self.ask = ask
            self.ready = ready
        }

        /// A hand named in the file with nothing to find it by yet: "TeamChat
        /// Manager (placeholder until it exists)", 24 Sep. It still holds its
        /// row, under an id no session can have.
        public var isPlaceholder: Bool {
            name != nil && (session ?? "").isEmpty && (cwd ?? "").isEmpty && (tmux ?? "").isEmpty
        }
        public var placeholderId: String? {
            guard isPlaceholder, let name else { return nil }
            let slug = name.lowercased().map { $0.isLetter || $0.isNumber ? $0 : "-" }
            return "hand:" + String(slug)
        }

        /// Whether this hand's card is its projects rather than its last turn.
        public var hasCard: Bool { projects?.isEmpty == false || rollup != nil }
        /// Whether a reply to this hand is asked of its brain rather than typed.
        public var asks: Bool { ask?.isEmpty == false }

        /// Whether this hand matches a session, by any of its three keys.
        public func matches(sessionId: String, cwd: String?, tmuxSessionName: String?) -> Bool {
            if let session, !session.isEmpty {
                let a = session.lowercased(), b = sessionId.lowercased()
                // A prefix of eight or more is unambiguous in practice and is
                // how ids are written down everywhere else in this app.
                if a == b || (a.count >= 8 && b.hasPrefix(a)) { return true }
            }
            if let want = self.cwd, !want.isEmpty, let have = cwd,
               Self.normalize(path: want) == Self.normalize(path: have) { return true }
            if let tmux, !tmux.isEmpty, let have = tmuxSessionName, tmux == have { return true }
            return false
        }

        static func normalize(path: String) -> String {
            var p = (path as NSString).expandingTildeInPath
            while p.count > 1, p.hasSuffix("/") { p.removeLast() }
            return p
        }
    }

    /// The file's contents.
    public struct Roster: Equatable, Sendable {
        public var hands: [Hand]
        /// Keep preparing summaries for sessions that are NOT hands. Off by
        /// default: a summary is a model call per Stop, and a hand-only panel
        /// has no use for the others'. On when a director wants `tbase brief`
        /// for every session.
        public var summarizeOthers: Bool

        public init(hands: [Hand], summarizeOthers: Bool = false) {
            self.hands = hands
            self.summarizeOthers = summarizeOthers
        }

        /// The hand a session belongs to, or nil.
        public func hand(for sessionId: String, cwd: String?, tmuxSessionName: String?) -> Hand? {
            hands.first { $0.matches(sessionId: sessionId, cwd: cwd, tmuxSessionName: tmuxSessionName) }
        }

        /// Resolve the roster against what is actually running: every id
        /// that is a hand, and the name and rollup each of those ids wears.
        /// `ownership` supplies the tmux session names; `sessions` supplies
        /// ids and directories from whichever list the caller has.
        public func resolve(sessions: [(id: String, cwd: String?)],
                            ownership: [SessionOwnershipRecord]) -> Resolved {
            var tmuxById: [String: String] = [:]
            for record in ownership {
                if let name = record.sessionName { tmuxById[record.sessionId] = name }
            }
            var ids = Set<String>()
            var names: [String: String] = [:]
            var rollups: [String: String] = [:]
            var byId: [String: Hand] = [:]
            for hand in hands {
                guard let id = hand.placeholderId else { continue }
                ids.insert(id)
                byId[id] = hand
                names[id] = hand.name
            }
            // Explicit ids are hands whether or not anything is running under
            // them: a name has to resolve for a row built from disk too.
            for hand in hands {
                guard let session = hand.session, session.count >= 32 else { continue }
                ids.insert(session)
                byId[session] = hand
                if let name = hand.name { names[session] = name }
                if let rollup = hand.rollup { rollups[session] = rollup }
            }
            // Ownership records carry both a cwd and a tmux name, so they
            // are a source of sessions too, not only a lookup table.
            let fromOwnership = ownership.map { (id: $0.sessionId, cwd: $0.cwd) }
            for session in sessions + fromOwnership {
                guard let hand = hand(for: session.id, cwd: session.cwd,
                                      tmuxSessionName: tmuxById[session.id]) else { continue }
                ids.insert(session.id)
                byId[session.id] = hand
                if let name = hand.name { names[session.id] = name }
                if let rollup = hand.rollup { rollups[session.id] = rollup }
            }
            // The roster's own order, one id per hand, for the grid: the id a
            // live session resolved to, else the file's explicit id, else the
            // placeholder's.
            var order: [String] = []
            for hand in hands {
                let resolvedId = byId.filter { $0.value == hand && $0.key.count >= 32 }.keys.sorted().first
                if let id = hand.placeholderId ?? resolvedId ?? hand.session, !order.contains(id) {
                    order.append(id)
                }
            }
            return Resolved(ids: ids, names: names, rollups: rollups,
                            summarizeOthers: summarizeOthers, hands: byId, order: order)
        }
    }

    /// The roster, resolved to session ids. What the grid, the announcer and
    /// `tbase` consume.
    public struct Resolved: Equatable, Sendable {
        public var ids: Set<String>
        public var names: [String: String]
        public var rollups: [String: String]
        public var summarizeOthers: Bool
        /// The hand each resolved id belongs to.
        public var hands: [String: Hand]
        /// One id per hand, in the file's order: the grid's order.
        public var order: [String]

        public init(ids: Set<String>, names: [String: String] = [:],
                    rollups: [String: String] = [:], summarizeOthers: Bool = false,
                    hands: [String: Hand] = [:], order: [String] = []) {
            self.ids = ids
            self.names = names
            self.rollups = rollups
            self.summarizeOthers = summarizeOthers
            self.hands = hands
            self.order = order
        }

        public func contains(_ sessionId: String) -> Bool { ids.contains(sessionId) }
    }

    // MARK: - The file

    public static var url: URL {
        QueueStore.supportDirectory.appendingPathComponent("right-hands.json")
    }

    /// Said out loud when the file is present but cannot be read. Silent by
    /// default; the app points it at its log.
    public nonisolated(unsafe) static var trace: (@Sendable (String) -> Void)?

    /// The file, decoded. Nil means NO FILE: the panel behaves as it always
    /// has. A file that is present but malformed is also nil — failing open
    /// to the old behaviour rather than to an empty grid — and says so on the
    /// trace, because a silent fall-back is how a typo hides a fleet.
    public static func load(from url: URL = RightHands.url) -> Roster? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        switch parse(data) {
        case .success(let roster): return roster
        case .failure(let error):
            trace?("right-hands: \(url.lastPathComponent) is unreadable (\(error)); showing everyone")
            return nil
        }
    }

    public enum ParseError: Error, Equatable { case notJSON, wrongShape(String) }

    /// Pure, for tests. Accepts:
    ///
    ///     {"hands": [ {"name": "Director", "session": "…", "cwd": "…", "tmux": "…", "rollup": "…"}, … ],
    ///      "summarizeOthers": false}
    ///
    /// and, for a file written by hand in a hurry, a bare array, whose
    /// entries may be strings: a uuid (or an 8+ character prefix of one) is a
    /// session, anything starting with `/` or `~` is a directory.
    public static func parse(_ data: Data) -> Result<Roster, ParseError> {
        guard let raw = try? JSONSerialization.jsonObject(with: data) else { return .failure(.notJSON) }
        let entries: [Any]
        var summarizeOthers = false
        if let object = raw as? [String: Any] {
            guard let hands = object["hands"] as? [Any] else {
                return .failure(.wrongShape("no \"hands\" array"))
            }
            entries = hands
            summarizeOthers = object["summarizeOthers"] as? Bool ?? false
        } else if let array = raw as? [Any] {
            entries = array
        } else {
            return .failure(.wrongShape("neither an object nor an array"))
        }
        var hands: [Hand] = []
        for (index, entry) in entries.enumerated() {
            if let string = entry as? String {
                let trimmed = string.trimmingCharacters(in: .whitespaces)
                guard !trimmed.isEmpty else { continue }
                if trimmed.hasPrefix("/") || trimmed.hasPrefix("~") {
                    hands.append(Hand(cwd: trimmed))
                } else {
                    hands.append(Hand(session: trimmed))
                }
            } else if let object = entry as? [String: Any] {
                func string(_ key: String) -> String? {
                    guard let value = object[key] as? String else { return nil }
                    let trimmed = value.trimmingCharacters(in: .whitespaces)
                    return trimmed.isEmpty ? nil : trimmed
                }
                func argv(_ key: String) -> [String]? {
                    guard let list = object[key] as? [String], !list.isEmpty else { return nil }
                    return list
                }
                hands.append(Hand(name: string("name"), session: string("session"),
                                  cwd: string("cwd"), tmux: string("tmux"),
                                  rollup: string("rollup"),
                                  projects: argv("projects"), ask: argv("ask"),
                                  ready: argv("ready")))
            } else {
                return .failure(.wrongShape("entry \(index) is neither an object nor a string"))
            }
        }
        return .success(Roster(hands: hands, summarizeOthers: summarizeOthers))
    }

    // MARK: - The process-wide snapshot

    /// The last resolution anybody performed, for the lookups that have no
    /// live list in hand — `GridAssembler.harnessTitle` names a row from
    /// nine call sites and cannot be handed a roster at each.
    ///
    /// Published by the app's tick (`sessionRowsNow`) and by `tbase` before
    /// it prints, and read by the name lookup. A lookup before the first
    /// publish falls back to the file's explicit ids, so a pinned name on a
    /// known id is never late.
    private final class Snapshot: @unchecked Sendable {
        private let lock = NSLock()
        private var resolved: Resolved?
        func read() -> Resolved? { lock.lock(); defer { lock.unlock() }; return resolved }
        func write(_ value: Resolved?) { lock.lock(); defer { lock.unlock() }; resolved = value }
    }
    private static let snapshot = Snapshot()

    /// Resolve against the live list and remember the answer. Nil when there
    /// is no file, and the snapshot is cleared so a file removed at runtime
    /// takes effect on the next tick.
    @discardableResult
    public static func publish(sessions: [(id: String, cwd: String?)],
                               ownership: [SessionOwnershipRecord],
                               roster: Roster? = RightHands.load()) -> Resolved? {
        let resolved = roster?.resolve(sessions: sessions, ownership: ownership)
        snapshot.write(resolved)
        return resolved
    }

    /// For tests: set the snapshot directly.
    public static func publish(_ resolved: Resolved?) { snapshot.write(resolved) }

    public static func current() -> Resolved? { snapshot.read() }

    /// The user's own name for a session, or nil. The snapshot first; then
    /// the file's explicit ids, so a name pinned to a known id answers before
    /// the first tick has run.
    public static func pinnedName(for sessionId: String, roster: Roster? = RightHands.load()) -> String? {
        if let name = snapshot.read()?.names[sessionId] { return name }
        guard let roster else { return nil }
        return roster.hands.first {
            $0.name != nil && $0.matches(sessionId: sessionId, cwd: nil, tmuxSessionName: nil)
        }?.name
    }

    /// Where a hand's rollup lives, or nil for a session that has none.
    public static func rollupPath(for sessionId: String, roster: Roster? = RightHands.load()) -> String? {
        let path = snapshot.read()?.rollups[sessionId]
            ?? roster?.hands.first {
                $0.rollup != nil && $0.matches(sessionId: sessionId, cwd: nil, tmuxSessionName: nil)
            }?.rollup
        return path.map { ($0 as NSString).expandingTildeInPath }
    }

    /// The hand a session belongs to: the published resolution first, then
    /// the file's explicit ids. Nil for a session that is not a hand.
    public static func hand(for sessionId: String, roster: Roster? = RightHands.load()) -> Hand? {
        if let hand = snapshot.read()?.hands[sessionId] { return hand }
        return roster?.hands.first { $0.matches(sessionId: sessionId, cwd: nil, tmuxSessionName: nil) }
    }

    // MARK: - The brain

    public enum AskFailure: Error, Equatable, Sendable {
        case notABrain
        case failed(String)
    }

    /// Fill a hand's `ask` template. Pure, for tests.
    static func fill(_ template: [String], text: String, conversation: String, session: String) -> [String] {
        template.map {
            $0.replacingOccurrences(of: "{text}", with: text)
                .replacingOccurrences(of: "{conversation}", with: conversation)
                .replacingOccurrences(of: "{session}", with: session)
        }
    }

    /// Ask a hand's brain and return the line it answered with.
    ///
    /// The command is run directly, never through a shell: the words are one
    /// argv element whatever they contain. A bare program name is found on
    /// the user's own directories as well as PATH, because an app launched
    /// from the Dock has a PATH of four system directories and `director`
    /// lives in `~/.local/bin`. `run` is the seam.
    public static func ask(_ hand: Hand, text: String, conversation: String, session: String,
                           timeout: TimeInterval = 60,
                           run: (String, [String], TimeInterval) -> Result<String, ScriptError> = {
                               Subprocess.run($0, $1, timeout: $2)
                           }) -> Result<String, AskFailure> {
        guard let template = hand.ask, !template.isEmpty else { return .failure(.notABrain) }
        let argv = fill(template, text: text, conversation: conversation, session: session)
        guard let program = executable(argv[0]) else {
            return .failure(.failed("\(argv[0]) is not on this Mac"))
        }
        switch run(program, Array(argv.dropFirst()), timeout) {
        case .success(let out):
            let line = out.trimmingCharacters(in: .whitespacesAndNewlines)
            return line.isEmpty ? .failure(.failed("\(argv[0]) answered nothing")) : .success(line)
        case .failure(let error):
            return .failure(.failed(error.message.isEmpty ? "\(argv[0]) failed" : error.message))
        }
    }

    /// Run a hand's `projects` command and read the card from what it prints.
    public static func projects(_ hand: Hand, timeout: TimeInterval = 20,
                                run: (String, [String], TimeInterval) -> Result<String, ScriptError> = {
                                    Subprocess.run($0, $1, timeout: $2)
                                }) -> Rollup? {
        guard let template = hand.projects, !template.isEmpty else { return nil }
        let session = hand.session ?? ""
        let argv = template.map { $0.replacingOccurrences(of: "{session}", with: session) }
        guard let program = executable(argv[0]) else { return nil }
        guard case .success(let out) = run(program, Array(argv.dropFirst()), timeout) else {
            trace?("right-hands: \(argv.joined(separator: " ")) failed; the card falls back")
            return nil
        }
        return Rollup.parse(Data(out.utf8))
    }

    /// The card for a hand: its `projects` command, else its rollup file.
    public static func card(for hand: Hand) -> Rollup? {
        if hand.projects?.isEmpty == false, var rollup = projects(hand) {
            if let ready = hand.ready, !ready.isEmpty, let program = executable(ready[0]),
               case .success(let out) = Subprocess.run(program, Array(ready.dropFirst()), timeout: 20) {
                rollup.items += Rollup.readyItems(Data(out.utf8))
            }
            return rollup
        }
        guard let path = hand.rollup else { return nil }
        return Rollup.load(path: (path as NSString).expandingTildeInPath)
    }

    /// An absolute path for a program named in a template.
    static func executable(_ name: String) -> String? {
        let fm = FileManager.default
        if name.contains("/") {
            let path = (name as NSString).expandingTildeInPath
            return fm.isExecutableFile(atPath: path) ? path : nil
        }
        let home = fm.homeDirectoryForCurrentUser.path
        let dirs = (ProcessInfo.processInfo.environment["PATH"] ?? "").split(separator: ":").map(String.init)
            + ["\(home)/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
        return dirs.map { "\($0)/\(name)" }.first { fm.isExecutableFile(atPath: $0) }
    }

    // MARK: - The rollup

    /// What a director's card shows instead of its last turn: at most five
    /// projects, each with a state and one line. The director writes it; this
    /// app only reads it.
    ///
    ///     {"updatedAt": "2026-09-23T19:40:00Z",
    ///      "projects": [
    ///        {"name": "Yobi1 design", "state": "needs you", "line": "Your call on the nav."},
    ///        {"name": "Firebase", "state": "ready", "line": "Migration is green."},
    ///        {"name": "GA fab", "state": "moving", "line": "Writing the eval harness."}]}
    public struct Rollup: Equatable, Sendable {
        public enum State: String, CaseIterable, Sendable {
            /// A decision or an answer only the user can give.
            case needsYou = "needs you"
            /// Finished, or waiting to be looked at.
            case ready
            /// Working; nothing to do.
            case moving

            /// Lenient on the way in: "needs_you", "needs-you", "blocked",
            /// "waiting" all mean the first; anything unrecognised is moving,
            /// because a project the director did not flag is not asking.
            static func read(_ raw: String?) -> State {
                let key = (raw ?? "").lowercased()
                    .replacingOccurrences(of: "_", with: " ")
                    .replacingOccurrences(of: "-", with: " ")
                    .trimmingCharacters(in: .whitespaces)
                switch key {
                case "needs you", "needsyou", "blocked", "waiting", "decision", "question": return .needsYou
                case "ready", "done", "finished", "review": return .ready
                default: return .moving
                }
            }

            /// Needs-you first, then ready, then moving: the order the user
            /// wants to hear them in, which is the order of the grid's lamps.
            var rank: Int {
                switch self { case .needsYou: return 0; case .ready: return 1; case .moving: return 2 }
            }
        }

        public struct Project: Equatable, Sendable {
            public var name: String
            public var state: State
            public var line: String
            public init(name: String, state: State, line: String) {
                self.name = name
                self.state = state
                self.line = line
            }
        }

        /// The cap. A card the user can take in at a glance is the whole
        /// point; a sixth project is the list they asked not to see.
        public static let limit = 5

        public var projects: [Project]
        public var updatedAt: String?
        /// A whole-fleet count to say instead of counting the projects shown,
        /// when the source knows more than five things.
        public var totals: String?
        /// How many things need the user across the whole source, not only
        /// the five shown. The row's dot, and the accordion's first line.
        public var needsYou: Int
        /// The sessions Director says need the user, by session id: a hand's
        /// dot when nothing else feeds this app its turns (25 Sep, the
        /// Director app has no hooks of its own).
        public var needsSessions: Set<String>
        /// Director's own words for the panel (25 Sep, `needs` in its status
        /// JSON): one sentence naming the top three, and "<project>: <what>"
        /// lines already cut at a word, in the order of the numbered list
        /// `director ask` binds "number 2" to. Empty when the source has none.
        public var panelSummary: String?
        public var panelLines: [String]

        /// One line under an open hand, and what it is (25 Sep, Ahmed: "the
        /// little indicators that say this agent is waiting on me matter").
        /// Only `waiting` is counted and only `waiting` is said; `ready` and
        /// `blocked` are shown with their own glyph and never spoken.
        public struct Item: Equatable, Sendable {
            public enum Kind: String, Sendable {
                /// Waiting on you: the hand's dot, and the summary's count.
                case waiting
                /// Finished work ready for you to look at.
                case ready
                /// Blocked on another agent (or on Director), not on you.
                case blocked
                /// A project that is only moving (the older rollup shape).
                case moving
            }
            public var line: String
            public var kind: Kind
            public init(line: String, kind: Kind) {
                self.line = line
                self.kind = kind
            }
        }
        /// The lines in the order they are drawn: waiting, then ready, then
        /// blocked. Empty for the older rollup shape, which `Accordion.entries`
        /// reads from `projects` instead.
        public var items: [Item]
        /// Work is moving: the hand's row wears a hollow dot when nothing
        /// needs the user.
        public var moving: Bool
        /// The sessions Director says are working, by session id.
        public var workingSessions: Set<String>

        public init(projects: [Project], updatedAt: String? = nil, totals: String? = nil,
                    needsYou: Int? = nil, needsSessions: Set<String> = [],
                    panelSummary: String? = nil, panelLines: [String] = [],
                    items: [Item] = [], moving: Bool? = nil, workingSessions: Set<String> = []) {
            self.projects = projects
            self.updatedAt = updatedAt
            self.totals = totals
            self.needsYou = needsYou ?? projects.filter { $0.state == .needsYou }.count
            self.needsSessions = needsSessions
            self.panelSummary = panelSummary
            self.panelLines = panelLines
            self.items = items
            self.moving = moving ?? projects.contains { $0.state == .moving }
            self.workingSessions = workingSessions
        }

        /// A hand's row, in three states and no more (25 Sep): filled when
        /// something waits on the user, hollow when work is moving, nothing
        /// when quiet. Filled wins; the same truth as the spoken sentence.
        public enum Indicator: Equatable, Sendable { case needsYou, moving, quiet }

        public static func indicator(_ card: Rollup?, sessionAsks: Bool = false,
                                     sessionWorking: Bool = false) -> Indicator {
            if (card?.needsYou ?? 0) > 0 || sessionAsks { return .needsYou }
            if card?.moving == true || sessionWorking { return .moving }
            return .quiet
        }

        /// `director --json ready` read as "ready" lines: each item's title,
        /// cut at a word, in Director's order.
        public static func readyItems(_ data: Data) -> [Item] {
            guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let raw = object["items"] as? [[String: Any]] else { return [] }
            return raw.compactMap { entry in
                guard let title = (entry["title"] as? String)?
                    .trimmingCharacters(in: .whitespacesAndNewlines), !title.isEmpty else { return nil }
                return Item(line: cut(title), kind: .ready)
            }
        }

        /// At most `limit` characters, cut at a word, "…" when cut; a
        /// trailing "ready to look at." is dropped, since the glyph says it.
        static func cut(_ text: String, limit: Int = 60) -> String {
            var t = text.replacingOccurrences(of: "\n", with: " ")
            for tail in [", ready to look at.", " ready to look at.", ", ready to look at"] where t.hasSuffix(tail) {
                t = String(t.dropLast(tail.count))
            }
            guard t.count > limit else { return t }
            var head = String(t.prefix(limit))
            if let space = head.lastIndex(of: " ") { head = String(head[..<space]) }
            while let last = head.last, ",;:-–—".contains(last) { head.removeLast() }
            return head + "…"
        }

        public static func load(path: String) -> Rollup? {
            guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)) else { return nil }
            return parse(data)
        }

        /// Pure. Sorted needs-you first, cut to `limit`. Nil for anything that
        /// is not a rollup at all; an empty project list is a rollup that
        /// says "nothing to report", which is an answer.
        public static func parse(_ data: Data) -> Rollup? {
            guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
            if object["projects"] == nil, let groups = object["groups"] as? [String: Any] {
                var card = fromDirectorStatus(groups)
                if let needs = object["needs"] as? [String: Any] {
                    card.panelSummary = (needs["summary"] as? String)
                        .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                        .flatMap { $0.isEmpty ? nil : $0 }
                    card.panelLines = (needs["lines"] as? [[String: Any]] ?? [])
                        .compactMap { ($0["line"] as? String) ?? ($0["full"] as? String) }
                        .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                        .filter { !$0.isEmpty }
                    // Director's needs list is the one truth (25 Sep): its
                    // summary counts exactly these lines, so the dot and the
                    // count are these lines, not the needs_you group, which
                    // is before Director's noise rules.
                    card.needsYou = card.panelLines.count
                    card.items = card.panelLines.map { Item(line: $0, kind: .waiting) } + card.items
                }
                return card
            }
            // A hand's own card (`hand-status`, 25 Sep):
            // {"summary": "…", "moving": false, "items": [{"line": "…", "kind": "waiting"}]}
            if object["projects"] == nil, let raw = object["items"] as? [[String: Any]] {
                let items = raw.compactMap { entry -> Item? in
                    guard let line = (entry["line"] as? String)?
                        .trimmingCharacters(in: .whitespacesAndNewlines), !line.isEmpty else { return nil }
                    let kind = Item.Kind(rawValue: (entry["kind"] as? String ?? "").lowercased()) ?? .waiting
                    return Item(line: line, kind: kind)
                }
                let order: [Item.Kind] = [.waiting, .ready, .blocked, .moving]
                let sorted = items.enumerated().sorted {
                    (order.firstIndex(of: $0.element.kind)!, $0.offset) < (order.firstIndex(of: $1.element.kind)!, $1.offset)
                }.map(\.element)
                let summary = (object["summary"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines)
                return Rollup(projects: [], needsYou: sorted.filter { $0.kind == .waiting }.count,
                              panelSummary: summary?.isEmpty == false ? summary : nil,
                              items: sorted, moving: object["moving"] as? Bool ?? false)
            }
            guard let raw = object["projects"] as? [[String: Any]] else { return nil }
            let projects = raw.compactMap { entry -> Project? in
                guard let name = (entry["name"] as? String)?.trimmingCharacters(in: .whitespaces),
                      !name.isEmpty else { return nil }
                return Project(name: name, state: State.read(entry["state"] as? String),
                               line: ((entry["line"] as? String) ?? "").trimmingCharacters(in: .whitespacesAndNewlines))
            }
            // Stable: the director's own order within a state is kept.
            let sorted = projects.enumerated()
                .sorted { ($0.element.state.rank, $0.offset) < ($1.element.state.rank, $1.offset) }
                .map(\.element)
            return Rollup(projects: Array(sorted.prefix(limit)),
                          updatedAt: object["updatedAt"] as? String)
        }

        /// Director's `--json status` read as a card (24 Sep): the agents
        /// waiting on the user first, then the ones working, five at most.
        /// Each line is the agent's own question, else its worker note, else
        /// the first reason Director gave; `totals` is the whole fleet, so the
        /// card says "10 need you" even when it shows three of them.
        static func fromDirectorStatus(_ groups: [String: Any]) -> Rollup {
            func rows(_ key: String) -> [[String: Any]] { groups[key] as? [[String: Any]] ?? [] }
            func line(_ a: [String: Any]) -> String {
                for key in ["pending_question", "question", "worker_note", "block_detail"] {
                    if let v = a[key] as? String {
                        let t = v.trimmingCharacters(in: .whitespacesAndNewlines)
                        if !t.isEmpty { return clip(t) }
                    }
                }
                if let last = a["last_message"] as? String,
                   !last.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return clip(last) }
                // Director's own reasons, but not its judge's bookkeeping:
                // "Jev: waiting on Ahmed (ahmed_decision, 100%)" says nothing a
                // needs-you lamp has not already said.
                if let reasons = a["reasons"] as? [String],
                   let first = reasons.first(where: { !$0.hasPrefix("Jev:") }) { return clip(first) }
                return ""
            }
            func name(_ a: [String: Any]) -> String? {
                for key in ["name", "claude_name", "project"] {
                    if let v = a[key] as? String, !v.isEmpty { return v }
                }
                return nil
            }
            var projects: [Project] = []
            for (key, state) in [("needs_you", State.needsYou), ("working", State.moving)] {
                for a in rows(key) {
                    guard let n = name(a) else { continue }
                    projects.append(Project(name: n, state: state, line: line(a)))
                }
            }
            var parts: [String] = []
            for (key, word) in [("needs_you", "need you"), ("working", "working"),
                                ("stuck", "stuck"), ("idle", "idle")] {
                let n = rows(key).count
                if n > 0 { parts.append("\(n) \(key == "needs_you" && n == 1 ? "needs you" : word)") }
            }
            // Blocked on another agent, or on Director: shown, never counted.
            let blocked = (rows("blocked") + rows("needs_director")).compactMap { a -> Item? in
                guard let n = name(a) else { return nil }
                let what = line(a)
                return Item(line: cut(what.isEmpty ? n : "\(n): \(what)"), kind: .blocked)
            }
            return Rollup(projects: Array(projects.prefix(limit)),
                          totals: parts.isEmpty ? nil : parts.joined(separator: ", ") + ".",
                          needsYou: rows("needs_you").count,
                          needsSessions: Set(rows("needs_you").compactMap { $0["session_id"] as? String }),
                          items: blocked, moving: !rows("working").isEmpty,
                          workingSessions: Set(rows("working").compactMap { $0["session_id"] as? String }))
        }

        /// One sentence, at most about twenty words: a worker note can be a
        /// paragraph, and the card is for a glance.
        static func clip(_ text: String, words: Int = 20) -> String {
            // A command or a path in backticks is for reading, not hearing.
            let flat = text.replacingOccurrences(of: "\n", with: " ")
                .replacingOccurrences(of: "`[^`]*`", with: "a command", options: .regularExpression)
                .replacingOccurrences(of: "*", with: "")
            let first = flat.split(separator: ".", maxSplits: 1).first.map(String.init) ?? flat
            let cut = first.split(separator: " ").prefix(words).joined(separator: " ")
            return cut.count < first.count ? cut + "…" : cut
        }

        /// The card and the spoken line, built from the projects. `topic` is
        /// the hand's name, so the ear hears who is talking first, as with
        /// every other brief.
        public func brief(topic: String) -> SessionBrief {
            let counts = Dictionary(grouping: projects, by: \.state).mapValues(\.count)
            func count(_ state: State) -> Int { counts[state] ?? 0 }
            let happened: String
            if let totals {
                happened = totals
            } else if projects.isEmpty {
                happened = "No projects to report."
            } else {
                var parts: [String] = []
                if count(.needsYou) > 0 { parts.append("\(count(.needsYou)) need\(count(.needsYou) == 1 ? "s" : "") you") }
                if count(.ready) > 0 { parts.append("\(count(.ready)) ready") }
                if count(.moving) > 0 { parts.append("\(count(.moving)) moving") }
                happened = "\(projects.count) project\(projects.count == 1 ? "" : "s"): " + parts.joined(separator: ", ") + "."
            }
            // The spoken recap: every project, state and line, in rank order.
            // Under the usual forty words only when the director keeps its
            // lines short, which is the director's job; the sanitizer clamps
            // nothing here because the pull is the whole card.
            let lines = projects.map { project -> String in
                let line = project.line.isEmpty ? "" : " " + Self.sentence(project.line)
                switch project.state {
                case .needsYou: return "\(project.name) needs you." + line
                case .ready: return "\(project.name) is ready." + line
                case .moving: return "\(project.name) is moving." + line
                }
            }
            let recap = ([topic + "."] + [happened] + lines).joined(separator: " ")
            let asking = projects.first { $0.state == .needsYou }
            return SessionBrief(
                topic: topic,
                goal: "The projects \(topic) is running.",
                happened: happened,
                nextStep: projects.isEmpty ? nil : "Say \(topic), then a project name, to dive in.",
                question: asking.map { "\($0.name): \(Self.sentence($0.line))" },
                findings: lines.isEmpty ? nil : lines.joined(separator: " "),
                recap: recap)
        }

        static func sentence(_ text: String) -> String {
            guard let last = text.last else { return text }
            return ".!?".contains(last) ? text : text + "."
        }
    }

    /// The rollup for a session, if it is a hand with one and the file reads.
    public static func rollup(for sessionId: String) -> Rollup? {
        if let hand = hand(for: sessionId), hand.hasCard { return card(for: hand) }
        guard let path = rollupPath(for: sessionId) else { return nil }
        return Rollup.load(path: path)
    }

    // MARK: - The accordion (24 Sep)

    /// What an opened right-hand shows under its row, in Ahmed's drawing:
    /// one short line ("5 things need you"), the first three items, then
    /// "more…". "So that it never really overwhelms me."
    public enum Accordion {
        public static let shown = 3

        /// The row ids an opened hand's lines wear: `<parent>#summary`,
        /// `<parent>#item-<n>`, `<parent>#more`. Parsed back by `part(of:)`.
        public enum Part: Equatable, Sendable {
            case summary, item(Int), more
        }

        public static func id(_ parent: String, _ part: Part) -> String {
            switch part {
            case .summary: return parent + "#summary"
            case .item(let n): return parent + "#item-\(n)"
            case .more: return parent + "#more"
            }
        }

        public static func part(of id: String) -> (parent: String, part: Part)? {
            guard let hash = id.lastIndex(of: "#") else { return nil }
            let parent = String(id[..<hash]), tail = id[id.index(after: hash)...]
            switch tail {
            case "summary": return (parent, .summary)
            case "more": return (parent, .more)
            default:
                guard tail.hasPrefix("item-"), let n = Int(tail.dropFirst(5)) else { return nil }
                return (parent, .item(n))
            }
        }

        /// The first line, said and drawn.
        public static func summary(_ card: Rollup) -> String {
            switch card.needsYou {
            case 0: return "Nothing needs you"
            case 1: return "1 thing needs you"
            default: return "\(card.needsYou) things need you"
            }
        }

        /// The one sentence a tap on the hand speaks: the count, and the first
        /// thing with its subject named (ruling 5's shape).
        public static func sentence(_ card: Rollup) -> String {
            guard let first = card.projects.first else { return summary(card) + "." }
            let what = first.line.isEmpty ? first.name : "\(first.name): \(first.line)"
            return summary(card) + "; first, " + Rollup.sentence(what)
        }

        /// What a tap on one item ASKS the hand (25 Sep): Director's
        /// `explain_item` intent reads the item and never types into a pane.
        /// By name, not by number: the accordion's items come from
        /// `director --json status`, whose order need not match the numbered
        /// list Director last showed this thread, and a name binds either way.
        public static func explainRequest(_ project: Rollup.Project) -> String {
            "tell me more about \(project.name)"
        }

        /// The same ask by NUMBER, for Director's own panel lines (25 Sep):
        /// they are in the order of the numbered list Director keeps for the
        /// thread (refreshed by the "what needs me?" asked when the hand
        /// opens), and they carry no agent names to bind by.
        public static func explainRequest(number n: Int) -> String {
            "tell me more about number \(n)"
        }

        /// The most lines an open hand shows once "more…" is pressed: Director
        /// numbers five for a thread, and a sixth could not be asked about.
        public static let most = 5

        /// The line an item row shows and speaks: Director's own panel line
        /// when it sent them, else the project and its line.
        public static func lines(_ card: Rollup) -> [String] {
            if !card.panelLines.isEmpty { return card.panelLines }
            return card.projects.map { $0.line.isEmpty ? $0.name : "\($0.name): \($0.line)" }
        }

        /// What a tap on one item speaks: whose it is, and what it needs.
        public static func itemSentence(_ project: Rollup.Project) -> String {
            let state: String
            switch project.state {
            case .needsYou: state = "needs you"
            case .ready: state = "is ready"
            case .moving: state = "is moving"
            }
            return project.line.isEmpty ? "\(project.name) \(state)."
                : "\(project.name) \(state). " + Rollup.sentence(project.line)
        }

        /// Every line an open hand can show, in drawing order: waiting on you,
        /// then ready to look at, then blocked on another agent (25 Sep).
        /// Director's needs list, when it sent one, is the whole of "waiting";
        /// the older rollup shape is read from its projects.
        public static func entries(_ card: Rollup) -> [Rollup.Item] {
            var all = card.items
            if card.panelSummary == nil, !all.contains(where: { $0.kind == .waiting }) {
                all = card.projects.map { p in
                    let line = p.line.isEmpty ? p.name : "\(p.name): \(p.line)"
                    switch p.state {
                    case .needsYou: return Rollup.Item(line: line, kind: .waiting)
                    case .ready: return Rollup.Item(line: line, kind: .ready)
                    case .moving: return Rollup.Item(line: line, kind: .moving)
                    }
                } + all
            }
            let order: [Rollup.Item.Kind] = [.waiting, .ready, .blocked, .moving]
            return all.enumerated().sorted {
                (order.firstIndex(of: $0.element.kind)!, $0.offset) < (order.firstIndex(of: $1.element.kind)!, $1.offset)
            }.map(\.element)
        }

        /// What "more…" opens to: every line waiting on you, so the count in
        /// the summary is the count of filled lines, and at most two each of
        /// the rest; `longest` rows in all.
        public static let longest = 12
        public static let othersShown = 2
        public static func expanded(_ card: Rollup) -> [Rollup.Item] {
            var out: [Rollup.Item] = []
            var others: [Rollup.Item.Kind: Int] = [:]
            for item in entries(card) where out.count < longest {
                if item.kind == .waiting { out.append(item); continue }
                let n = others[item.kind, default: 0]
                if n < othersShown { out.append(item); others[item.kind] = n + 1 }
            }
            return out
        }

        /// The lamp an item row wears, which `NestedRowView` draws as its
        /// glyph: waiting is the hand's own green, ready is advisory blue,
        /// blocked is a quiet socket, and a merely moving project has none.
        public static func lamp(for kind: Rollup.Item.Kind) -> Lamp {
            switch kind {
            case .waiting: return .ready
            case .ready: return .working
            case .blocked: return .running
            case .moving: return .unlit
            }
        }

        /// The sentence the summary row shows. Director's needs summary first:
        /// it counts exactly the lines drawn as waiting (25 Sep, "the number
        /// in the summary equals the number of filled items below"). Then the
        /// sentence the hand said when opened, then a count.
        public static func summaryText(_ card: Rollup, said: String? = nil) -> String {
            card.panelSummary ?? said ?? summary(card)
        }

        /// The lines, as rows under `parent`: the summary, the first `shown`
        /// lines, and "N more…" when more would show; `all` is "more…"
        /// pressed. Each item row wears its kind as its lamp. Each opens its
        /// card, which is why each carries a recorded turn.
        public static func rows(parent: String, card: Rollup, said: String? = nil,
                                all: Bool = false) -> [SessionRow] {
            var out = [SessionRow(id: id(parent, .summary), name: summaryText(card, said: said), aux: "",
                                  lamp: .running, read: .opened, hasRecordedTurn: true)
                .placed(pinned: false, parentId: parent)]
            let full = expanded(card)
            let lines = all ? full : Array(full.prefix(shown))
            for (index, item) in lines.enumerated() {
                out.append(SessionRow(id: id(parent, .item(index + 1)), name: item.line, aux: "",
                                      lamp: lamp(for: item.kind), read: .none,
                                      detail: item.line, hasRecordedTurn: true)
                    .placed(pinned: false, parentId: parent))
            }
            if lines.count < full.count {
                out.append(SessionRow(id: id(parent, .more), name: "\(full.count - lines.count) more…", aux: "",
                                      lamp: .running, read: .opened, hasRecordedTurn: true)
                    .placed(pinned: false, parentId: parent))
            }
            return out
        }
    }

    // MARK: - The card cache

    /// The last card each hand's `projects` command printed, refreshed off
    /// the main thread (CLAUDE.md rule 9: a subprocess never runs where a
    /// frame is drawn). The grid reads it for the dot and the accordion on
    /// every repaint; the tick refreshes it when it is older than `maxAge`.
    public final class CardCache: @unchecked Sendable {
        public static let shared = CardCache()
        private let lock = NSLock()
        private var cards: [String: (card: Rollup, at: Date)] = [:]
        private var inFlight = Set<String>()

        public init() {}

        public func card(for id: String) -> Rollup? {
            lock.lock(); defer { lock.unlock() }
            return cards[id]?.card
        }

        public func put(_ card: Rollup, for id: String, at: Date = Date()) {
            lock.lock(); cards[id] = (card, at); lock.unlock()
        }

        /// Which of these hands are due a refresh, claimed so two ticks never
        /// run the same command at once.
        public func claimStale(_ ids: [String], maxAge: TimeInterval, now: Date = Date()) -> [String] {
            lock.lock(); defer { lock.unlock() }
            let due = ids.filter { id in
                !inFlight.contains(id) && (cards[id].map { now.timeIntervalSince($0.at) >= maxAge } ?? true)
            }
            inFlight.formUnion(due)
            return due
        }

        public func release(_ id: String) {
            lock.lock(); inFlight.remove(id); lock.unlock()
        }

        /// The sentence the hand said when it was last opened, by hand id.
        private var said: [String: String] = [:]
        public func putSaid(_ line: String?, for id: String) {
            lock.lock(); said[id] = line; lock.unlock()
        }
        public func saidLine(for id: String) -> String? {
            lock.lock(); defer { lock.unlock() }; return said[id]
        }

        /// Run each due hand's command and keep what it printed. Blocking;
        /// call it detached.
        public func refresh(_ hands: [String: Hand], maxAge: TimeInterval = 10) {
            let due = claimStale(hands.filter { $0.value.hasCard }.map(\.key), maxAge: maxAge)
            for id in due {
                defer { release(id) }
                if let hand = hands[id], let card = RightHands.card(for: hand) { put(card, for: id) }
            }
        }
    }
}
