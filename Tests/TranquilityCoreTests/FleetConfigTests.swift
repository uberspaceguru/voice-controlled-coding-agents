import XCTest
@testable import TranquilityCore

final class FleetConfigTests: XCTestCase {
    func testOnlyAbsoluteSocketFieldsAreCandidates() {
        XCTAssertEqual(FleetConfig.unixSocketPaths("p123\nn/private/tmp/a.sock\nn->0x12\nnrelative\np4\nn/private/tmp/a.sock\n"), ["/private/tmp/a.sock"])
    }

    func testExplicitPathsAreDeduplicatedAndInvalidPathsIgnored() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appendingPathComponent("hq.json")
        try Data(#"{"tmux":{"socketPaths":["/private/tmp/custom.sock","relative","/private/tmp/custom.sock"]}}"#.utf8).write(to: file)
        XCTAssertEqual(FleetConfig.socketPaths(config: file), ["/private/tmp/custom.sock"])
        try Data("{}".utf8).write(to: file)
        XCTAssertEqual(FleetConfig.socketPaths(config: file), [])
    }
}
