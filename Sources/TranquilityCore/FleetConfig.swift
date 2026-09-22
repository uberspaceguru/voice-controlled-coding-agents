import Foundation

/// Optional custom socket paths supplement discovery; this never creates servers.
public enum FleetConfig {
    public static func socketPaths(config: URL = HubApp.configPath) -> [String] {
        guard let data = try? Data(contentsOf: config),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let tmux = root["tmux"] as? [String: Any],
              let paths = tmux["socketPaths"] as? [String] else { return [] }
        return Array(Set(paths.prefix(64).compactMap(TmuxFleet.canonicalSocket))).sorted()
    }

    /// lsof supplies paths only, never process arguments or environment. Only
    /// owned filesystem sockets survive the caller's stat check. Remote servers
    /// are intentionally outside this machine's local fleet.
    static func unixSocketPaths(_ listing: String) -> [String] {
        Array(Set(listing.split(separator: "\n").compactMap { line -> String? in
            guard line.first == "n" else { return nil }
            return TmuxFleet.canonicalSocket(String(line.dropFirst()))
        })).sorted()
    }

    static func processSocketPaths() -> [String] {
        guard case .success(let out) = Subprocess.run("/usr/sbin/lsof",
            ["-n", "-P", "-a", "-U", "-u", String(getuid()), "-c", "tmux", "-Fn"], timeout: 1.5)
        else { return [] }
        return unixSocketPaths(out).prefix(128).filter { path in
            guard let attrs = try? FileManager.default.attributesOfItem(atPath: path) else { return false }
            return attrs[.type] as? FileAttributeType == .typeSocket
                && (attrs[.ownerAccountID] as? NSNumber)?.uint32Value == getuid()
        }
    }
}
