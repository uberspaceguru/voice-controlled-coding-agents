import Foundation
import TranquilityCore

/// Fleet discovery intentionally bypasses QueueStore and the ownership ledger.
func runFleetCommand(_ arguments: [String]) throws -> Int32 {
    let options = try FleetCLI(arguments: arguments)
    let snapshot = TmuxFleet.scan(extraSockets: options.sockets)
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
    let data = try encoder.encode(snapshot)
    guard options.organize else {
        FileHandle.standardOutput.write(data)
        print("")
        return 0
    }

    // The source checkout or a colocated installed helper is trusted explicitly;
    // never search the caller's working directory for executable code.
    let executable = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL
    let checkout = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
    let candidates = options.helper.map { [URL(fileURLWithPath: $0)] } ?? [
        executable.deletingLastPathComponent().appendingPathComponent("organize-tmux.py"),
        executable.deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Resources/organize-tmux.py"),
        checkout.appendingPathComponent("scripts/organize-tmux.py"),
    ]
    guard let helper = candidates.first(where: { FileManager.default.isReadableFile(atPath: $0.path) }) else {
        throw FleetCLI.Failure.invalid("Organizer helper missing; supply --helper /absolute/path/organize-tmux.py")
    }
    let scratch = FileManager.default.temporaryDirectory
        .appendingPathComponent("tbase-fleet-\(UUID().uuidString)", isDirectory: true)
    try FileManager.default.createDirectory(at: scratch, withIntermediateDirectories: false,
                                           attributes: [.posixPermissions: 0o700])
    defer { try? FileManager.default.removeItem(at: scratch) }
    let inventory = scratch.appendingPathComponent("inventory.json")
    guard FileManager.default.createFile(atPath: inventory.path, contents: data,
                                        attributes: [.posixPermissions: 0o600]) else {
        throw FleetCLI.Failure.invalid("Could not write private inventory")
    }
    var argv = ["python3", helper.path, "--inventory", inventory.path,
                "--output-dir", options.outputDirectory!, "--timeout-seconds", String(options.timeout)]
    if options.dryRun { argv.append("--dry-run") }
    if let session = options.reportSession { argv += ["--report-session", session] }
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
    process.arguments = argv
    // The helper owns the child timeout and writes a machine-readable receipt.
    // Inherit stdout/stderr so a long analysis does not deadlock on filled pipes.
    process.standardOutput = FileHandle.standardOutput
    process.standardError = FileHandle.standardError
    process.standardInput = FileHandle.nullDevice
    try process.run()
    process.waitUntilExit()
    return process.terminationStatus
}
