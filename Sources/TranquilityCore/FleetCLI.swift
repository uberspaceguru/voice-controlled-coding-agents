import Foundation

/// Parsed before opening the queue: observing terminals must not enroll or adopt them.
public struct FleetCLI: Equatable, Sendable {
    public enum Failure: Error, CustomStringConvertible {
        case invalid(String)
        public var description: String {
            switch self { case .invalid(let message): return message }
        }
    }
    public let organize: Bool
    public var sockets: [String] = []
    public var outputDirectory: String?
    public var helper: String?
    public var dryRun = false
    public var timeout = 180
    public var reportSession: String?

    public init(arguments: [String]) throws {
        guard let command = arguments.first, ["fleet", "organize"].contains(command) else {
            throw Failure.invalid("Expected fleet or organize")
        }
        organize = command == "organize"
        var index = 1
        var seen = Set<String>()
        while index < arguments.count {
            let flag = arguments[index]
            index += 1
            guard flag == "--socket" || seen.insert(flag).inserted else {
                throw Failure.invalid("Duplicate option: \(flag)")
            }
            if flag == "--json" { continue }
            if flag == "--dry-run", organize { dryRun = true; continue }
            let allowed = organize
                ? ["--socket", "--output-dir", "--helper", "--timeout-seconds", "--report-session"]
                : ["--socket"]
            guard allowed.contains(flag), index < arguments.count else {
                throw Failure.invalid("Unknown option or missing value: \(flag)")
            }
            let value = arguments[index]
            index += 1
            switch flag {
            case "--socket", "--output-dir", "--helper":
                guard value.hasPrefix("/"), !value.contains("\0") else {
                    throw Failure.invalid("\(flag) requires an absolute path")
                }
                if flag == "--socket" { sockets.append(value) }
                else if flag == "--output-dir" { outputDirectory = value }
                else { helper = value }
            case "--timeout-seconds":
                guard let seconds = Int(value), (10...600).contains(seconds) else {
                    throw Failure.invalid("Timeout must be between 10 and 600 seconds")
                }
                timeout = seconds
            case "--report-session":
                guard UUID(uuidString: value) != nil else {
                    throw Failure.invalid("Report session must be a UUID")
                }
                reportSession = value
            default: break
            }
        }
        if organize, outputDirectory == nil {
            throw Failure.invalid("organize requires --output-dir /absolute/new-directory")
        }
        sockets = Array(Set(sockets)).sorted()
    }
}
