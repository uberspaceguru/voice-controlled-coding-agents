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

        public init(name: String? = nil, session: String? = nil, cwd: String? = nil,
                    tmux: String? = nil, rollup: String? = nil) {
            self.name = name
            self.session = session
            self.cwd = cwd
            self.tmux = tmux
            self.rollup = rollup
        }

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
            // Explicit ids are hands whether or not anything is running under
            // them: a name has to resolve for a row built from disk too.
            for hand in hands {
                guard let session = hand.session, session.count >= 32 else { continue }
                ids.insert(session)
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
                if let name = hand.name { names[session.id] = name }
                if let rollup = hand.rollup { rollups[session.id] = rollup }
            }
            return Resolved(ids: ids, names: names, rollups: rollups, summarizeOthers: summarizeOthers)
        }
    }

    /// The roster, resolved to session ids. What the grid, the announcer and
    /// `tbase` consume.
    public struct Resolved: Equatable, Sendable {
        public var ids: Set<String>
        public var names: [String: String]
        public var rollups: [String: String]
        public var summarizeOthers: Bool

        public init(ids: Set<String>, names: [String: String] = [:],
                    rollups: [String: String] = [:], summarizeOthers: Bool = false) {
            self.ids = ids
            self.names = names
            self.rollups = rollups
            self.summarizeOthers = summarizeOthers
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
                hands.append(Hand(name: string("name"), session: string("session"),
                                  cwd: string("cwd"), tmux: string("tmux"),
                                  rollup: string("rollup")))
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

        public init(projects: [Project], updatedAt: String? = nil) {
            self.projects = projects
            self.updatedAt = updatedAt
        }

        public static func load(path: String) -> Rollup? {
            guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)) else { return nil }
            return parse(data)
        }

        /// Pure. Sorted needs-you first, cut to `limit`. Nil for anything that
        /// is not a rollup at all; an empty project list is a rollup that
        /// says "nothing to report", which is an answer.
        public static func parse(_ data: Data) -> Rollup? {
            guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let raw = object["projects"] as? [[String: Any]] else { return nil }
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

        /// The card and the spoken line, built from the projects. `topic` is
        /// the hand's name, so the ear hears who is talking first, as with
        /// every other brief.
        public func brief(topic: String) -> SessionBrief {
            let counts = Dictionary(grouping: projects, by: \.state).mapValues(\.count)
            func count(_ state: State) -> Int { counts[state] ?? 0 }
            let happened: String
            if projects.isEmpty {
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
        guard let path = rollupPath(for: sessionId) else { return nil }
        return Rollup.load(path: path)
    }
}
