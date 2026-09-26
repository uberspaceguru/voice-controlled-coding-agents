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
            // The "what needs me?" ask binds Director's numbered list for this
            // thread, so it is asked whenever the card carries that list (or
            // has no sentence of its own). What is SPOKEN is the card's own
            // summary first (25 Sep, tb-indicators): it counts exactly the
            // filled lines drawn below, so the ear and the dots agree.
            var said: String?
            if hand.asks, card?.panelLines.isEmpty == false || card?.panelSummary == nil,
               case .success(let line) = RightHands.ask(
                hand, text: "what needs me?", conversation: id, session: id) {
                said = line
            }
            let line = card?.panelSummary ?? said
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
            let lines = RightHands.Accordion.expanded(card)
            guard n >= 1, n <= lines.count else { return }
            let item = lines[n - 1]
            Permissions.log("right-hands: \(name) item \(n) (\(item.kind.rawValue): \(item.line)) opened")
            // Director's waiting lines are asked about by number: they are the
            // list Director numbered for this thread when the hand opened, and
            // it numbers five. Tap-to-explain (25 Sep): the hand explains the
            // item in plain words and it is then in focus in the thread, so a
            // "yes" said next answers it; if the hand cannot answer, the card
            // reads the line instead of going quiet.
            if !card.panelLines.isEmpty, item.kind == .waiting, n <= RightHands.Accordion.most,
               RightHands.hand(for: parent)?.asks == true {
                askBrain(RightHands.Accordion.explainRequest(number: n), of: parent, name: name,
                         fallback: item.line)
                return
            }
            // Anything else is read as drawn: a ready or blocked line, a sixth
            // waiting line Director never numbered, a line from a hand's own
            // status. Asking a brain about a line it did not write guesses.
            speakOnCard(item.line, as: parent, name: name)
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
    func speakOnCard(_ text: String, as id: String, name: String, force: Bool = false) {
        let spoken = SpokenTextSanitizer().sanitize(
            String(text.prefix(1200)),
            allowing: SpokenTextSanitizer.speakableTerms(in: text).union([name]))
        speakForManager(session: id, spoken: spoken, placard: name.uppercased(), force: force)
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

    /// A summons from the Whisper key (25 Sep, SPEC-summons-20260925): the hand
    /// it names answers, and the answer is spoken on that hand's card. Director
    /// is asked on channel summons with the context, in its card's thread, so a
    /// follow-up by hands-free or a tap binds to the same conversation. Yobi1
    /// has no typed-input door this app can reach: while it runs, its brain is
    /// asked the same way a tap asks it; when it does not, the card says so.
    func summon(_ s: DeepLink.Summons) {
        guard let hands = RightHands.current() else {
            Permissions.log("summons: no right-hands on this Mac; dropped")
            return
        }
        func id(named name: String) -> String? {
            hands.order.first { (hands.names[$0] ?? "").caseInsensitiveCompare(name) == .orderedSame }
        }
        Permissions.log("summons: to \(s.to) from \(s.app ?? "-"): \(s.text.prefix(80))")
        if s.to == "yobi1" {
            guard let yobi = id(named: "Yobi1") else { return }
            let running = !NSRunningApplication.runningApplications(withBundleIdentifier: "io.yobi.yobi1.fable").isEmpty
            guard running else {
                speakOnCard("Yobi1 isn't running, so I couldn't pass that on.", as: yobi, name: "Yobi1", force: true)
                return
            }
            askBrain(s.text, of: yobi, name: "Yobi1")
            return
        }
        guard let director = id(named: "Director") else { return }
        let thread = hands.hands[director]?.session ?? director
        hud.showResult("Asking Director…")
        Task.detached(priority: .userInitiated) { [weak self] in
            let result = RightHands.summon(s, thread: thread)
            await MainActor.run { [weak self] in
                guard let self else { return }
                switch result {
                case .success(let line) where !line.isEmpty:
                    Permissions.log("summons: Director answered: \(line.prefix(120))")
                    self.speakOnCard(line, as: director, name: "Director", force: true)
                case .success:
                    Permissions.log("summons: Director chose silence")
                case .failure(let why):
                    Permissions.log("summons: Director did not answer: \(why)")
                    self.hud.showResult("Director didn't answer just now.")
                }
            }
        }
    }

    /// The hands' cards, refreshed in the background when they are stale.
    func refreshHandCards(_ hands: [String: RightHands.Hand]) {
        let carded = hands.filter { $0.value.hasCard }
        guard !carded.isEmpty else { return }
        Task.detached(priority: .utility) { RightHands.CardCache.shared.refresh(carded) }
    }
}
