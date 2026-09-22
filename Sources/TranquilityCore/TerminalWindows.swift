import Foundation

/// In-memory terminal surfaces correlated with exact tmux endpoint and live
/// client receipts. Legacy session-name window IDs remain diagnostic only.
public enum TerminalWindows {
    /// Measured host surface and the client that attached from that surface.
    /// Reuse requires fresh client proof, never a title/cwd match.
    struct Attachment: Equatable, Sendable {
        let host: TerminalHost.Choice
        let surfaceID: String
        let view: TmuxTerminalView.View
        let client: TmuxTerminalView.Client
    }

    private final class Store: @unchecked Sendable {
        private let lock = NSLock()
        private var bySession: [String: Int] = [:]
        private var byEndpoint: [String: Attachment] = [:]
        func attachment(_ endpoint: String) -> Attachment? {
            lock.lock(); defer { lock.unlock() }
            return byEndpoint[endpoint]
        }
        func remember(_ attachment: Attachment, endpoint: String) {
            lock.lock(); byEndpoint[endpoint] = attachment; lock.unlock()
        }
        func forgetEndpoint(_ endpoint: String) {
            lock.lock(); byEndpoint.removeValue(forKey: endpoint); lock.unlock()
        }
        func get(_ session: String) -> Int? {
            lock.lock(); defer { lock.unlock() }
            return bySession[session]
        }
        func put(_ session: String, _ windowId: Int) {
            lock.lock(); bySession[session] = windowId; lock.unlock()
        }
        func forget(_ session: String) {
            lock.lock(); bySession.removeValue(forKey: session); lock.unlock()
        }
        func removeAll() {
            lock.lock(); bySession.removeAll(); byEndpoint.removeAll(); lock.unlock()
        }
    }
    private static let store = Store()

    static func attachment(for endpoint: String) -> Attachment? { store.attachment(endpoint) }
    static func remember(_ value: Attachment, for endpoint: String) { store.remember(value, endpoint: endpoint) }
    static func forget(endpoint: String) { store.forgetEndpoint(endpoint) }

    /// The window this session was last opened in, if we opened it.
    public static func windowId(for sessionName: String) -> Int? {
        store.get(sessionName)
    }

    /// Remember the window an attach just landed in.
    public static func remember(sessionName: String, windowId: Int) {
        store.put(sessionName, windowId)
    }

    /// The window is gone, or was never ours. The next focus opens a fresh one.
    public static func forget(sessionName: String) {
        store.forget(sessionName)
    }

    /// Tests only: a clean table between cases.
    public static func forgetAll() { store.removeAll() }
}
