import AppKit
import TranquilityCore

/// The right-hands panel, app half (24 Sep, RIGHT-HANDS-UX.md): a tap on a
/// hand with a card opens it like an accordion and says one sentence; a tap on
/// one of its lines opens a card named for the hand, with that line in focus,
/// so the next thing said goes to the hand's brain; More asks the brain. The
/// rows themselves are built in Core (`GridAssembler.handRow`,
/// `RightHands.Accordion`); this file only answers taps and speaks.
extension AppDelegate {

    /// Every row tap that means "open this" lands here first. Anything that is
    /// not a right-hand or one of its lines goes on to `announceNext(only:)`
    /// exactly as before.
    func pick(_ id: String) {
        if let (parent, part) = RightHands.Accordion.part(of: id) {
            openLine(part, of: parent)
            return
        }
        if let hand = RightHands.hand(for: id), hand.hasCard {
            toggleHand(id, hand: hand)
            return
        }
        if RightHands.hand(for: id)?.isPlaceholder == true {
            hud.showResult("\(RightHands.hand(for: id)?.name ?? "That agent") isn't running yet.")
            return
        }
        announceNext(only: id)
    }

    /// Open or close a hand. Opening reads its card fresh (off the main
    /// thread), draws its lines, and says the one sentence: the count and the
    /// first thing, subject named. Closing is silent.
    private func toggleHand(_ id: String, hand: RightHands.Hand) {
        if expandedHand == id {
            expandedHand = nil
            expandedAll = false
            Permissions.log("right-hands: \(hand.name ?? id.prefix(8).description) closed")
            showIdleGrid()
            return
        }
        expandedHand = id
        expandedAll = false
        Permissions.log("right-hands: \(hand.name ?? id.prefix(8).description) opened")
        Task.detached(priority: .userInitiated) { [weak self] in
            let card = RightHands.card(for: hand)
            if let card { RightHands.CardCache.shared.put(card, for: id) }
            // Director's own sentence (25 Sep): asked in the card's thread, which
            // also renumbers the list "number 2" binds to, and shown and spoken
            // exactly as it came back. Never a summary of it.
            var said: String?
            if hand.asks, case .success(let line) = RightHands.ask(
                hand, text: "what needs me?", conversation: id, session: id) {
                said = line
            }
            let line = said ?? card?.panelSummary
            RightHands.CardCache.shared.putSaid(line, for: id)
            await MainActor.run { [weak self] in
                guard let self, self.expandedHand == id else { return }
                self.showIdleGrid()
                guard let line else {
                    self.hud.showResult("\(hand.name ?? "Director") didn't answer just now.")
                    return
                }
                self.speakOverGrid(line, as: id, name: hand.name ?? "Director")
            }
        }
    }

    /// A line inside an open hand. An item opens the hand's card with that
    /// item said in plain words, and makes the hand the reply target, so an
    /// answer spoken or typed next goes to its brain. More, and the summary,
    /// ask the brain for the whole list.
    private func openLine(_ part: RightHands.Accordion.Part, of parent: String) {
        let name = RightHands.hand(for: parent)?.name ?? "Director"
        switch part {
        case .item(let n):
            guard let card = RightHands.CardCache.shared.card(for: parent) else { return }
            let lines = RightHands.Accordion.lines(card)
            guard n >= 1, n <= lines.count else { return }
            Permissions.log("right-hands: \(name) item \(n) (\(lines[n - 1])) opened")
            // Director's own lines are asked about by number: they are the
            // list Director numbered for this thread when the hand opened.
            if !card.panelLines.isEmpty, RightHands.hand(for: parent)?.asks == true {
                askBrain(RightHands.Accordion.explainRequest(number: n), of: parent, name: name,
                         fallback: lines[n - 1])
                return
            }
            guard n <= card.projects.count else { return }
            let project = card.projects[n - 1]
            // Tap-to-explain (25 Sep): the hand explains the item in plain
            // words, and the item is then in focus in its thread, so a "yes"
            // said next answers it. If the hand cannot answer, the card reads
            // the item's own line instead of going quiet.
            if RightHands.hand(for: parent)?.asks == true {
                askBrain(RightHands.Accordion.explainRequest(project), of: parent, name: name,
                         fallback: RightHands.Accordion.itemSentence(project))
            } else {
                speakOnCard(RightHands.Accordion.itemSentence(project), as: parent, name: name)
            }
        case .more:
            // More shows more (25 Sep: a button that reveals the rest of the
            // list, up to what Director numbers), and says nothing.
            expandedAll = true
            Permissions.log("right-hands: \(name) more")
            showIdleGrid()
        case .summary:
            askBrain("what needs me?", of: parent, name: name)
        }
    }

    /// ⌃⌃ on a hand's card is More (ruling 5: a card that speaks offers
    /// More). True when it was handled here.
    func moreOnBrainCard() -> Bool {
        guard hud.state.isCardOnStage, let id = hud.currentTarget?.sessionId,
              let hand = RightHands.hand(for: id), hand.asks else { return false }
        askBrain("what needs me?", of: id, name: hand.name ?? "Director")
        return true
    }

    /// Ask the hand's brain and speak its answer on its card. Off the main
    /// thread: the brain is a subprocess.
    func askBrain(_ text: String, of id: String, name: String, fallback: String? = nil) {
        guard let hand = RightHands.hand(for: id), hand.asks else { return }
        Permissions.log("right-hands: asking \(name): \(text.prefix(80))")
        Task.detached(priority: .userInitiated) { [weak self] in
            let result = RightHands.ask(hand, text: text, conversation: id, session: id)
            await MainActor.run { [weak self] in
                switch result {
                case .success(let line): self?.speakOnCard(line, as: id, name: name)
                case .failure(let why):
                    Permissions.log("right-hands: \(name) did not answer: \(why)")
                    if let fallback { self?.speakOnCard(fallback, as: id, name: name) }
                    else { self?.hud.showResult("\(name) didn't answer just now.") }
                }
            }
        }
    }

    /// A line on a card named for the hand (ruling 7), in its voice.
    func speakOnCard(_ text: String, as id: String, name: String) {
        let spoken = SpokenTextSanitizer().sanitize(
            String(text.prefix(1200)),
            allowing: SpokenTextSanitizer.speakableTerms(in: text).union([name]))
        speakForManager(session: id, spoken: spoken, placard: name.uppercased())
    }

    /// The opening sentence, spoken while the grid stays up, so the lines it
    /// introduces are right there to tap.
    private func speakOverGrid(_ text: String, as id: String, name: String) {
        guard let coordinator else { return }
        let spoken = SpokenTextSanitizer().sanitize(
            text, allowing: SpokenTextSanitizer.speakableTerms(in: text).union([name]))
        let voices = coordinator.voices(for: id)
        let previous = announceTask
        announceTask = Task { @MainActor in
            coordinator.speech.stop()
            previous?.cancel()
            _ = await previous?.value
            guard !Task.isCancelled else { return }
            _ = await coordinator.speech.speak(spoken, voice: voices.cloud,
                                               systemVoice: voices.system, onWord: { _ in })
        }
    }

    /// The hands' cards, refreshed in the background when they are stale.
    func refreshHandCards(_ hands: [String: RightHands.Hand]) {
        let carded = hands.filter { $0.value.hasCard }
        guard !carded.isEmpty else { return }
        Task.detached(priority: .utility) { RightHands.CardCache.shared.refresh(carded) }
    }
}
