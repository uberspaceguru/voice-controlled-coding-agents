import XCTest
@testable import TranquilityCore

/// tb-media-aware-2 (26 Sep): the Codex walk re-parsed 2.8 GB of rollouts every
/// 30 s and held a core at 65-72% with the app idle.
final class CodexDigestTests: XCTestCase {
    private func rollout(_ lines: [String]) throws -> URL {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appendingPathComponent("rollout.jsonl")
        try (lines.joined(separator: "\n") + "\n").write(to: url, atomically: true, encoding: .utf8)
        return url
    }

    private let meta = #"{"timestamp":"2026-09-26T10:00:00Z","type":"session_meta","payload":{"id":"01a0-test","cwd":"/tmp/x"}}"#
    private func message(_ role: String, _ text: String) -> String {
        #"{"timestamp":"2026-09-26T10:00:01Z","type":"response_item","payload":{"type":"message","role":"\#(role)","content":[{"type":"input_text","text":"\#(text)"}]}}"#
    }

    func testTheEndsSayWhatTheWholeFileSays() throws {
        let filler = String(repeating: "x", count: 1000)
        let lines = [meta] + (0..<400).map { message($0 % 2 == 0 ? "user" : "assistant", "\(filler) \($0)") } + [message("user", "last")]
        let url = try rollout(lines)
        let size = try XCTUnwrap(url.resourceValues(forKeys: [.fileSizeKey]).fileSize)
        XCTAssertGreaterThan(size, CodexDigestCache.tailBytes, "big enough that only the ends are read")
        let whole = CodexRollout.parse(try String(contentsOf: url, encoding: .utf8))
        let d = try XCTUnwrap(CodexDigestCache.read(url, modified: Date(), size: size))
        XCTAssertEqual(d.sessionId, whole.meta?.sessionId)
        XCTAssertEqual(d.cwd, whole.meta?.cwd)
        XCTAssertEqual(d.lastRole, whole.messages.last?.role)
        XCTAssertEqual(d.lastRole, "user")
    }

    func testAnUnchangedFileIsNotReadAgain() throws {
        let url = try rollout([meta, message("assistant", "hi")])
        let cache = CodexDigestCache()
        let at = Date(timeIntervalSince1970: 100)
        _ = cache.digest(url, modified: at, size: 10)
        _ = cache.digest(url, modified: at, size: 10)
        XCTAssertEqual(cache.reads, 1)
        _ = cache.digest(url, modified: at + 1, size: 11)
        XCTAssertEqual(cache.reads, 2, "changed: read again")
    }
}
