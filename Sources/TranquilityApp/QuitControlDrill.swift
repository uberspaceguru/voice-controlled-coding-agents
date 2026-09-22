import AppKit
import TranquilityCore

/// Runs before the app delegate exists. No microphone, hooks, live queue,
/// manager process, or shared preference writes are involved.
@MainActor
enum QuitControlDrill {
    static func run() async -> Bool {
        let hud = StatusHUD(persistWidth: false)
        // Exercise real layout without reopening a visible app over the user.
        let panel = hud.build()
        panel.alphaValue = 0
        panel.ignoresMouseEvents = true
        hud.setCollapsed(false)
        var requests = 0
        hud.onQuit = { requests += 1 }
        var checks: [(String, Bool)] = []
        defer { hud.panel?.orderOut(nil) }

        func check(_ name: String) async {
            try? await Task.sleep(for: .milliseconds(300))
            hud.panel?.contentView?.layoutSubtreeIfNeeded()
            guard let button = hud.quitButton, let root = hud.panel?.contentView,
                  let parent = button.superview else {
                checks.append((name + " exists", false)); return
            }
            let rect = parent.convert(button.frame, to: root)
            checks.append((name + " available and inside panel",
                           !button.isHiddenOrHasHiddenAncestor && root.bounds.contains(rect)
                           && rect.width >= 100 && rect.height >= 24))
            checks.append((name + " accessible name", button.accessibilityLabel() == "Quit Tranquility Base"))
            let before = requests
            button.performClick(nil)
            checks.append((name + " one quit request", requests == before + 1))
        }

        hud.showIdle(rows: [])
        await check("empty grid")
        // The same real controls receive workers discovered on another
        // server; their session ID, not a tab title or row position, survives
        // the click. These are synthetic observations, never live agents.
        let observations = [
            SessionOwnershipRecord(sessionId: "external-alpha", harness: "codex", pid: 101,
                paneId: "%0", sessionName: "work", origin: .external),
            SessionOwnershipRecord(sessionId: "external-beta", harness: "claude-code", pid: 102,
                paneId: "%0", sessionName: "work", origin: .external),
        ]
        let rows = FleetLive.merging([], records: observations).map {
            SessionRow(id: $0.sessionId, name: $0.name ?? $0.sessionId,
                       aux: $0.sessionId, lamp: .working, harness: $0.harness)
        }
        var focused: [String] = []
        var revived: [String] = []
        hud.onGoToSession = { focused.append($0) }
        hud.onRevive = { id, _ in revived.append(id) }
        hud.showIdle(rows: rows)
        await check("external fleet grid")
        func controls(_ view: NSView) -> [NSControl] {
            ((view as? NSControl).map { [$0] } ?? [])
                + view.subviews.flatMap { controls($0) }
        }
        if let root = hud.panel?.contentView {
            let rendered = controls(root)
            for row in rows {
                let control = rendered.first { $0.identifier?.rawValue == row.id }
                checks.append(("external row \(row.id) rendered", control != nil))
                if let control { _ = control.sendAction(control.action, to: control.target) }
            }
            checks.append(("external row exact identities", focused == rows.map(\.id)))
            checks.append(("external rows never revive", revived.isEmpty))
            if let flag = CommandLine.arguments.firstIndex(of: "--fleet-control-shot"),
               flag + 1 < CommandLine.arguments.count,
               let bitmap = root.bitmapImageRepForCachingDisplay(in: root.bounds) {
                root.cacheDisplay(in: root.bounds, to: bitmap)
                try? bitmap.representation(using: .png, properties: [:])?
                    .write(to: URL(fileURLWithPath: CommandLine.arguments[flag + 1]))
            }
        }
        hud.setManager(on: true)
        hud.showIdle(rows: [])
        hud.setManagerState(StatusHUD.orbState, line: "listening")
        await check("hands-free listening")
        hud.setManagerState(StatusHUD.orbState, line: "speaking", mood: "speaking")
        await check("hands-free speaking")
        if let flag = CommandLine.arguments.firstIndex(of: "--quit-control-shot"),
           flag + 1 < CommandLine.arguments.count,
           let view = hud.panel?.contentView,
           let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) {
            view.cacheDisplay(in: view.bounds, to: bitmap)
            try? bitmap.representation(using: .png, properties: [:])?
                .write(to: URL(fileURLWithPath: CommandLine.arguments[flag + 1]))
        }
        hud.showResult("A response is being prepared.")
        await check("conversation card")
        for (name, passed) in checks { print("quit control: \(passed ? "PASS" : "FAIL") \(name)") }
        return !checks.isEmpty && checks.allSatisfy(\.1)
    }
}
