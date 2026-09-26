import CoreAudio
import Darwin
import Foundation

/// Is another app playing sound? (tb-media-aware, 26 Sep 12:35: a video behind
/// Ahmed was transcribed onto the Director card.) Core Audio lists every
/// process with an audio client and whether its output is running; this app,
/// its children (the voice) and the right-hands that hold an engine open while
/// silent are not "other". While another app plays, hands-free is name-only:
/// the app writes `media.json` for the voice, which gates on it.
public enum OtherAudio {
    public struct Player: Equatable, Sendable {
        public var pid: pid_t
        public var bundle: String
    }

    /// Clients that keep output running while they say nothing: this app, and
    /// Yobi1 (a right-hand, whose own speech is not media).
    public static let ignoredBundles = ["com.robertnowell.voice-dispatch.director", "io.yobi.yobi1"]

    /// Every process playing output now, but ours and the ignored ones.
    public static func players(excluding pids: Set<pid_t>) -> [Player] {
        processObjects().compactMap { object -> Player? in
            guard uint32(object, kAudioProcessPropertyIsRunningOutput) != 0 else { return nil }
            let pid = pid_t(bitPattern: uint32(object, kAudioProcessPropertyPID))
            guard pid > 0, !pids.contains(pid) else { return nil }
            let bundle = bundleID(object) ?? ""
            guard !ignoredBundles.contains(where: { !bundle.isEmpty && bundle.hasPrefix($0) }) else { return nil }
            return Player(pid: pid, bundle: bundle)
        }
    }

    /// `pid` and everything it started, however deep.
    public static func family(of pid: pid_t) -> Set<pid_t> {
        var out: Set<pid_t> = [pid]
        var queue = [pid]
        while let next = queue.popLast() {
            var buffer = [pid_t](repeating: 0, count: 256)
            let n = Int(proc_listchildpids(next, &buffer, Int32(buffer.count * MemoryLayout<pid_t>.size)))
            for child in buffer.prefix(max(0, n)) where child > 0 && !out.contains(child) {
                out.insert(child)
                queue.append(child)
            }
        }
        return out
    }

    /// On once sound has run `onAfter` seconds without a break; off once it has
    /// been silent `offAfter` seconds. A notification chime changes nothing.
    public struct Debounce: Sendable {
        public var onAfter: TimeInterval = 2
        public var offAfter: TimeInterval = 4
        public private(set) var on = false
        private var since: Date?

        public init(onAfter: TimeInterval = 2, offAfter: TimeInterval = 4) {
            self.onAfter = onAfter
            self.offAfter = offAfter
        }

        /// Feed one sample; true when `on` changed.
        public mutating func update(_ active: Bool, now: Date = Date()) -> Bool {
            if active == on { since = nil; return false }
            let started = since ?? now
            since = started
            guard now.timeIntervalSince(started) >= (active ? onAfter : offAfter) else { return false }
            on = active
            since = nil
            return true
        }
    }

    /// What the voice reads (tb-voice director_link.media_playing): stale after 15 s.
    public static func stateJSON(playing: Bool, who: [String], at date: Date = Date()) -> Data {
        let object: [String: Any] = ["playing": playing, "who": who, "t": date.timeIntervalSince1970]
        return (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])) ?? Data()
    }

    // MARK: - Core Audio

    private static func processObjects() -> [AudioObjectID] {
        var address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyProcessObjectList,
                                                 mScope: kAudioObjectPropertyScopeGlobal,
                                                 mElement: kAudioObjectPropertyElementMain)
        var size: UInt32 = 0
        let system = AudioObjectID(kAudioObjectSystemObject)
        guard AudioObjectGetPropertyDataSize(system, &address, 0, nil, &size) == noErr, size > 0 else { return [] }
        var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
        guard AudioObjectGetPropertyData(system, &address, 0, nil, &size, &ids) == noErr else { return [] }
        return ids
    }

    private static func uint32(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector) -> UInt32 {
        var address = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
                                                 mElement: kAudioObjectPropertyElementMain)
        var value: UInt32 = 0
        var size = UInt32(MemoryLayout<UInt32>.size)
        return AudioObjectGetPropertyData(object, &address, 0, nil, &size, &value) == noErr ? value : 0
    }

    private static func bundleID(_ object: AudioObjectID) -> String? {
        var address = AudioObjectPropertyAddress(mSelector: kAudioProcessPropertyBundleID,
                                                 mScope: kAudioObjectPropertyScopeGlobal,
                                                 mElement: kAudioObjectPropertyElementMain)
        var value: Unmanaged<CFString>?
        var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        guard AudioObjectGetPropertyData(object, &address, 0, nil, &size, &value) == noErr,
              let string = value?.takeRetainedValue() else { return nil }
        return string as String
    }
}
