import XCTest
@testable import TranquilityCore

/// The keep-audio drill is the deploy-time proof that a committed capture is
/// kept and a kept file is adopted (docs/rulings/ruling-an-open-microphone-is-a-promise.md).
/// It runs on the real filesystem against a throwaway store; this test guards
/// the drill itself, so a green launch gate cannot come from a drill that
/// started erroring or quietly inverted a check.
final class KeepAudioDrillTests: XCTestCase {
    func testEveryCheckPasses() throws {
        let groups = try KeepAudioDrill.run()
        XCTAssertEqual(groups.map(\.name), ["keepAudio", "bootAdopt", "keptNow", "dismissKeeps", "partialsSurvive"])
        for group in groups {
            for check in group.checks {
                XCTAssertTrue(check.passed, "\(group.name).\(check.name) must pass")
            }
        }
    }

    /// Leaves nothing behind — the drill writes only under its own temp root.
    func testDrillLeavesNoResidueInTheTempRoot() throws {
        let fm = FileManager.default
        let fixture = fm.temporaryDirectory
            .appendingPathComponent("keep-drill-test-\(UUID().uuidString)", isDirectory: true)
        let parent = fixture.appendingPathComponent("drills", isDirectory: true)
        try fm.createDirectory(at: parent, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: fixture) }
        let sibling = fixture.appendingPathComponent("unrelated.txt")
        let sentinel = Data("keep this sibling".utf8)
        try sentinel.write(to: sibling)

        _ = try KeepAudioDrill.run(temporaryDirectory: parent)
        XCTAssertEqual(try fm.contentsOfDirectory(atPath: parent.path), [],
                       "the drill must clean up its throwaway root")
        XCTAssertEqual(try Data(contentsOf: sibling), sentinel,
                       "cleanup must not remove or change a sibling outside the drill parent")
    }
}
