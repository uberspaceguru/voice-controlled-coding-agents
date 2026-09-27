import AppKit

/// Always there on the panel: type to Director (27 Sep, Ahmed: "aren't I supposed to have a text interface to
/// Director also?"). The panel never holds the keyboard on its own; a click here borrows it, and Return or Escape
/// hands it back, so typing meant for another app is never taken.
final class DirectorField: NSTextField, NSTextFieldDelegate {
    var onWantsKeyboard: (() -> Void)?
    var onSubmit: ((String) -> Void)?
    var onDone: (() -> Void)?

    init() {
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        isBordered = true
        isBezeled = true
        bezelStyle = .roundedBezel
        focusRingType = .none
        drawsBackground = true
        backgroundColor = NSColor.white.withAlphaComponent(0.06)
        font = StateLegend.Face.message(13)
        textColor = StateLegend.Palette.ink
        placeholderAttributedString = NSAttributedString(string: "Message Director\u{2026}", attributes: [
            .font: StateLegend.Face.message(13), .foregroundColor: StateLegend.Palette.hint])
        lineBreakMode = .byTruncatingHead
        maximumNumberOfLines = 1
        cell?.wraps = false
        cell?.isScrollable = true
        delegate = self
        heightAnchor.constraint(equalToConstant: 26).isActive = true
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("not used") }

    override func mouseDown(with event: NSEvent) {
        onWantsKeyboard?()
        super.mouseDown(with: event)
    }

    func control(_ control: NSControl, textView: NSTextView, doCommandBy selector: Selector) -> Bool {
        if selector == #selector(NSResponder.insertNewline(_:)) {
            let text = stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            stringValue = ""
            if !text.isEmpty { onSubmit?(text) }
            onDone?()
            return true
        }
        if selector == #selector(NSResponder.cancelOperation(_:)) {
            onDone?()
            return true
        }
        return false
    }
}
