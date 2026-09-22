import Foundation
import XCTest
@testable import TranquilityCore

/// Fixture-only: these tests never contact tmux, read a user's registry,
/// enumerate running processes, or change session ownership.
final class TmuxFleetTests: XCTestCase {
    private let socket = "/fixture/tmux/default"
    private let first = "11111111-1111-4111-8111-111111111111"
    private let second = "22222222-2222-4222-8222-222222222222"

    private func row(session: String = "work", window: String = "@1", pane: String = "%1",
                     pid: Int = 100, tty: String = "/dev/ttys001", command: String = "zsh",
                     dead: Bool = false, clients: Int = 1) -> String {
        [session, window, "editor", pane, String(pid), tty, "/fixture/project with spaces", command,
         dead ? "1" : "0", String(clients)].joined(separator: "\t")
    }

    private func pane(command: String = "zsh", dead: Bool = false) throws -> TmuxFleet.Pane {
        try XCTUnwrap(TmuxFleet.parsePanes(row(command: command, dead: dead), socketPath: socket)?.first)
    }

    private func processes(_ command: String = "codex", tty: String = "/dev/ttys001") -> [Int: TmuxFleet.ProcessRow] {
        [100: .init(pid: 100, parent: 1, tty: tty, command: "zsh"),
         101: .init(pid: 101, parent: 100, tty: tty, command: command)]
    }

    private func registry(id: String, pid: Int = 101, tmux: String? = "work:@1.%1",
                          kind: String? = nil) -> SessionRegistry.Entry {
        .init(pid: pid, sessionId: id, cwd: "/fixture/project with spaces", status: "idle", tmux: tmux,
              messagingSocketPath: nil, name: "Build helper", updatedAt: 10, kind: kind)
    }

    private func identify(_ value: TmuxFleet.Pane, processes: [Int: TmuxFleet.ProcessRow],
                          registry: [SessionRegistry.Entry] = [], ownership: [SessionOwnershipRecord] = [],
                          codex: [Int: TmuxFleet.CodexFiles] = [:],
                          metadata: [String: CodexRollout.SessionMeta] = [:]) -> TmuxFleet.Pane {
        var panes = [value]
        TmuxFleet.identify(&panes, processes: processes, registry: registry,
                           ownership: ownership, codex: codex, metadata: metadata)
        return panes[0]
    }

    func testSocketDiscoveryDeduplicatesCanonicalPathsAndKeepsNamedServers() throws {
        let actual = TmuxFleet.socketCandidates(uid: 501, support: "/fixture/app/tmux",
            tmux: "/fixture/custom,with,commas/socket,77,1",
            extra: ["/fixture/custom,with,commas/./socket", "relative", "/fixture/named/socket"]) { root in
                [root + "/default", root + "/other", root + "/./other"]
            }
        XCTAssertEqual(actual.count, Set(actual).count)
        XCTAssertTrue(actual.contains("/fixture/app/tmux/tmux-501/tb"))
        XCTAssertTrue(actual.contains("/fixture/app/tmux/tmux-501/other"))
        XCTAssertTrue(actual.contains("/fixture/custom,with,commas/socket"))
        XCTAssertTrue(actual.contains("/fixture/named/socket"))
        XCTAssertFalse(actual.contains("relative"))
        XCTAssertEqual(actual, actual.sorted())
    }

    func testEnvironmentSocketUsesRightmostFieldsAndRejectsMalformedValues() {
        XCTAssertEqual(TmuxFleet.socketFromEnvironment("/fixture/a,b/socket,123,4"), "/fixture/a,b/socket")
        XCTAssertNil(TmuxFleet.socketFromEnvironment("/fixture/socket,123"))
        XCTAssertNil(TmuxFleet.socketFromEnvironment("relative,123,4"))
        XCTAssertNil(TmuxFleet.socketFromEnvironment("/fixture/socket,pid,4"))
        XCTAssertNil(TmuxFleet.canonicalSocket("/fixture/socket\nother"))
        XCTAssertNil(TmuxFleet.canonicalSocket("/fixture/\0socket"))
    }

    func testPresentationViewDoesNotReplaceSourceIdentity() throws {
        let markedView = row(session: "tb-view-a") + "\t" + String(repeating: "a", count: 64)
        let original = row(session: "z-work") + "\t"
        let parsed = try XCTUnwrap(TmuxFleet.parsePanes(markedView + "\n" + original, socketPath: socket))
        XCTAssertEqual(parsed.count, 1)
        XCTAssertEqual(parsed[0].sessionName, "z-work")
        XCTAssertEqual(parsed[0].sessionAliases, ["tb-view-a", "z-work"])
        let identified = identify(parsed[0], processes: processes("claude"),
                                  registry: [registry(id: first, tmux: "z-work:@1.%1")])
        XCTAssertEqual(identified.agents.first?.sessionId, first)
    }

    func testLinkedOriginalSessionAliasRetainsRegistryIdentity() throws {
        let parsed = try XCTUnwrap(TmuxFleet.parsePanes(row(session: "a-view") + "\n" + row(session: "work"), socketPath: socket)?.first)
        let identified = identify(parsed, processes: processes("claude"), registry: [registry(id: first)])
        XCTAssertEqual(identified.agents.first?.sessionId, first)
    }

    func testSamePaneIdOnDifferentSocketsHasDifferentStableIdentity() throws {
        let a = try XCTUnwrap(TmuxFleet.parsePanes(row(), socketPath: "/fixture/server-a")?.first)
        let b = try XCTUnwrap(TmuxFleet.parsePanes(row(), socketPath: "/fixture/server-b")?.first)
        XCTAssertNotEqual(a.id, b.id)
        XCTAssertEqual(a.id.count, 64)
        XCTAssertEqual(a.id, TmuxFleet.paneIdentity(socketPath: "/fixture/server-a", paneId: "%1"))
        XCTAssertNotEqual(a.id, TmuxFleet.paneIdentity(socketPath: "/fixture/server-a", paneId: "%2"))
    }

    func testMultiplePanesRemainSeparateWithSessionScopedClientCount() throws {
        let text = row(clients: 2) + "\n" + row(pane: "%2", pid: 200, tty: "/dev/ttys002", clients: 2)
        let panes = try XCTUnwrap(TmuxFleet.parsePanes(text, socketPath: socket))
        XCTAssertEqual(panes.count, 2)
        XCTAssertNotEqual(panes[0].id, panes[1].id)
        XCTAssertEqual(panes.map(\.attachedClientCount), [2, 2])
        XCTAssertEqual(panes[0].cwd, "/fixture/project with spaces")
    }

    func testLinkedWindowRepeatsPhysicalPaneWithDeterministicRepresentative() throws {
        let first = row(session: "zeta", clients: 1)
        let second = row(session: "alpha", clients: 0)
        let a = try XCTUnwrap(TmuxFleet.parsePanes(first + "\n" + second, socketPath: socket))
        let b = try XCTUnwrap(TmuxFleet.parsePanes(second + "\n" + first, socketPath: socket))
        XCTAssertEqual(a, b)
        XCTAssertEqual(a.count, 1)
        XCTAssertEqual(a[0].sessionName, "alpha")
        XCTAssertEqual(a[0].attachedClientCount, 0)
    }

    func testConflictingDuplicatePaneIdentityFailsClosed() {
        XCTAssertNil(TmuxFleet.parsePanes(row() + "\n" + row(pid: 200), socketPath: socket))
        XCTAssertNil(TmuxFleet.parsePanes(row() + "\n" + row(tty: "/dev/ttys002"), socketPath: socket))
    }

    func testMalformedInventoryIsNotAnEmptyServer() {
        let (server, panes) = TmuxFleet.serverInventory(socketPath: socket, result: .success("not\tan\tinventory"))
        XCTAssertEqual(server.status, "error")
        XCTAssertEqual(server.error, "malformed_pane_inventory")
        XCTAssertTrue(panes.isEmpty)
        XCTAssertNil(TmuxFleet.parsePanes(row(session: "bad\tname"), socketPath: socket))
    }

    func testEmptyAbsentTimeoutAndUnqueryableServerAreDistinct() {
        XCTAssertEqual(TmuxFleet.serverInventory(socketPath: socket, result: .success("")).0.status, "ok")
        XCTAssertEqual(TmuxFleet.serverInventory(socketPath: socket,
            result: .failure(.init(message: "no current target"))).0.status, "ok")
        let absent = TmuxFleet.serverInventory(socketPath: socket,
            result: .failure(.init(message: "no server running on /fixture/socket"))).0
        XCTAssertEqual(absent.status, "unavailable")
        XCTAssertEqual(absent.error, "socket_unavailable")
        let timeout = TmuxFleet.serverInventory(socketPath: socket,
            result: .failure(.init(message: "private diagnostic content", timedOut: true))).0
        XCTAssertEqual(timeout.status, "error")
        XCTAssertEqual(timeout.error, "pane_query_timeout")
        let failure = TmuxFleet.serverInventory(socketPath: socket,
            result: .failure(.init(message: "private diagnostic content"))).0
        XCTAssertEqual(failure.error, "pane_query_failed")
        XCTAssertFalse(String(describing: failure).contains("private diagnostic"))
    }

    func testProcessParserHandlesPaddingPathsSpacesZombieAndMissingTTY() throws {
        let table = """
            100     1 ttys001 Ss   /bin/zsh
            101   100 ttys001 S+   /fixture/Applications With Spaces/claude.exe
            102   100 ??      S    /usr/bin/other
            103   100 ttys001 Z    zombie
        """
        let rows = try XCTUnwrap(TmuxFleet.parseProcesses(table))
        XCTAssertEqual(rows[101]?.command, "claude.exe")
        XCTAssertEqual(rows[101]?.tty, "/dev/ttys001")
        XCTAssertNil(rows[102]?.tty)
        XCTAssertNil(rows[103])
        XCTAssertNil(TmuxFleet.parseProcesses("not a process table"))
        XCTAssertNil(TmuxFleet.parseProcesses(""))
    }

    func testPlainShellAndUnknownCommandAreNotInventedAgents() throws {
        let shell = identify(try pane(), processes: [:])
        XCTAssertEqual(shell.identityStatus, "none")
        XCTAssertTrue(shell.agents.isEmpty)
        let unknown = identify(try pane(command: "node"), processes: [:])
        XCTAssertEqual(unknown.identityStatus, "unresolved")
        XCTAssertTrue(unknown.candidateHarnesses.isEmpty)
        XCTAssertTrue(unknown.agents.isEmpty)
    }

    func testHarnessCommandAloneIsOnlyAnUnresolvedCandidate() throws {
        let candidate = identify(try pane(command: "codex"), processes: processes())
        XCTAssertEqual(candidate.identityStatus, "unresolved")
        XCTAssertEqual(candidate.candidateHarnesses, ["codex"])
        XCTAssertTrue(candidate.agents.isEmpty)
    }

    func testClaudeExeUsesRegistryAndLivePaneAncestryAsEvidence() throws {
        let result = identify(try pane(command: "claude.exe"), processes: processes("claude.exe"),
                              registry: [registry(id: first)])
        XCTAssertEqual(result.identityStatus, "verified")
        XCTAssertEqual(result.agents.map(\.sessionId), [first])
        XCTAssertEqual(result.agents[0].harness, "claude-code")
        XCTAssertEqual(result.agents[0].name, "Build helper")
        XCTAssertEqual(result.agents[0].status, "idle")
        XCTAssertTrue(result.agents[0].identityEvidence.contains("pane_process_ancestry"))
    }

    func testRegistryCanProveNodeHarnessButStaleUnknownExecutableCannot() throws {
        XCTAssertEqual(identify(try pane(), processes: processes("node"), registry: [registry(id: first)]).agents.count, 1)
        XCTAssertTrue(identify(try pane(), processes: processes("python"), registry: [registry(id: first)]).agents.isEmpty)
    }

    func testRegistryWrongTTYWrongPaneAndBackgroundAreRejected() throws {
        XCTAssertTrue(identify(try pane(), processes: processes("claude.exe", tty: "/dev/ttys002"),
                               registry: [registry(id: first)]).agents.isEmpty)
        XCTAssertTrue(identify(try pane(), processes: processes("claude.exe"),
                               registry: [registry(id: first, tmux: "different:@1.%1")]).agents.isEmpty)
        XCTAssertTrue(identify(try pane(), processes: processes("claude.exe"),
                               registry: [registry(id: first, kind: "bg")]).agents.isEmpty)
        XCTAssertTrue(identify(try pane(), processes: processes("claude.exe"),
                               registry: [registry(id: first, kind: "background")]).agents.isEmpty)
    }

    func testRegistryAmbiguousCurrentConversationIsNotChosenByRecency() throws {
        let result = identify(try pane(), processes: processes("claude.exe"),
                              registry: [registry(id: first), registry(id: second)])
        XCTAssertTrue(result.agents.isEmpty)
        XCTAssertEqual(result.identityStatus, "unresolved")
    }

    func testAncestryCannotCrossDetachedProcessOrCycle() throws {
        var rows = processes()
        rows[101] = .init(pid: 101, parent: 200, tty: "/dev/ttys001", command: "codex")
        rows[200] = .init(pid: 200, parent: 101, tty: "/dev/ttys001", command: "zsh")
        XCTAssertFalse(TmuxFleet.process(101, belongsTo: try pane(), processes: rows))
    }

    func testDeadPaneRetainsInventoryWithoutAnyLiveIdentity() throws {
        let value = identify(try pane(command: "codex", dead: true), processes: processes())
        XCTAssertTrue(value.dead)
        XCTAssertEqual(value.identityStatus, "none")
        XCTAssertTrue(value.agents.isEmpty)
        XCTAssertNotNil(TmuxFleet.parsePanes(row(pid: 0, tty: "", dead: true), socketPath: socket))
    }

    func testLsofOpenWriterFilesKeepPIDAndFileBoundariesWithoutClaimingOSLocks() {
        let listing = """
        p101
        f10
        lW
        n/fixture/locks/\(first).lock
        f11
        n/fixture/locks/\(second).lock
        f12
        lW
        n/fixture/locks/.coordination.lock
        f13
        n/fixture/sessions/2026/rollout-\(first).jsonl
        p999
        f10
        lW
        n/fixture/locks/\(second).lock
        """
        let parsed = TmuxFleet.parseCodexFiles(listing, allowedPids: [101], locks: "/fixture/locks", sessions: "/fixture/sessions")
        XCTAssertEqual(parsed[101]?.locks, [first, second])
        XCTAssertEqual(parsed[101]?.rollouts[first], "/fixture/sessions/2026/rollout-\(first).jsonl")
        XCTAssertNil(parsed[999])
    }

    func testLsofRejectsNestedSiblingAndTraversalPaths() {
        let paths = ["/fixture/locks/nested/\(first).lock", "/fixture/locks-other/\(first).lock",
                     "/fixture/locks/../other/\(first).lock"]
        let listing = "p101\n" + paths.enumerated().map { "f\($0.offset)\nlW\nn\($0.element)" }.joined(separator: "\n")
        let parsed = TmuxFleet.parseCodexFiles(listing, allowedPids: [101], locks: "/fixture/locks", sessions: "/fixture/sessions")
        XCTAssertTrue(parsed[101]?.locks.isEmpty ?? true)
    }

    func testBlankLsofLockStateAllowsOnlyQualifiedOpenFileAssociation() throws {
        let listing = "p101\nf10\nl \nn/fixture/locks/\(first).lock"
        let files = TmuxFleet.parseCodexFiles(listing, allowedPids: [101], locks: "/fixture/locks", sessions: "/fixture/sessions")
        let result = identify(try pane(), processes: processes(), codex: files,
                              metadata: [first: .init(sessionId: first, threadSource: "user")])
        XCTAssertEqual(result.agents.map(\.sessionId), [first])
        XCTAssertTrue(result.agents[0].identityEvidence.contains("open_writer_lock"))
        XCTAssertFalse(result.agents[0].identityEvidence.contains("held_writer_lock"))
        XCTAssertTrue(identify(try pane(), processes: processes(), codex: files).agents.isEmpty)
    }

    func testCodexUniqueRootPlusSubagentFilesAssociateConversation() throws {
        let result = identify(try pane(), processes: processes(), codex: [101: .init(locks: [first, second])],
                              metadata: [first: .init(sessionId: first, threadSource: "user"),
                                         second: .init(sessionId: second, threadSource: "subagent")])
        XCTAssertEqual(result.agents.map(\.sessionId), [first])
        XCTAssertEqual(result.agents[0].identityEvidence.first, "open_writer_lock")
        XCTAssertNil(result.agents[0].name)
        XCTAssertNil(result.agents[0].status)
    }

    func testCodexMultipleRootsMissingMetadataAndSubagentOnlyStayUnresolved() throws {
        let roots: [String: CodexRollout.SessionMeta] = [first: .init(sessionId: first), second: .init(sessionId: second)]
        let multiple = identify(try pane(), processes: processes(), codex: [101: .init(locks: [first, second])], metadata: roots)
        XCTAssertTrue(multiple.agents.isEmpty)
        let missing = identify(try pane(), processes: processes(), codex: [101: .init(locks: [first, second])],
                               metadata: [first: .init(sessionId: first)])
        XCTAssertTrue(missing.agents.isEmpty)
        let subagent = identify(try pane(), processes: processes(), codex: [101: .init(locks: [first])],
                                metadata: [first: .init(sessionId: first, threadSource: "subagent")])
        XCTAssertTrue(subagent.agents.isEmpty)
    }

    func testOldOwnershipCreatesCandidateButCannotAssertRememberedSession() throws {
        let record = SessionOwnershipRecord(sessionId: first, harness: "codex", pid: 101,
            paneId: "%1", socketName: "tb", sessionName: "work", paneTty: "/dev/ttys001")
        let unresolved = identify(try pane(), processes: processes("node"), ownership: [record])
        XCTAssertTrue(unresolved.agents.isEmpty)
        XCTAssertEqual(unresolved.candidateHarnesses, ["codex"])
        let moved = identify(try pane(), processes: processes("node"), ownership: [record],
            codex: [101: .init(locks: [second])], metadata: [second: .init(sessionId: second)])
        XCTAssertEqual(moved.agents.map(\.sessionId), [second])
    }

    func testSnapshotRoundTripCarriesVersionTimeAndNoProcessPayloadFields() throws {
        let snapshot = TmuxFleet.Snapshot(schemaVersion: 1, snapshotId: first, capturedAt: 12345.5,
            servers: [.init(socketPath: socket, status: "ok", error: nil)], panes: [try pane()], warnings: [])
        let data = try JSONEncoder().encode(snapshot)
        XCTAssertEqual(try JSONDecoder().decode(TmuxFleet.Snapshot.self, from: data), snapshot)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(object["capturedAt"] as? Double, 12345.5)
        XCTAssertEqual(object["schemaVersion"] as? Int, 1)
        let panes = try XCTUnwrap(object["panes"] as? [[String: Any]])
        for field in ["argv", "arguments", "environment", "scrollback", "rollout", "content"] {
            XCTAssertNil(panes[0][field])
        }
    }
}
