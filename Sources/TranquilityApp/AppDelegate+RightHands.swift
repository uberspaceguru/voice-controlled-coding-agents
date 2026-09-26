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
        // One ask at a time per hand (26 Sep): a hand reopened while its brain
        // is still answering draws its card and says its own summary, and
        // asks nothing on top of the answer in flight.
        let asking = hand.asks && RightHands.BrainAsks.shared.begin(id) == .go
        if hand.asks, !asking {
            Permissions.log("right-hands: \(hand.name ?? "Director") is still answering; opened without asking")
        }
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
            if asking, card?.panelLines.isEmpty == false || card?.panelSummary == nil,
               case .success(let line) = RightHands.ask(
                hand, text: "what needs me?", conversation: id, session: id) {
                said = line
            }
            let line = card?.panelSummary ?? said
            RightHands.CardCache.shared.putSaid(line, for: id)
            await MainActor.run { [weak self] in
                if asking { self?.finishBrainAsk(id) }
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
    /// More). Nil when it is not a brain hand's card; otherwise the
    /// decision, for the gesture's analytics.
    ///
    /// More continues the card's own conversation (26 Sep, session audit
    /// "duplicate turn"): "go on", in the card's thread, where Director keeps
    /// the numbered list and its last turns. It used to ask a fixed "what
    /// needs me?", which answered a different question from the one the card
    /// had just asked ("Want to hear the rest?"). A press while the brain is
    /// still answering is acknowledged and asks nothing.
    func moreOnBrainCard() -> String? {
        guard hud.state.isCardOnStage, let id = hud.currentTarget?.sessionId,
              let hand = RightHands.hand(for: id), hand.asks else { return nil }
        let name = hand.name ?? "Director"
        if hud.face.placardOverride == RightHands.testSummonsPlacard {
            // A test's answer was asked in a thread of its own; "go on" in
            // the card's thread would continue something else.
            Permissions.log("right-hands: \(name) more on a test summons card; not asked")
            hud.acknowledge(.registered)
            return "brain_more_test_card"
        }
        return askBrain(RightHands.moreRequest, of: id, name: name) == .go ? "brain_more" : "brain_busy"
    }

    /// Ask the hand's brain and speak its answer on its card. Off the main
    /// thread: the brain is a subprocess.
    ///
    /// One ask at a time per hand (26 Sep): while one is in flight, a gesture
    /// or a tap is acknowledged (blue: received, not acted on) and asks
    /// nothing; `queueIfBusy` keeps words said to the hand, asked after the
    /// answer in flight rather than on top of it.
    @discardableResult
    func askBrain(_ text: String, of id: String, name: String, fallback: String? = nil,
                  queueIfBusy: Bool = false) -> RightHands.BrainAsks.Admission? {
        guard let hand = RightHands.hand(for: id), hand.asks else { return nil }
        let admission = RightHands.BrainAsks.shared.begin(id, queueing: queueIfBusy ? text : nil)
        switch admission {
        case .go:
            runBrainAsk(text, of: id, hand: hand, name: name, fallback: fallback)
        case .queued:
            Permissions.log("right-hands: \(name) is still answering; queued: \(text.prefix(80))")
        case .busy:
            Permissions.log("right-hands: \(name) is still answering; ignored: \(text.prefix(80))")
            hud.acknowledge(.registered)
        }
        return admission
    }

    /// The ask itself, with the hand already claimed. The card on stage for
    /// this hand says it is asking at once, so a press never looks lost
    /// while the brain thinks.
    private func runBrainAsk(_ text: String, of id: String, hand: RightHands.Hand, name: String,
                             fallback: String?) {
        Permissions.log("right-hands: asking \(name): \(text.prefix(80))")
        let working = hud.showCardWorking(for: id, "\(StateLegend.Glyph.quiet) ASKING \(name.uppercased())…")
        Task.detached(priority: .userInitiated) { [weak self] in
            let result = RightHands.ask(hand, text: text, conversation: id, session: id)
            await MainActor.run { [weak self] in
                if let working { self?.hud.endCardWorking(working) }
                defer { self?.finishBrainAsk(id) }
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

    /// The ask in flight for this hand has answered: release it, or ask the
    /// words that were said to it meanwhile.
    private func finishBrainAsk(_ id: String) {
        guard let next = RightHands.BrainAsks.shared.finish(id) else { return }
        guard let hand = RightHands.hand(for: id), hand.asks else {
            // The roster changed under the queue; nothing can be asked.
            while RightHands.BrainAsks.shared.finish(id) != nil {}
            return
        }
        runBrainAsk(next, of: id, hand: hand, name: hand.name ?? "Right-hand", fallback: nil)
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
        Permissions.log("summons: \(s.test ? "TEST " : "")to \(s.to) from \(s.app ?? "-"): \(s.text.prefix(80))")
        if s.test {
            testSummons(s, hands: hands, id: id(named:))
            return
        }
        if s.to == "yobi1" {
            guard let yobi = id(named: "Yobi1") else { return }
            let running = !NSRunningApplication.runningApplications(withBundleIdentifier: "io.yobi.yobi1.fable").isEmpty
            guard running else {
                speakOnCard("Yobi1 isn't running, so I couldn't pass that on.", as: yobi, name: "Yobi1", force: true)
                return
            }
            // His own words: they wait behind an answer in flight, never refused.
            askBrain(s.text, of: yobi, name: "Yobi1", queueIfBusy: true)
            return
        }
        guard let director = id(named: "Director") else { return }
        let thread = hands.hands[director]?.session ?? director
        // A summons is Ahmed's own words, so it is never refused; it claims
        // Director when it is free, so a ⌃⌃ while it is answered asks nothing.
        let claimed = RightHands.BrainAsks.shared.begin(director) == .go
        if !claimed { Permissions.log("summons: Director is already answering; asked anyway") }
        hud.showResult("Asking Director…")
        Task.detached(priority: .userInitiated) { [weak self] in
            let result = RightHands.summon(s, thread: thread)
            await MainActor.run { [weak self] in
                guard let self else { return }
                if claimed { self.finishBrainAsk(director) }
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

    /// A test summons (`test=1`, 26 Sep): never spoken, never in the card's
    /// own thread. Director is asked in a thread beside the card's
    /// (`RightHands.testThread`), so the answer does not become the turn
    /// "go on" continues; Yobi1 is not asked at all (its brain has no thread
    /// of its own to keep a test out of). What came back is logged and shown
    /// under the TEST SUMMONS placard, unless the panel is busy: then the
    /// log is the whole record, so a test never talks over or covers a real
    /// card.
    private func testSummons(_ s: DeepLink.Summons, hands: RightHands.Resolved,
                             id: (String) -> String?) {
        let name = s.to == "yobi1" ? "Yobi1" : "Director"
        guard let hand = id(name) else {
            Permissions.log("summons: TEST for \(name), who is not on the roster; dropped")
            return
        }
        guard s.to != "yobi1" else {
            showTestSummons("Test summons for Yobi1, not passed on: \(s.text)", as: hand, name: name)
            return
        }
        let thread = RightHands.testThread(hands.hands[hand]?.session ?? hand)
        Task.detached(priority: .utility) { [weak self] in
            let result = RightHands.summon(s, thread: thread)
            await MainActor.run { [weak self] in
                switch result {
                case .success(let line) where !line.isEmpty:
                    Permissions.log("summons: TEST answered (not spoken): \(line.prefix(120))")
                    self?.showTestSummons(line, as: hand, name: name)
                case .success:
                    Permissions.log("summons: TEST, Director chose silence")
                    self?.showTestSummons("Test summons: Director chose silence.", as: hand, name: name)
                case .failure(let why):
                    Permissions.log("summons: TEST, Director did not answer: \(why)")
                    self?.showTestSummons("Test summons: Director didn't answer.", as: hand, name: name)
                }
            }
        }
    }

    /// A test summons on the hand's card, labelled and silent.
    private func showTestSummons(_ text: String, as id: String, name: String) {
        guard !hud.isCapturingAudio, !hud.state.ownsStage, coordinator?.speech.isSpeaking != true else {
            Permissions.log("summons: TEST not shown; the panel is in use")
            return
        }
        let spoken = SpokenTextSanitizer().sanitize(
            String(text.prefix(1200)),
            allowing: SpokenTextSanitizer.speakableTerms(in: text).union([name]))
        returnToGridWork?.cancel()
        let shown = hud.showAnnouncement(spoken: spoken, sessionId: id, pid: nil, project: name,
                                         cwd: nil, eventId: id, placard: RightHands.testSummonsPlacard)
        Permissions.log("summons: TEST \(shown ? "shown on \(name)'s card, silent" : "not shown; the stage refused it")")
    }

    /// The hands' cards, refreshed in the background when they are stale.
    func refreshHandCards(_ hands: [String: RightHands.Hand]) {
        let carded = hands.filter { $0.value.hasCard }
        guard !carded.isEmpty else { return }
        Task.detached(priority: .utility) { RightHands.CardCache.shared.refresh(carded) }
    }
}

/// The card's short "working" state (26 Sep): while a hand's brain is asked
/// from its own card, the card's placard says so, so a ⌃⌃ or a tap is
/// visibly in hand before the answer arrives. Only the placard changes: the
/// words on the card, its target and its doors stay as they were.
extension StatusHUD {
    struct CardWorking {
        let label: String
        let previous: String
    }

    /// Nil when this hand's card is not on stage.
    func showCardWorking(for sessionId: String, _ label: String) -> CardWorking? {
        guard state.isCardOnStage, currentTarget?.sessionId == sessionId else { return nil }
        let working = CardWorking(label: label, previous: face.placardOverride)
        face.placardOverride = label
        render()
        return working
    }

    /// Put the placard back, unless the card has moved on since.
    func endCardWorking(_ working: CardWorking) {
        guard face.placardOverride == working.label else { return }
        face.placardOverride = working.previous
        render()
    }
}
