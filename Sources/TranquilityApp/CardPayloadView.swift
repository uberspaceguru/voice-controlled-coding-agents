import AppKit
import TranquilityCore

/// What Director's card draws under its sentence (M26, 26 Sep, Ahmed: "it can
/// show me things as the agent talks"): the turn's `CardPayload`, in the
/// panel's own component style (the accordion's rows, its chips and its
/// doors). Built once per payload; the HUD shows it on the conversation card
/// and keeps it until the next turn replaces it.
@MainActor
enum CardPayloadRows {
    static let width: CGFloat = 348

    static func build(_ payload: CardPayload, target: AnyObject, action: Selector) -> [NSView] {
        var out: [NSView] = [rule()]
        switch payload {
        case .list(let title, let rows):
            if let title { out.append(placard(title)) }
            out += rows.map { listRow($0, target: target, action: action) }
        case .item(let item):
            out.append(itemView(item, target: target, action: action))
        case .action(let a):
            out.append(actionView(a, target: target, action: action))
        case .screen(let agent, let lines):
            out.append(screenView(agent: agent, lines: lines, target: target, action: action))
        }
        return out
    }

    // MARK: - The four components

    /// A list row: "1  YobiWispr · run this command?", and under it its age
    /// and Go to Agent.
    private static func listRow(_ row: CardPayload.Row, target: AnyObject, action: Selector) -> NSView {
        let line = NSMutableAttributedString()
        if let n = row.n { line.append(text("\(n)  ", StateLegend.Palette.hint)) }
        if let project = row.project { line.append(text("\(project) · ", StateLegend.Palette.secondary)) }
        line.append(text(row.title, StateLegend.Palette.ink))
        let title = label(line, lines: 1)
        var under: [NSView] = []
        if let age = row.age { under.append(chip(age, StateLegend.Palette.hint)) }
        if let agent = row.agent {
            under.append(ActionRowView.door("Go to Agent", id: CardPayload.Door.goTo(agent: agent).id,
                                            ink: StateLegend.Palette.secondary, target: target, action: action))
        }
        return block([title] + (under.isEmpty ? [] : [doors(under)]), spacing: 2)
    }

    /// An item: its subject, the full question, and the doors that are his.
    private static func itemView(_ item: CardPayload.Item, target: AnyObject, action: Selector) -> NSView {
        let head = NSMutableAttributedString()
        if let n = item.n { head.append(text("\(n)  ", StateLegend.Palette.hint)) }
        if let project = item.project { head.append(text("\(project) · ", StateLegend.Palette.secondary)) }
        head.append(text(item.title, StateLegend.Palette.secondary))
        var views: [NSView] = [label(head, lines: 1)]
        if let question = item.question, question != item.title {
            let q = label(NSAttributedString(string: question, attributes: [
                .font: StateLegend.Face.message(12), .foregroundColor: StateLegend.Palette.ink]), lines: 8)
            views.append(q)
        }
        var under: [NSView] = []
        switch item.reply {
        case .approve?:
            under.append(ActionRowView.door("Approve", id: CardPayload.Door.approve.id,
                                            ink: StateLegend.Palette.fault, target: target, action: action))
        case .answer? where item.agent != nil:
            under.append(ActionRowView.door("Answer", id: CardPayload.Door.answer(agent: item.agent!).id,
                                            ink: StateLegend.Palette.ready, target: target, action: action))
        default: break
        }
        if let agent = item.agent {
            under.append(ActionRowView.door("Go to Agent", id: CardPayload.Door.goTo(agent: agent).id,
                                            ink: StateLegend.Palette.secondary, target: target, action: action))
        }
        if !under.isEmpty { views.append(doors(under)) }
        return block(views, spacing: 4)
    }

    /// An action: what it is, its state chip, Approve while it waits on him.
    private static func actionView(_ a: PendingActions.Action, target: AnyObject, action: Selector) -> NSView {
        let lamp = RightHands.Accordion.actionLamp(a.state)
        let line = NSMutableAttributedString()
        line.append(text("\(StateLegend.Glyph.dot)  ", ActionRowView.ink(lamp)))
        line.append(text(a.what, lamp == .unlit ? StateLegend.Palette.secondary : StateLegend.Palette.ink))
        // A second line hangs under the words, not the dot.
        let hang = NSMutableParagraphStyle()
        hang.headIndent = text("\(StateLegend.Glyph.dot)  ", .clear).size().width
        line.addAttribute(.paragraphStyle, value: hang, range: NSRange(location: 0, length: line.length))
        var under: [NSView] = [chip(PendingActions.chip(a), ActionRowView.ink(lamp))]
        if a.state == .awaitingAhmed {
            under.append(ActionRowView.door("Approve", id: CardPayload.Door.approve.id,
                                            ink: StateLegend.Palette.fault, target: target, action: action))
        }
        if let agent = a.target {
            under.append(ActionRowView.door("Go to Agent", id: CardPayload.Door.goTo(agent: agent).id,
                                            ink: StateLegend.Palette.secondary, target: target, action: action))
        }
        return block([label(line, lines: 2), doors(under)], spacing: 3)
    }

    /// A screen: the pane's last lines, monospaced, one line each (never
    /// wrapped: a pane's columns mean something), under the agent's name.
    private static func screenView(agent: String?, lines: [String], target: AnyObject, action: Selector) -> NSView {
        var head: [NSView] = []
        if let agent {
            head.append(chip(agent, StateLegend.Palette.secondary))
            head.append(ActionRowView.door("Go to Agent", id: CardPayload.Door.goTo(agent: agent).id,
                                           ink: StateLegend.Palette.secondary, target: target, action: action))
        }
        let pane = NSStackView()
        pane.orientation = .vertical
        pane.alignment = .leading
        pane.spacing = 0
        pane.edgeInsets = NSEdgeInsets(top: 6, left: 8, bottom: 6, right: 8)
        pane.wantsLayer = true
        pane.layer?.backgroundColor = NSColor.black.withAlphaComponent(0.28).cgColor
        pane.layer?.cornerRadius = 4
        pane.translatesAutoresizingMaskIntoConstraints = false
        for l in lines {
            let row = label(NSAttributedString(string: l.isEmpty ? " " : l, attributes: [
                .font: ChromeType.mono(ofSize: 10.5, weight: .regular),
                .foregroundColor: StateLegend.Palette.secondary]), lines: 1, width: width - 16)
            pane.addArrangedSubview(row)
        }
        pane.widthAnchor.constraint(equalToConstant: width).isActive = true
        return block((head.isEmpty ? [] : [doors(head)]) + [pane], spacing: 4)
    }

    // MARK: - Parts

    private static func text(_ s: String, _ color: NSColor) -> NSAttributedString {
        NSAttributedString(string: s, attributes: [.font: NestedRowView.font, .foregroundColor: color])
    }

    private static func label(_ s: NSAttributedString, lines: Int, width: CGFloat = width) -> NSTextField {
        let l = NSTextField(labelWithAttributedString: s)
        l.maximumNumberOfLines = lines
        l.lineBreakMode = lines == 1 ? .byTruncatingTail : .byWordWrapping
        l.cell?.truncatesLastVisibleLine = true
        l.preferredMaxLayoutWidth = width
        l.translatesAutoresizingMaskIntoConstraints = false
        l.widthAnchor.constraint(equalToConstant: width).isActive = true
        l.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return l
    }

    private static func chip(_ s: String, _ color: NSColor) -> NSTextField {
        let c = NSTextField(labelWithString: s)
        c.font = ChromeType.mono(ofSize: 11, weight: .regular)
        c.textColor = color
        c.translatesAutoresizingMaskIntoConstraints = false
        c.setContentCompressionResistancePriority(.required, for: .horizontal)
        return c
    }

    private static func placard(_ s: String) -> NSTextField {
        let p = NSTextField(labelWithAttributedString: Widgets.letterspaced(
            s.uppercased(), size: 9.5, tracking: 1.6, color: StateLegend.Lens.chrome.color))
        p.translatesAutoresizingMaskIntoConstraints = false
        return p
    }

    private static func doors(_ views: [NSView]) -> NSStackView {
        let s = NSStackView(views: views)
        s.orientation = .horizontal
        s.alignment = .firstBaseline
        s.spacing = 10
        s.translatesAutoresizingMaskIntoConstraints = false
        return s
    }

    private static func block(_ views: [NSView], spacing: CGFloat) -> NSStackView {
        let s = NSStackView(views: views)
        s.orientation = .vertical
        s.alignment = .leading
        s.spacing = spacing
        s.edgeInsets = NSEdgeInsets(top: 3, left: 0, bottom: 3, right: 0)
        s.translatesAutoresizingMaskIntoConstraints = false
        return s
    }

    private static func rule() -> NSView {
        let line = NSView()
        line.wantsLayer = true
        line.layer?.backgroundColor = StateLegend.Palette.hairlineSoft.cgColor
        line.translatesAutoresizingMaskIntoConstraints = false
        line.heightAnchor.constraint(equalToConstant: 1).isActive = true
        line.widthAnchor.constraint(equalToConstant: width).isActive = true
        return line
    }
}
