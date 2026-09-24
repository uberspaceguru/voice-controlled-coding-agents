import Foundation

/// A reply to a right-hand with a brain (`RightHands.Hand.ask`), delivered by
/// asking the brain rather than typing into a pane (24 Sep).
///
/// Director's card is a conversation with Director: what the user says to it
/// goes to `director ask "<text>" --channel tranquility --external-id <id>`,
/// and the line Director answers with is spoken back on the card. The words
/// are one argv element and never reach a shell.
///
/// The conversation id is the hand's own session id, so the card, the voice
/// manager (`tbase ask`) and every reply after the first are one thread in
/// Director's `conversation` table, and "the second one" binds to the list the
/// user was last read.
public struct BrainTransport: DispatchTransport {
    public let kind = TransportKind.remote
    public let hand: RightHands.Hand

    /// Where the answer goes. The app points it at the card's voice; nil in
    /// `tbase`, which prints instead.
    public nonisolated(unsafe) static var answered: (@Sendable (_ sessionId: String, _ name: String, _ line: String) -> Void)?

    public init(hand: RightHands.Hand) { self.hand = hand }

    public func readiness(for target: DispatchTarget) async -> Readiness { .ready }

    public func send(text: String, to target: DispatchTarget) async -> DispatchOutcome {
        let started = Date()
        let hand = self.hand
        let result = await Task.detached(priority: .userInitiated) {
            RightHands.ask(hand, text: text, conversation: target.sessionId, session: target.sessionId)
        }.value
        switch result {
        case .success(let line):
            Coordinator.trace?("brain: \(hand.name ?? target.sessionId.prefix(8).description) answered "
                + "in \(Int(Date().timeIntervalSince(started) * 1000)) ms")
            Self.answered?(target.sessionId, hand.name ?? "", line)
            return .confirmed(latencyMs: Int(Date().timeIntervalSince(started) * 1000))
        case .failure(.notABrain):
            return .failed(.targetGone)
        case .failure(.failed(let why)):
            Coordinator.trace?("brain: \(hand.name ?? "hand") failed: \(why.prefix(200))")
            return .failed(.injectionFailed(String(why.prefix(200))))
        }
    }
}
