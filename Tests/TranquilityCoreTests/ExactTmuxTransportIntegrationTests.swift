import Foundation
import XCTest
@testable import TranquilityCore

/// Opt-in real terminal I/O, with no model, real agent, app, or shared ledger.
/// Run with TB_RUN_EXACT_TMUX_TESTS=1 and VOICE_DISPATCH_SUPPORT_DIR set to a
/// fresh private directory below /private/tmp (also isolates dispatch locks).
final class ExactTmuxTransportIntegrationTests: XCTestCase {
    private struct Worker: Sendable {
        let id: String
        let pid: Int
        let pane: TmuxPaneAddress
        let transcript: URL

        var target: DispatchTarget {
            DispatchTarget(sessionId: id, pid: pid, tty: pane.paneTty, pane: pane,
                transcriptPath: transcript.path, readinessSource: .processAlive)
        }
    }

    private final class Fixture {
        let root: URL
        let binary: String
        private var sockets = Set<String>()

        init() throws {
            let env = ProcessInfo.processInfo.environment
            guard env["TB_RUN_EXACT_TMUX_TESTS"] == "1" else {
                throw XCTSkip("opt-in disposable tmux integration")
            }
            guard let support = env["VOICE_DISPATCH_SUPPORT_DIR"] else {
                throw Failure("set VOICE_DISPATCH_SUPPORT_DIR to a fresh private fixture directory")
            }
            let supportURL = URL(fileURLWithPath: support).standardizedFileURL.resolvingSymlinksInPath()
            let temporaryRoot = URL(fileURLWithPath: "/private/tmp").standardizedFileURL.resolvingSymlinksInPath()
            let attributes = try FileManager.default.attributesOfItem(atPath: support)
            guard supportURL.deletingLastPathComponent().path == temporaryRoot.path,
                  supportURL.lastPathComponent.hasPrefix("tb-exact-support."),
                  supportURL.lastPathComponent.count > "tb-exact-support.".count,
                  attributes[.type] as? FileAttributeType == .typeDirectory,
                  (attributes[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(),
                  let mode = attributes[.posixPermissions] as? NSNumber,
                  mode.intValue & 0o077 == 0 else {
                throw Failure("support override must be a user-owned private /private/tmp/tb-exact-support.XXXXXX directory")
            }
            guard Tmux.resolveBinary() != nil else { throw XCTSkip("tmux is not installed") }
            let repo = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
                .deletingLastPathComponent().deletingLastPathComponent()
            binary = env["TB_TEST_TARGET_BINARY"] ?? repo.appendingPathComponent(".build/debug/tbase-test-target").path
            guard FileManager.default.isExecutableFile(atPath: binary) else {
                throw XCTSkip("build tbase-test-target first; this test never starts a build")
            }
            // Short paths stay below sockaddr_un's length limit on macOS.
            let created = URL(fileURLWithPath: "/private/tmp/tb-exact-" + String(UUID().uuidString.prefix(12)))
            try FileManager.default.createDirectory(at: created, withIntermediateDirectories: false,
                attributes: [.posixPermissions: 0o700])
            // Foundation normalizes existing /private/tmp paths to /tmp on
            // macOS. Use the same canonical spelling as production routing.
            root = created.standardizedFileURL.resolvingSymlinksInPath()
        }

        func close() {
            // These paths were generated and started by this fixture. Never
            // kill by process name, tmux namespace, or a user's session name.
            for socket in sockets {
                _ = Tmux.run(["kill-server"], socketPath: socket, timeout: 3)
            }
            try? FileManager.default.removeItem(at: root)
        }

        func run(_ args: [String], socket: String) throws -> String {
            guard socket.hasPrefix(root.path + "/") else { throw Failure("not a fixture socket") }
            switch Tmux.run(args, socketPath: socket, timeout: 5) {
            case .success(let output): return output
            case .failure(let error): throw Failure(error.message)
            }
        }

        func worker(server: String, window: String? = nil) async throws -> Worker {
            let socket = root.appendingPathComponent(server + ".sock").path
            let id = "fixture-" + UUID().uuidString
            let format = "#{pane_id}\t#{pane_pid}\t#{pane_tty}"
            let args: [String]
            if let window {
                args = ["new-window", "-d", "-t", "same", "-n", window, "-P", "-F", format]
            } else {
                sockets.insert(socket)
                args = ["-f", "/dev/null", "new-session", "-d", "-s", "same", "-x", "160", "-y", "40", "-P", "-F", format]
            }
            // Multiple argv entries make tmux exec directly. No shell command
            // interpolation, user configuration, or inherited support path.
            let output = try run(args + ["/usr/bin/env", "VOICE_DISPATCH_SUPPORT_DIR=" + root.path, binary, id], socket: socket)
            let fields = output.trimmingCharacters(in: .whitespacesAndNewlines).split(separator: "\t").map(String.init)
            guard fields.count == 3, let pid = Int(fields[1]), pid > 0 else { throw Failure("invalid fixture identity") }
            let pane = TmuxPaneAddress(socketName: "tb", paneId: fields[0], sessionName: "same",
                paneTty: fields[2], socketPath: socket, isExternal: true)
            let worker = Worker(id: id, pid: pid, pane: pane,
                transcript: root.appendingPathComponent("test-targets/\(id).jsonl"))
            for _ in 0..<60 {
                let screen = try self.screen(worker)
                if screen.contains("pid=\(pid)"), screen.contains("❯") { return worker }
                try await Task.sleep(nanoseconds: 50_000_000)
            }
            throw Failure("fixture target did not reach its raw-mode prompt")
        }

        func screen(_ worker: Worker) throws -> String {
            try run(["capture-pane", "-p", "-J", "-t", worker.pane.paneId], socket: worker.pane.socketPath!)
        }

        func messages(_ worker: Worker) throws -> [String] {
            let data = try Data(contentsOf: worker.transcript)
            return try String(decoding: data, as: UTF8.self).split(separator: "\n").map { line in
                let object = try JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any]
                guard let message = object?["message"] as? [String: Any], let content = message["content"] as? String else {
                    throw Failure("invalid fixture transcript")
                }
                return content
            }
        }

        struct Failure: Error { let message: String; init(_ message: String) { self.message = message } }
    }

    /// The only mocked seam is harness conversation identity: these disposable
    /// processes are intentionally not registered as real coding agents.
    private final class Identity: @unchecked Sendable {
        let workers: [String: Worker]
        let failAt: Int?
        let rendezvous: Bool
        private let condition = NSCondition()
        private var calls: [String: Int] = [:]
        private var loaded = Set<String>()

        init(_ workers: [Worker], failAt: Int? = nil, rendezvous: Bool = false) {
            self.workers = Dictionary(uniqueKeysWithValues: workers.map { ($0.id, $0) })
            self.failAt = failAt
            self.rendezvous = rendezvous
        }

        func verify(_ id: String, _ pid: Int, _ pane: TmuxPaneAddress) -> Bool {
            guard let worker = workers[id], worker.pid == pid, worker.pane == pane,
                  ProcessProbe.tty(of: pid) == pane.paneTty else { return false }
            condition.lock()
            defer { condition.unlock() }
            calls[id, default: 0] += 1
            let count = calls[id]!
            if let failAt, count >= failAt { return false }
            if rendezvous, count == 3 {
                // Readiness, pre-paste, then post-load/pre-paste verification.
                // Force both buffers to coexist on the same actual server.
                loaded.insert(id)
                condition.broadcast()
                let deadline = Date().addingTimeInterval(5)
                while loaded.count != workers.count {
                    if !condition.wait(until: deadline) { return false }
                }
            }
            return true
        }
    }

    private func transport(_ identity: Identity) -> TmuxTransport {
        TmuxTransport(verificationTimeout: 2, pollInterval: 0.05,
            externalVerifier: { identity.verify($0, $1, $2) })
    }

    func testExactSocketRoutingAndExternalInputPreservation() async throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let a = try await fixture.worker(server: "a")
        let b = try await fixture.worker(server: "b")
        XCTAssertEqual(a.pane.paneId, b.pane.paneId, "the actual servers must have colliding pane IDs")
        let transport = transport(Identity([a, b]))
        let delivered = await transport.send(text: "only server a receives this", to: a.target)
        guard case .confirmed = delivered else { return XCTFail("delivery failed: \(delivered)") }
        XCTAssertEqual(try fixture.messages(a), ["only server a receives this"])
        XCTAssertEqual(try fixture.messages(b), [])

        _ = try fixture.run(["send-keys", "-t", b.pane.paneId, "-l", "unfinished human draft"], socket: b.pane.socketPath!)
        // Wait for positive screen evidence before asking the transport to act.
        for _ in 0..<40 {
            if try fixture.screen(b).contains("unfinished human draft") { break }
            try await Task.sleep(nanoseconds: 25_000_000)
        }
        let draftBefore = try fixture.screen(b)
        XCTAssertTrue(draftBefore.contains("unfinished human draft"))
        let held = await transport.send(text: "must not join the draft", to: b.target)
        guard case .deferred(.floorHeld) = held else { return XCTFail("draft was not refused: \(held)") }
        XCTAssertEqual(try fixture.screen(b), draftBefore)
        XCTAssertEqual(try fixture.messages(b), [])

        _ = try fixture.run(["copy-mode", "-t", a.pane.paneId], socket: a.pane.socketPath!)
        let copying = await transport.send(text: "must not cancel copy mode", to: a.target)
        guard case .failed(.injectionFailed) = copying else { return XCTFail("copy mode was not refused: \(copying)") }
        XCTAssertEqual(try fixture.run(["display-message", "-p", "-t", a.pane.paneId, "#{pane_in_mode}"], socket: a.pane.socketPath!), "1")
        XCTAssertEqual(try fixture.messages(a), ["only server a receives this"])
        XCTAssertEqual(try fixture.screen(b), draftBefore)
        XCTAssertTrue(ProcessProbe.isAlive(a.pid))
        XCTAssertTrue(ProcessProbe.isAlive(b.pid))
    }

    func testStaleIdentityBetweenBufferLoadAndPasteRefusesWithoutInput() async throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let worker = try await fixture.worker(server: "stale")
        let before = try fixture.screen(worker)
        let result = await transport(Identity([worker], failAt: 3)).send(text: "must never paste", to: worker.target)
        guard case .failed(.targetGone) = result else { return XCTFail("stale identity was not refused: \(result)") }
        XCTAssertEqual(try fixture.screen(worker), before)
        XCTAssertEqual(try fixture.messages(worker), [])
        XCTAssertFalse(try fixture.run(["list-buffers", "-F", "#{buffer_name}"], socket: worker.pane.socketPath!).contains("tb-dispatch-"))
        XCTAssertTrue(ProcessProbe.isAlive(worker.pid))
    }

    func testConcurrentPanesHaveIndependentBuffersOnTheSameServer() async throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let a = try await fixture.worker(server: "shared")
        let b = try await fixture.worker(server: "shared", window: "second")
        XCTAssertNotEqual(a.pane.paneId, b.pane.paneId)
        let transport = transport(Identity([a, b], rendezvous: true))
        let first = Task.detached { await transport.send(text: "buffer for first pane", to: a.target) }
        let second = Task.detached { await transport.send(text: "buffer for second pane", to: b.target) }
        let one = await first.value, two = await second.value
        guard case .confirmed = one, case .confirmed = two else { return XCTFail("concurrent sends failed: \(one), \(two)") }
        XCTAssertEqual(try fixture.messages(a), ["buffer for first pane"])
        XCTAssertEqual(try fixture.messages(b), ["buffer for second pane"])
        XCTAssertFalse(try fixture.run(["list-buffers", "-F", "#{buffer_name}"], socket: a.pane.socketPath!).contains("tb-dispatch-"))
    }
}
