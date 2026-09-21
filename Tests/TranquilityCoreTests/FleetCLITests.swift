import XCTest
@testable import TranquilityCore

final class FleetCLITests: XCTestCase {
    func testInventoryAcceptsMultipleExactSocketsButNoMutationFlags() throws {
        let request = try FleetCLI(arguments: ["fleet", "--json", "--socket", "/tmp/a", "--socket", "/tmp/b"])
        XCTAssertFalse(request.organize)
        XCTAssertEqual(request.sockets, ["/tmp/a", "/tmp/b"])
        for flag in ["--send", "--adopt", "--dry-run", "--output-dir"] {
            XCTAssertThrowsError(try FleetCLI(arguments: ["fleet", flag, "/tmp/example"]))
        }
        XCTAssertThrowsError(try FleetCLI(arguments: ["fleet", "--socket", "tb"]))
    }

    func testOrganizerIsBoundedAndRequiresAnExplicitOutputLocation() throws {
        let request = try FleetCLI(arguments: ["organize", "--output-dir", "/tmp/proposal", "--dry-run", "--timeout-seconds", "120"])
        XCTAssertTrue(request.organize)
        XCTAssertTrue(request.dryRun)
        XCTAssertEqual(request.timeout, 120)
        for arguments in [
            ["organize"], ["organize", "--output-dir", "relative"],
            ["organize", "--output-dir", "/tmp/a", "--timeout-seconds", "0"],
            ["organize", "--output-dir", "/tmp/a", "--timeout-seconds", "601"],
            ["organize", "--output-dir", "/tmp/a", "--report-session", "wrong"],
            ["organize", "--output-dir", "/tmp/a", "--output-dir", "/tmp/b"],
            ["organize", "--output-dir", "/tmp/a", "--apply"],
        ] { XCTAssertThrowsError(try FleetCLI(arguments: arguments)) }
    }
}
