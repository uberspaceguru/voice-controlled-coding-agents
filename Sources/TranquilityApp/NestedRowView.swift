import AppKit
import TranquilityCore

/// A line inside an open right-hand (the Director accordion), drawn as what it
/// is: part of the row above, not an agent of its own (25 Sep, Ahmed: "the
/// expanded items look like agent rows").
///
/// - indented under the hand's name, in smaller type;
/// - no status lamp: an item wears one small glyph for what it is (25 Sep,
///   tb-indicators): ● waiting on you (the hand's green, and the only kind
///   the summary counts), ◆ ready for you to look at, ◌ blocked on another
///   agent; a line that is only moving keeps the chevron. The summary wears
///   nothing;
/// - the summary is Director's own sentence, full width, up to two lines;
/// - an item is one line, cut at a word;
/// - "more…" is a button, not a row (`MoreButton` below).
///
/// Taps go through the same action as a grid row (`sessionRowTapped`), which
/// reads the row id off `identifier`, so every line lands in
/// `AppDelegate.pick` and nothing here decides what a line does.
final class NestedRowView: NSControl {
    enum Kind { case summary, item }

    static let indent: CGFloat = GridRowView.lampColumn
    static let font = ChromeType.mono(ofSize: 12, weight: .regular)
    static let itemHeight: CGFloat = 28

    private let label: NSTextField
    private let resting: NSColor
    private let kind: Kind
    /// The whole line, kept so a resize can cut it again from the start.
    private let fullText: String

    init(item: SessionRow, kind: Kind, target: AnyObject, action: Selector) {
        label = NSTextField(wrappingLabelWithString: item.name)
        resting = kind == .summary ? StateLegend.Palette.secondary : StateLegend.Palette.ink
        self.kind = kind
        fullText = item.name
        super.init(frame: .zero)
        self.target = target
        self.action = action
        identifier = NSUserInterfaceItemIdentifier(item.id)
        translatesAutoresizingMaskIntoConstraints = false
        toolTip = kind == .item ? Self.glyph(for: item.lamp).meaning + ": " + item.name : (item.detail ?? item.name)

        label.font = Self.font
        label.textColor = resting
        label.isSelectable = false
        label.drawsBackground = false
        label.translatesAutoresizingMaskIntoConstraints = false
        // Cut at a WORD, never mid-word: word wrapping with a line limit and
        // the last visible line truncated is AppKit's word-boundary cut.
        label.lineBreakMode = .byWordWrapping
        label.cell?.truncatesLastVisibleLine = true
        label.maximumNumberOfLines = kind == .summary ? 2 : 1
        label.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        addSubview(label)

        var constraints = [
            label.trailingAnchor.constraint(equalTo: trailingAnchor),
            label.topAnchor.constraint(equalTo: topAnchor, constant: kind == .summary ? 6 : 5),
            label.bottomAnchor.constraint(equalTo: bottomAnchor, constant: kind == .summary ? -6 : -5),
        ]
        if kind == .item {
            // The item's glyph, where a lamp would be on an agent row.
            let mark = Self.glyph(for: item.lamp)
            let chevron = NSTextField(labelWithString: mark.glyph)
            chevron.font = mark.glyph == "›" ? Self.font : ChromeType.mono(ofSize: 9, weight: .regular)
            chevron.textColor = mark.ink
            chevron.setAccessibilityLabel(mark.meaning)
            chevron.translatesAutoresizingMaskIntoConstraints = false
            addSubview(chevron)
            constraints += [
                chevron.leadingAnchor.constraint(equalTo: leadingAnchor, constant: Self.indent),
                chevron.firstBaselineAnchor.constraint(equalTo: label.firstBaselineAnchor),
                label.leadingAnchor.constraint(equalTo: chevron.trailingAnchor, constant: 6),
                heightAnchor.constraint(greaterThanOrEqualToConstant: Self.itemHeight),
            ]
        } else {
            constraints += [
                label.leadingAnchor.constraint(equalTo: leadingAnchor, constant: Self.indent),
            ]
        }
        NSLayoutConstraint.activate(constraints)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("not used") }

    /// An item's kind arrives as its lamp (`RightHands.Accordion.lamp(for:)`).
    static func glyph(for lamp: Lamp) -> (glyph: String, ink: NSColor, meaning: String) {
        switch lamp {
        case .ready: return (StateLegend.Glyph.dot, StateLegend.Palette.ready, "Waiting on you")
        case .working: return ("◆", StateLegend.Palette.working, "Ready for you to look at")
        case .running: return (StateLegend.Glyph.quiet, StateLegend.Palette.hint, "Blocked on another agent")
        default: return (StateLegend.Glyph.forward, StateLegend.Palette.hint, "Moving")
        }
    }

    /// The width the label wraps at, once the row knows its own width; and an
    /// item, which is one line, is cut at its last whole word that fits.
    /// AppKit's own word-boundary cut applies only across lines: measured on
    /// the first render, a one-line item cut "send pri…" mid-word.
    override func layout() {
        super.layout()
        let width = label.frame.width
        if width > 0, label.preferredMaxLayoutWidth != width {
            label.preferredMaxLayoutWidth = width
        }
        if kind == .item, width > 0 {
            let cut = Self.cutAtWord(fullText, font: Self.font, width: width)
            if label.stringValue != cut { label.stringValue = cut }
        }
    }

    /// The longest prefix ending at a word, plus "…", that fits `width`; the
    /// whole text when it fits. A single word too long for the row is the
    /// only case cut inside a word, because there is no earlier boundary.
    static func cutAtWord(_ text: String, font: NSFont, width: CGFloat) -> String {
        func fits(_ s: String) -> Bool {
            (s as NSString).size(withAttributes: [.font: font]).width <= width
        }
        if fits(text) { return text }
        var words = text.split(separator: " ", omittingEmptySubsequences: true).map(String.init)
        while words.count > 1 {
            words.removeLast()
            var head = words.joined(separator: " ")
            while let last = head.last, ",;:-–—".contains(last) { head.removeLast() }
            if fits(head + "…") { return head + "…" }
        }
        return text
    }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        trackingAreas.forEach(removeTrackingArea)
        addTrackingArea(NSTrackingArea(rect: bounds, options: [.mouseEnteredAndExited, .activeAlways],
                                       owner: self, userInfo: nil))
        PointerCursor.track(self)
    }
    override func mouseEntered(with event: NSEvent) { label.textColor = StateLegend.hovered(resting) }
    override func mouseExited(with event: NSEvent) { label.textColor = resting }
    override func resetCursorRects() { super.resetCursorRects(); addCursorRect(bounds, cursor: .pointingHand) }
    override func cursorUpdate(with event: NSEvent) { PointerCursor.show() }
    override func mouseDown(with event: NSEvent) {}
    override func mouseUp(with event: NSEvent) {
        guard bounds.contains(convert(event.locationInWindow, from: nil)) else { return }
        sendAction(action, to: target)
    }
}

/// "more…" as a button (25 Sep): a small link in the hint ink under the
/// items, not a row with a rule and a hover band of its own.
final class MoreButton: NSButton {
    init(item: SessionRow, target: AnyObject, action: Selector) {
        super.init(frame: .zero)
        identifier = NSUserInterfaceItemIdentifier(item.id)
        self.target = target
        self.action = action
        isBordered = false
        setButtonType(.momentaryChange)
        attributedTitle = NSAttributedString(string: item.name, attributes: [
            .font: ChromeType.mono(ofSize: 11, weight: .regular),
            .foregroundColor: StateLegend.Palette.secondary,
        ])
        toolTip = "Show more of what needs you"
        translatesAutoresizingMaskIntoConstraints = false
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("not used") }

    override func resetCursorRects() { super.resetCursorRects(); addCursorRect(bounds, cursor: .pointingHand) }
}
