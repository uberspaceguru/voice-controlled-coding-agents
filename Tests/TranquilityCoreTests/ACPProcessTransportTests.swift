import Foundation
import Testing
@testable import TranquilityCore

/// Real disposable immediate children. These tests never launch an agent,
/// native app, tmux server, or a subprocess tree.
struct ACPProcessTransportTests {
    private enum FixtureError: Error { case childDidNotBecomeReady }

    private func waitUntil(timeout: TimeInterval = 2, _ predicate: () -> Bool) async -> Bool {
        let deadline = ProcessInfo.processInfo.systemUptime + timeout
        while !predicate() {
            guard ProcessInfo.processInfo.systemUptime < deadline else { return false }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        return true
    }

    private func withChild(_ script: String,
                           check: (ACPProcessTransport) async throws -> Void) async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("acp-close-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: directory) }
        let ready = directory.appendingPathComponent("ready")
        let transport = ACPProcessTransport(
            command: ["/bin/sh", "-c", script, "transport-fixture", ready.path],
            cwd: directory.path)
        try transport.start()
        do {
            // Each script writes readiness AFTER installing its signal traps.
            guard await waitUntil({ FileManager.default.fileExists(atPath: ready.path) }) else {
                throw FixtureError.childDidNotBecomeReady
            }
            try await check(transport)
        } catch {
            _ = await transport.closeAndWait(timeout: 1)
            throw error
        }
        let cleanedUp = await transport.closeAndWait(timeout: 1)
        #expect(cleanedUp, "Disposable child must not survive its test")
    }

    @Test func observesGracefulEOFExit() async throws {
        try await withChild("""
            trap '' TERM
            printf ready > "$1"
            while IFS= read -r line; do :; done
            exit 17
            """) { transport in
            let stopped = await transport.closeAndWait(timeout: 1)
            #expect(stopped)
            #expect(transport.exitStatus == 17, "Closing stdin must deliver EOF")
        }
    }

    @Test func defaultShutdownAllowsGracefulTERMHandler() async throws {
        try await withChild("""
            trap 'exit 23' TERM
            printf ready > "$1"
            while :; do :; done
            """) { transport in
            let stopped = await transport.closeAndWait(timeout: 1)
            #expect(stopped)
            #expect(transport.exitStatus == 23, "Default transport shutdown starts with TERM")
        }
    }

    @Test func managerCanUseGracefulInterruptFirst() async throws {
        try await withChild("""
            trap 'exit 24' INT
            trap 'exit 25' TERM
            printf ready > "$1"
            while :; do :; done
            """) { transport in
            let stopped = await transport.closeAndWait(timeout: 1, interruptFirst: true)
            #expect(stopped)
            #expect(transport.exitStatus == 24, "Pipeline interrupt cleanup must run before TERM")
        }
    }

    @Test func ignoredInterruptEscalatesToTERMWithinSameBudget() async throws {
        try await withChild("""
            trap '' INT
            trap 'exit 26' TERM
            printf ready > "$1"
            while :; do :; done
            """) { transport in
            let stopped = await transport.closeAndWait(timeout: 0.6, interruptFirst: true)
            #expect(stopped)
            #expect(transport.exitStatus == 26)
        }
    }

    @Test func stubbornChildIsKilledAndExitObserved() async throws {
        try await withChild("""
            trap '' INT TERM
            printf ready > "$1"
            while :; do :; done
            """) { transport in
            let start = ProcessInfo.processInfo.systemUptime
            let stopped = await transport.closeAndWait(timeout: 0.4, interruptFirst: true)
            #expect(stopped)
            #expect(transport.exitStatus == 9, "Success must follow the owned child's actual SIGKILL exit")
            #expect(ProcessInfo.processInfo.systemUptime - start < 2,
                    "Shutdown must not use an unbounded waitUntilExit")
        }
    }

    @Test func neverStartedAndRepeatedCloseAreSafe() async throws {
        let transport = ACPProcessTransport(command: ["/bin/sh", "-c", "exit 0"], cwd: NSTemporaryDirectory())
        #expect(transport.exitStatus == nil)
        let first = await transport.closeAndWait(timeout: 0.1)
        let second = await transport.closeAndWait(timeout: 0.1, interruptFirst: true)
        await transport.close()
        #expect(first && second)
        #expect(transport.exitStatus == nil)
        #expect(throws: ACPProcessTransport.StartError.self) { try transport.start() }
    }

    @Test func alreadyExitedChildRetainsItsStatus() async throws {
        try await withChild("printf ready > \"$1\"; exit 7") { transport in
            let exited = await waitUntil { transport.exitStatus != nil }
            #expect(exited)
            let stopped = await transport.closeAndWait(timeout: 0.1)
            #expect(stopped)
            #expect(transport.exitStatus == 7)
        }
    }

    @Test func immediateCloseCanBeFollowedByBoundedAndConcurrentClose() async throws {
        try await withChild("""
            trap '' TERM
            printf ready > "$1"
            while :; do :; done
            """) { transport in
            await transport.close()
            #expect(transport.exitStatus == nil, "Existing close remains nonwaiting")
            async let first = transport.closeAndWait(timeout: 0.4)
            async let second = transport.closeAndWait(timeout: 0.6)
            let outcomes = await (first, second)
            #expect(outcomes.0 && outcomes.1)
            #expect(transport.exitStatus == 9)
        }
    }

    @Test func zeroBudgetDoesNotClaimASignalIsAnExit() async throws {
        try await withChild("""
            trap '' TERM
            printf ready > "$1"
            while :; do :; done
            """) { transport in
            let stopped = await transport.closeAndWait(timeout: 0)
            #expect(!stopped)
            #expect(transport.exitStatus == nil)
            // The fixture's cleanup performs the bounded escalation afterward.
        }
    }

    @Test func stoppingOneOwnedChildLeavesOtherImmediateChildrenAlive() async throws {
        try await withChild("""
            printf ready > "$1"
            while IFS= read -r line; do :; done
            exit 0
            """) { other in
            try await withChild("""
                trap '' INT TERM
                printf ready > "$1"
                while :; do :; done
                """) { target in
                let stopped = await target.closeAndWait(timeout: 0.4, interruptFirst: true)
                #expect(stopped)
                #expect(target.exitStatus == 9)
                #expect(other.exitStatus == nil, "No sibling process or process group may be stopped")
                try await other.write(Data("still open".utf8))
                #expect(other.exitStatus == nil)
            }
        }
    }
}
