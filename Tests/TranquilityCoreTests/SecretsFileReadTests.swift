import XCTest
@testable import TranquilityCore

/// The hub token is never cached, so a sign-in or sign-out is seen at once.
/// But the file behind it was re-read and logged on every ask, four times
/// every 1.5 s, 136,074 log lines in a day (24 Sep). It is now read again only
/// when it has changed on disk.
final class SecretsFileReadTests: XCTestCase {
    private var tmpDir: URL!
    private var savedDir: String?
    private var savedTrace: (@Sendable (String) -> Void)?

    final class Lines: @unchecked Sendable {
        private let lock = NSLock()
        private var lines: [String] = []
        func add(_ s: String) { lock.lock(); lines.append(s); lock.unlock() }
        var reads: Int { lock.lock(); defer { lock.unlock() }; return lines.filter { $0.hasPrefix("read ") }.count }
    }

    override func setUpWithError() throws {
        tmpDir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("vd-secrets-read-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
        savedDir = ProcessInfo.processInfo.environment["VOICE_DISPATCH_SUPPORT_DIR"]
        setenv("VOICE_DISPATCH_SUPPORT_DIR", tmpDir.path, 1)
        savedTrace = Secrets.trace
    }

    override func tearDownWithError() throws {
        Secrets.trace = savedTrace
        if let savedDir { setenv("VOICE_DISPATCH_SUPPORT_DIR", savedDir, 1) }
        else { unsetenv("VOICE_DISPATCH_SUPPORT_DIR") }
        try? FileManager.default.removeItem(at: tmpDir)
    }

    private func writeFile(_ json: String) throws {
        try json.write(to: Secrets.fileURL, atomically: true, encoding: .utf8)
    }

    func testAnUnchangedFileIsReadOnceAndAChangedOneAtOnce() throws {
        let lines = Lines()
        Secrets.trace = { lines.add($0) }
        try writeFile(#"{"hub-token": "first"}"#)
        XCTAssertEqual(Secrets.read(.hubToken), "first")
        for _ in 0..<50 { XCTAssertEqual(Secrets.read(.hubToken), "first") }
        XCTAssertEqual(lines.reads, 1, "fifty more asks of an unchanged file: no read, no log line")

        // Another process (tbase) rewrites the file: seen on the very next ask.
        try writeFile(#"{"hub-token": "second"}"#)
        XCTAssertEqual(Secrets.read(.hubToken), "second")
        XCTAssertEqual(lines.reads, 2)

        // This app's own write, the sign-out: seen at once too.
        try Secrets.write(.hubToken, value: "")
        XCTAssertNil(Secrets.read(.hubToken))
    }
}
