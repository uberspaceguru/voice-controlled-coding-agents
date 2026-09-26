import Foundation

/// Manager mode (19 Sep 2026): the hands-free manager as a stdio child of the app.
///
/// The manager (tb-voice) listens all day, decides with Jev whether it was
/// addressed, and drives the fleet through the doors the app already has:
/// `tbase send`, `tbase new`, and the speak-only deep links. What the app owes
/// it is a place to stand: the app spawns it exactly the way it spawns an ACP
/// agent, reads one JSON line per event from its stdout, and paints the orb
/// and the state label from those lines. The child never learns anything
/// about the app's audio path, and the app never parses the child's speech.
///
/// `ManagerEvent` is the contract. A native manager written in Core later
/// emits the same lines, and the orb does not know the difference.
public struct ManagerEvent: Codable, Equatable, Sendable {
    public enum Kind: String, Codable, Sendable {
        /// The pipeline is up and the microphone is open: listening for real.
        case ready
        /// The user started speaking; nothing decided yet.
        case hearing
        /// A finished turn the manager heard and stayed silent on.
        case listening
        /// A finished turn the manager was addressed by; `intent` says what.
        case addressed
        /// The manager, or a session on its behalf, is about to speak.
        case speaking
        /// The manager's own voice stopped.
        case quiet
        /// The child's source changed; it is about to exit 75 for a restart.
        case reloading
        /// A session took the stage.
        case stage
        /// The manager asks the app to play a cue by name.
        case earcon
        /// A door the manager walked through: `tbase send`, `open …://hear`.
        case tool
        /// Something failed, with its reason; the manager said a fixed line.
        case error
        /// Hosted: nobody spoke for `secs`; the bot is ending the session itself.
        case idle
        /// Hosted: the session's life (`secs`) is up; the bot ends it with
        /// nothing open, and the app opens a fresh one.
        case rotate
        /// One line of the exchange, whole, with its role and kind (hf-20,
        /// hf-26). The ledger records it; the orb has nothing to show for it.
        case said
        /// A turn addressed to a right-hand by name ("Yobi1, …"), handed to
        /// the app (25 Sep): `session` is the hand's, `text` the words, `name`
        /// the hand's. The app asks the hand's brain and speaks the answer on
        /// that hand's card. Sent only to a host that sets TB_RIGHT_HAND_CARDS.
        case ask
        /// A right-hand's answer, asked by the manager, for the app to speak
        /// on that hand's card (25 Sep): `session`, `name`, `text`. The
        /// manager mutes its mic for the line's length; the card's voice is
        /// echo there.
        case answer
        /// What Director's card shows with the sentence that follows (M26):
        /// `card` is the payload as a JSON string (`CardPayload`), empty to clear.
        case card
    }
    public var event: Kind
    public var t: Double?
    public var p: Double?
    public var intent: String?
    public var text: String?
    public var session: String?
    public var goal: String?
    public var project: String?
    public var name: String?
    public var voice: String?
    public var rung: String?
    public var ms: Int?
    public var meaning: String?
    public var reason: String?
    public var secs: Int?
    public var card: String?

    public static func parse(_ line: Data) -> ManagerEvent? {
        try? JSONDecoder().decode(ManagerEvent.self, from: line)
    }
}

public enum ManagerConfig {
    /// The command that starts the manager, from `~/.claude/hq.json`
    /// (`manager.command`, an argv array) or the default checkout beside the
    /// app's own. A path in config is a path the user typed; nothing here
    /// invents one.
    /// A command the user typed into `hq.json`, or nil. Distinct from
    /// `command()`, which falls back to the default checkout: a hosted
    /// manager is chosen only when nothing local was asked for.
    public static func explicitCommand(config: URL = HubApp.configPath) -> [String]? {
        if let own = localCommand() { return own }
        if let data = try? Data(contentsOf: config),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let manager = obj["manager"] as? [String: Any],
           let argv = manager["command"] as? [String], !argv.isEmpty {
            return argv
        }
        return nil
    }

    /// The `tbase` a hosted bot's door requests run: `manager.tbase` in
    /// `hq.json`, else the default checkout's debug build beside the app's own.
    public static func tbasePath(config: URL = HubApp.configPath) -> String {
        if let data = try? Data(contentsOf: config),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let manager = obj["manager"] as? [String: Any],
           let path = manager["tbase"] as? String, !path.isEmpty {
            return (path as NSString).expandingTildeInPath
        }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return "\(home)/Projects/voice-controlled-coding-agents/.build/arm64-apple-macosx/debug/tbase"
    }

    /// What HANDS-FREE would do if pressed.
    ///
    /// In order: a `manager.command` in hq.json is the developer's own bot and
    /// always wins; a signed-in Mac buys a session from the Gateway, which is
    /// the path every other user has; `manager.hosted` is the dev shim that
    /// starts a Cloud session with a public key on this Mac and goes when
    /// nobody needs it; the default checkout's `run.sh` on disk is a local
    /// manager; and with none of them the placard reads SET UP HANDS-FREE and
    /// a press says why.
    public enum Availability: Equatable, Sendable { case local, managed, hosted, unset }

    public static func availability(
        config: URL = HubApp.configPath,
        fileExists: (String) -> Bool = { FileManager.default.isExecutableFile(atPath: $0) },
        signedIn: () -> Bool = { HubApp.baseURL != nil && !(Secrets.read(.hubToken) ?? "").isEmpty }
    ) -> Availability {
        if explicitCommand(config: config) != nil { return .local }
        if signedIn() { return .managed }
        if hostedIsConfigured(config: config) { return .hosted }
        return fileExists(command(config: config)[0]) ? .local : .unset
    }

    static func hostedIsConfigured(config: URL) -> Bool {
        guard let data = try? Data(contentsOf: config),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let manager = obj["manager"] as? [String: Any],
              let hosted = manager["hosted"] as? [String: Any],
              let start = hosted["start"] as? String, !start.isEmpty,
              let key = hosted["key"] as? String, !key.isEmpty else { return false }
        return true
    }

    /// The WebRTC media path, the one that lets the manager be interrupted
    /// while it speaks. `manager.webrtc` in hq.json, with the offer URL of a
    /// bot that speaks SmallWebRTC; absent, hands-free uses the WebSocket it
    /// always has. Nothing else about the panel changes: both transports show
    /// the same event lines and answer the same doors.
    public struct WebRTCManager: Sendable, Equatable {
        /// Where a session is started (`POST /start` on the hosted agent).
        public let start: URL
        /// The public key for that agent, until the Gateway issues these too.
        public let key: String
    }

    public static func webrtc(config: URL = HubApp.configPath) -> WebRTCManager? {
        guard let data = try? Data(contentsOf: config),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let manager = obj["manager"] as? [String: Any],
              let rtc = manager["webrtc"] as? [String: Any],
              let start = (rtc["start"] as? String).flatMap(URL.init(string:)),
              let key = rtc["key"] as? String, !key.isEmpty else { return nil }
        return WebRTCManager(start: start, key: key)
    }

    public static func command(config: URL = HubApp.configPath) -> [String] {
        if let own = localCommand() { return own }
        if let data = try? Data(contentsOf: config),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let manager = obj["manager"] as? [String: Any],
           let argv = manager["command"] as? [String], !argv.isEmpty {
            return argv
        }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return ["\(home)/Projects/voice-controlled-coding-agents/tb-voice/server/run.sh"]
    }

    /// The child's environment: the user's, plus the marker that tells the
    /// bot the app is hosting it (so the app plays the cues, not the bot) and
    /// a PATH that can find `uv`, `open`, and `tbase`.
    public static func environment(base: [String: String] = ProcessInfo.processInfo.environment,
                                   scheme: String = AppIdentity.urlScheme) -> [String: String] {
        var env = base
        env["TB_HOST"] = "app"
        // The lane that started the child is the lane its links must reach
        // (23 Sep): Dev hands over `tbdev`, Prod `tranquilitybase`. A launcher
        // script that overwrites TB_URL_SCHEME inside the child still wins,
        // which is the one place left for a link to go to the wrong lane.
        env["TB_URL_SCHEME"] = scheme
        // This app speaks a right-hand's answer on the hand's own card, so
        // the manager hands it "Director, …", "Yobi1, …" (`ManagerEvent.ask`)
        // instead of answering in its own voice (25 Sep).
        env["TB_RIGHT_HAND_CARDS"] = "1"
        // Hands-free in Tranquility Base Director talks to Director by default
        // (25 Sep): every utterance goes to `director ask`, in one thread.
        if AppIdentity.channel == .director { env["TB_DEFAULT_INTERLOCUTOR"] = "director" }
        // An app with its own data folder hands it to the child, so the
        // `tbase` it runs reads this app's store and never Prod's (25 Sep).
        if AppIdentity.supportFolderName != nil {
            env["VOICE_DISPATCH_SUPPORT_DIR"] = QueueStore.supportDirectory.path
        }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let extra = ["\(home)/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
        env["PATH"] = (extra + (env["PATH"] ?? "").split(separator: ":").map(String.init)).joined(separator: ":")
        return env
    }

    /// How a LOCAL manager (a `manager.command` child) hears and speaks.
    ///
    /// `.child` is how it always was: the bot opens the Mac's microphone and
    /// speakers itself, with no echo canceller, so it mutes the microphone
    /// while it speaks and cannot be interrupted. `.webrtc` keeps the same
    /// child (same launcher, same event lines, same commands) and moves only
    /// the audio into this app's WebRTC engine on 127.0.0.1, the one a hosted
    /// manager already uses, which cancels the bot's voice out of the
    /// microphone so it can be talked over (tb-voice/server/local_rtc.py).
    ///
    /// This app's own setting, never hq.json: Prod reads hq.json too, and
    /// its bot must not change. `manager.json` in the app's own data folder,
    /// `{"audio": "webrtc"}`, honoured only by an app that has its own folder
    /// (Tranquility Base Director); anything else is `.child`.
    public enum LocalAudio: String, Sendable, Equatable { case child, webrtc }

    public static var localSettingsPath: URL {
        QueueStore.supportDirectory.appendingPathComponent("manager.json")
    }

    public static func localAudio(settings: URL = localSettingsPath,
                                  ownFolder: Bool = AppIdentity.supportFolderName != nil) -> LocalAudio {
        guard ownFolder,
              let data = try? Data(contentsOf: settings),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let raw = obj["audio"] as? String,
              let audio = LocalAudio(rawValue: raw.lowercased()) else { return .child }
        return audio
    }

    /// This app's own manager command (`command` in its manager.json, an argv
    /// array), ahead of hq.json's shared `manager.command`: a Director app can
    /// run a bot from a different checkout without touching what Prod starts.
    public static func localCommand(settings: URL = localSettingsPath,
                                    ownFolder: Bool = AppIdentity.supportFolderName != nil) -> [String]? {
        guard ownFolder,
              let data = try? Data(contentsOf: settings),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let argv = obj["command"] as? [String], !argv.isEmpty else { return nil }
        return argv
    }

    /// The child's extra environment when its audio is this app's WebRTC
    /// engine: where to answer the offer, and the token the offer carries so
    /// no other process on this Mac can take the microphone's place.
    public static func localWebRTCEnvironment(port: Int, token: String) -> [String: String] {
        ["TB_AUDIO": LocalAudio.webrtc.rawValue, "TB_WEBRTC_PORT": String(port), "TB_WEBRTC_TOKEN": token]
    }

    /// A port on 127.0.0.1 nobody is listening on right now, for the child's
    /// signalling server. Bound and released here; the child binds it a moment
    /// later, and a lost race shows as a failed start in the log.
    public static func freeLoopbackPort() -> Int? {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { return nil }
        defer { close(fd) }
        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = 0
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")
        let bound = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bound == 0 else { return nil }
        var len = socklen_t(MemoryLayout<sockaddr_in>.size)
        let named = withUnsafeMutablePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(fd, $0, &len) }
        }
        guard named == 0 else { return nil }
        return Int(UInt16(bigEndian: addr.sin_port))
    }
}
