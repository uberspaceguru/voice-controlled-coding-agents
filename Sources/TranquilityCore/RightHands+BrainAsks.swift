import Foundation

extension RightHands {

    /// What ⌃⌃ ("More") on a brain hand's card asks (26 Sep, the session
    /// audit's duplicate turn). It used to ask a fixed "what needs me?",
    /// which answered a different question from the one the card had just
    /// put ("Five things ready … Want to hear the rest?"). Asked in the
    /// card's own thread, where Director keeps the numbered list and the last
    /// turns, "go on" continues whatever the card was saying.
    public static let moreRequest = "go on"

    /// The placard a test summons wears (`tbdirector://summon?…&test=1`). A
    /// test is shown, labelled, and never spoken: on 25 Sep another agent's
    /// test summons talked on Ahmed's speakers and he could not tell where it
    /// came from.
    public static let testSummonsPlacard = "TEST SUMMONS"

    /// The thread a test summons is asked in: beside the card's own, never in
    /// it, so a test's answer is not the turn "go on" continues.
    public static func testThread(_ thread: String) -> String { thread + ":test" }

    /// One brain ask at a time per hand (26 Sep, session audit "duplicate
    /// turn"). Two ⌃⌃ presses 1.9 s apart started two `director ask`s on one
    /// thread, and the second answer cut the first off three seconds into it.
    ///
    /// A gesture or a tap that arrives while an ask is in flight is refused
    /// (`.busy`): the caller acknowledges the press and asks nothing. Words
    /// said to a hand are not dropped: they wait (`.queued`) and are handed
    /// back by `finish`, in order, so they are asked after the answer in
    /// flight instead of on top of it.
    public final class BrainAsks: @unchecked Sendable {
        public enum Admission: Equatable, Sendable {
            /// Nothing in flight for this hand: ask, then call `finish`.
            case go
            /// An ask is in flight; the words wait for it.
            case queued
            /// An ask is in flight; nothing is asked.
            case busy
        }

        public static let shared = BrainAsks()
        private let lock = NSLock()
        private var inFlight = Set<String>()
        private var waiting: [String: [String]] = [:]

        public init() {}

        /// Claim the hand for one ask. `queueing` is the words to keep when it
        /// is already claimed; nil refuses instead.
        public func begin(_ hand: String, queueing text: String? = nil) -> Admission {
            lock.lock(); defer { lock.unlock() }
            guard inFlight.contains(hand) else {
                inFlight.insert(hand)
                return .go
            }
            guard let text else { return .busy }
            waiting[hand, default: []].append(text)
            return .queued
        }

        /// The ask in flight has answered. Returns the next words waiting for
        /// this hand, if any, with the hand still claimed for them: the caller
        /// asks them now and calls `finish` again. Nil releases the hand.
        public func finish(_ hand: String) -> String? {
            lock.lock(); defer { lock.unlock() }
            if var queue = waiting[hand], !queue.isEmpty {
                let next = queue.removeFirst()
                waiting[hand] = queue.isEmpty ? nil : queue
                return next
            }
            inFlight.remove(hand)
            return nil
        }

        public func isAsking(_ hand: String) -> Bool {
            lock.lock(); defer { lock.unlock() }
            return inFlight.contains(hand)
        }
    }
}
