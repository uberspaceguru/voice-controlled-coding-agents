import Foundation
import Darwin

/// An ACP agent running as a child process, spoken to over its stdin/stdout.
///
/// ACP ships stdio only, so owning a process is not an alternative to the
/// provider seam, it is this provider's transport. That distinction is the
/// whole reason `ACPProvider` is an `AgentProvider` and not a `HarnessAdapter`:
/// a harness owns a TERMINAL and reads a screen, while this owns a PIPE and
/// reads a protocol. Ruled 14 Sep, and the reason OpenCode was never allowed to
/// become a keystroke-scraped terminal.
public final class ACPProcessTransport: ACPTransport, @unchecked Sendable {

    private let process = Process()
    private let toAgent = Pipe()
    private let fromAgent = Pipe()
    private let lock = NSLock()
    private let lifecycleLock = NSLock()
    private var started = false
    private var closed = false
    private var stopStage = 0
    private var continuation: AsyncStream<Data>.Continuation?
    private var buffer = Data()

    /// Guards against an agent that never emits a newline. 1 MiB is far beyond
    /// any real ACP message and far below a leak that matters.
    private static let maxLine = 1 << 20

    public init(command: [String], cwd: String, environment: [String: String]? = nil) {
        process.executableURL = URL(fileURLWithPath: command[0])
        process.arguments = Array(command.dropFirst())
        process.currentDirectoryURL = URL(fileURLWithPath: cwd)
        process.standardInput = toAgent
        process.standardOutput = fromAgent
        // stderr is DISCARDED, not merged. An agent's diagnostics on the same
        // pipe as its protocol would be lines the parser has to survive, and
        // "survive a banner" is a weaker guarantee than "never see one".
        process.standardError = FileHandle.nullDevice
        if let environment { process.environment = environment }
    }

    public enum StartError: Error {
        case closed
        case alreadyStarted
    }

    public func start() throws {
        try lifecycleLock.withLock {
            guard !closed else { throw StartError.closed }
            guard !started else { throw StartError.alreadyStarted }
            try process.run()
            started = true
        }
    }

    /// The child's exit status once it has ended; nil before launch or while it runs.
    public var exitStatus: Int32? {
        lifecycleLock.withLock {
            guard started, !process.isRunning else { return nil }
            return process.terminationStatus
        }
    }

    public func write(_ line: Data) async throws {
        var out = line
        out.append(0x0A)
        try toAgent.fileHandleForWriting.write(contentsOf: out)
    }

    /// Hand-rolled line splitting over the raw handle, for the reason recorded
    /// on 14 Sep against a live SSE stream: `AsyncBytes.lines` yielded nothing
    /// at all there, with no error, while raw bytes delivered immediately. The
    /// same class of silence on a pipe would read as an agent with nothing to
    /// say, which is a state an idle agent legitimately has.
    public func lines() -> AsyncStream<Data> {
        AsyncStream { continuation in
            lock.lock()
            self.continuation = continuation
            lock.unlock()

            fromAgent.fileHandleForReading.readabilityHandler = { [weak self] handle in
                guard let self else { return }
                let chunk = handle.availableData
                guard !chunk.isEmpty else {
                    continuation.finish()
                    handle.readabilityHandler = nil
                    return
                }
                self.lock.lock()
                self.buffer.append(chunk)
                var lines: [Data] = []
                while let newline = self.buffer.firstIndex(of: 0x0A) {
                    let line = self.buffer[self.buffer.startIndex..<newline]
                    self.buffer = self.buffer[self.buffer.index(after: newline)...]
                    if !line.isEmpty { lines.append(Data(line)) }
                }
                if self.buffer.count > Self.maxLine { self.buffer.removeAll(keepingCapacity: false) }
                self.lock.unlock()
                for line in lines { continuation.yield(line) }
            }

            continuation.onTermination = { [weak self] _ in
                self?.fromAgent.fileHandleForReading.readabilityHandler = nil
            }
        }
    }

    public func close() async {
        closePipes()
        requestStop(stage: 2)
    }

    /// Stop this transport's immediate child and observe its exit within one
    /// total timeout. The manager opts into SIGINT for its pipeline cleanup;
    /// ordinary transports retain TERM-first shutdown. The final quarter of
    /// the budget is reserved for SIGKILL and observing termination.
    ///
    /// Never signals a process group or another session. True means the child
    /// has exited (or was never started), not merely that a signal was sent.
    /// False leaves the caller responsible for deciding whether it can quit.
    /// Cancellation still attempts escalation, but cannot report an unobserved
    /// exit as success. A nonpositive/nonfinite timeout requests shutdown without
    /// waiting. Closing before start permanently prevents a later launch.
    public func closeAndWait(timeout: TimeInterval = 2, interruptFirst: Bool = false) async -> Bool {
        let start = ProcessInfo.processInfo.systemUptime
        closePipes()
        requestStop(stage: interruptFirst ? 1 : 2)
        if hasExited { return true }
        guard timeout.isFinite, timeout > 0, (start + timeout).isFinite else { return false }

        if interruptFirst {
            if await waitForExit(until: start + timeout * 0.5) { return true }
            requestStop(stage: 2)
        }
        if await waitForExit(until: start + timeout * 0.75) { return true }
        requestStop(stage: 3)
        return await waitForExit(until: start + timeout)
    }

    private var hasExited: Bool {
        lifecycleLock.withLock { !started || !process.isRunning }
    }

    private func closePipes() {
        let firstClose = lifecycleLock.withLock {
            let first = !closed
            closed = true
            return first
        }
        guard firstClose else { return }
        fromAgent.fileHandleForReading.readabilityHandler = nil
        let stream = lock.withLock {
            let stream = continuation
            continuation = nil
            return stream
        }
        stream?.finish()
        try? toAgent.fileHandleForWriting.close()
    }

    /// Serializes repeated close calls so they never repeat or downgrade a
    /// signal. Process identity is retained, checked immediately before each
    /// signal, and never looked up by name or inherited from a tmux session.
    private func requestStop(stage: Int) {
        lifecycleLock.withLock {
            guard started, process.isRunning, stage > stopStage else { return }
            switch stage {
            case 1:
                process.interrupt()
            case 2:
                process.terminate()
            default:
                let pid = process.processIdentifier
                guard pid > 0, Darwin.kill(pid, SIGKILL) == 0 else { return }
            }
            stopStage = stage
        }
    }

    private func waitForExit(until deadline: TimeInterval) async -> Bool {
        while !hasExited {
            let remaining = deadline - ProcessInfo.processInfo.systemUptime
            guard remaining > 0 else { return false }
            do {
                try await Task.sleep(nanoseconds: UInt64(min(remaining, 0.01) * 1_000_000_000))
            } catch {
                return hasExited
            }
        }
        return true
    }
}
