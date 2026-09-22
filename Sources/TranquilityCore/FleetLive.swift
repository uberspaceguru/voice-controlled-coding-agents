import Foundation

/// Joins observations for display; it does not import them into the ownership
/// store. Action doors independently recheck the exact process/session/pane.
public enum FleetLive {
    /// A single display join across both harnesses. Cached fleet evidence may
    /// fill registry gaps; it cannot grant lifecycle ownership or permission to
    /// send. Fleet reads use only the cache; the ownership reader retains its
    /// existing liveness checks. Refresh discovery separately off-main.
    public static func sessions(
        registry: [LiveSession],
        ownership: any SessionOwnershipStore = FileSessionOwnershipStore.shared,
        records: [SessionOwnershipRecord] = ExistingAgentDirectory.shared.cachedRecords,
        status: (String) -> String? = { _ in nil }, name: (String) -> String? = { _ in nil }
    ) -> [LiveSession] {
        merging(registry + ownership.liveNonRegistrySessions(status: status, name: name), records: records)
    }

    public static func merging(_ live: [LiveSession], records: [SessionOwnershipRecord]) -> [LiveSession] {
        let grouped = Dictionary(grouping: live, by: { $0.sessionId.lowercased() })
        var result: [String: LiveSession] = [:]
        for (id, rows) in grouped where Set(rows.map(\.pid)).count == 1 { result[id] = rows[0] }
        for record in records {
            let id = record.sessionId.lowercased()
            if let rows = grouped[id], rows.contains(where: { $0.pid != record.pid }) {
                // Contradictory live identity is visible in `fleet`, not an
                // arbitrary first matching dispatch target.
                result[id] = nil
                continue
            }
            if result[id] == nil, grouped[id] == nil {
                result[id] = LiveSession(harness: record.harness, pid: record.pid,
                    sessionId: record.sessionId, cwd: record.cwd, status: nil,
                    name: [record.sessionName, record.paneId].compactMap { $0 }.joined(separator: " / "),
                    waitingFor: nil)
            }
        }
        return result.values.sorted { $0.sessionId < $1.sessionId }
    }

    /// Memory-only fleet side. The app refreshes discovery off-main; callers
    /// already reading ownership retain that behavior and gain external workers.
    public static func nonRegistrySessions(
        ownership: any SessionOwnershipStore = FileSessionOwnershipStore.shared,
        status: (String) -> String? = { _ in nil }, name: (String) -> String? = { _ in nil }
    ) -> [LiveSession] {
        merging(ownership.liveNonRegistrySessions(status: status, name: name),
                records: ExistingAgentDirectory.shared.cachedRecords.filter { $0.harness != "claude-code" })
    }
}
