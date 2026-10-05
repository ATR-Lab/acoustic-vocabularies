import Darwin
import Foundation
import Synchronization

/// Splits a byte stream into lines at `\n` (and drops one `\r` before it).
///
/// Only `\n` separates messages: JSON text may contain other Unicode line separators.
/// A line longer than `maxLineLength` bytes is returned cut to that length (marked
/// `isCut`) as soon as it is that long, and the rest of it, up to its newline, is
/// dropped: the splitter never holds more than `maxLineLength + 1` bytes.
public struct LineSplitter: Sendable {
    /// One line without its newline.
    public struct Line: Sendable, Hashable {
        public var data: Data
        /// The line was longer than `maxLineLength`; `data` is its start.
        public var isCut: Bool
    }

    public let maxLineLength: Int
    private var buffer = Data()
    /// The line being read was cut: its rest is dropped up to the next newline.
    private var skipping = false

    public init(maxLineLength: Int = .max) {
        self.maxLineLength = max(1, maxLineLength)
    }

    /// Appends a chunk and returns the complete lines it finishes (cut lines included).
    public mutating func append(_ chunk: Data) -> [Data] {
        appendLines(chunk).map(\.data)
    }

    /// Appends a chunk and returns the lines it finishes, and the start of a line that
    /// has just passed `maxLineLength`.
    public mutating func appendLines(_ chunk: Data) -> [Line] {
        var lines: [Line] = []
        var rest = chunk[...]
        while !rest.isEmpty {
            let newline = rest.firstIndex(of: 0x0A)
            if skipping {
                guard let newline else { return lines }
                skipping = false
                rest = rest[(newline + 1)...]
                continue
            }
            guard let newline else {
                // Unterminated: keep it, unless it is already too long (one more byte is
                // allowed: it may be the `\r` of a line of exactly the maximum length).
                if buffer.count + rest.count - 1 > maxLineLength {  // no overflow at `.max`
                    buffer.append(rest.prefix(maxLineLength - min(buffer.count, maxLineLength)))
                    lines.append(Line(data: Data(buffer.prefix(maxLineLength)), isCut: true))
                    buffer = Data()
                    skipping = true
                } else {
                    buffer.append(rest)
                }
                return lines
            }
            buffer.append(rest[rest.startIndex..<newline])
            let line = Self.trimCR(buffer[...])
            lines.append(
                line.count > maxLineLength
                    ? Line(data: Data(line.prefix(maxLineLength)), isCut: true) : Line(data: line, isCut: false))
            buffer = Data()
            rest = rest[(newline + 1)...]
        }
        return lines
    }

    /// The unterminated rest at end of input, if any (nothing for a line already cut).
    public mutating func finish() -> Data? {
        defer {
            buffer = Data()
            skipping = false
        }
        return buffer.isEmpty || skipping ? nil : Data(Self.trimCR(buffer[...]).prefix(maxLineLength))
    }

    private static func trimCR(_ slice: Data.SubSequence) -> Data {
        if let last = slice.last, last == 0x0D { return Data(slice.dropLast()) }
        return Data(slice)
    }
}

/// Runs the real bridge with Foundation `Process`:
/// `<uv> run --frozen --project <repo>/sound python <repo>/sound/demo/macos/bridge/av_sound_bridge.py`
/// with the repository as working directory.
public final class ProcessBridgeTransport: BridgeTransport {
    /// The longest stdout line (one response) taken from the bridge. No response comes
    /// near it (the largest carry one WAV of under a megabyte); a longer line ends the
    /// stream of stdout lines with `.stdoutLineTooLong`.
    public static let maxStdoutLineBytes = 64 << 20
    /// stderr lines are cut after this many bytes (diagnostics only).
    public static let maxStderrLineBytes = 64 << 10

    public struct Configuration: Sendable, Hashable {
        public var executableURL: URL
        public var arguments: [String]
        public var currentDirectoryURL: URL?
        public var environment: [String: String]

        public init(executableURL: URL, arguments: [String], currentDirectoryURL: URL?, environment: [String: String]) {
            self.executableURL = executableURL
            self.arguments = arguments
            self.currentDirectoryURL = currentDirectoryURL
            self.environment = environment
        }

        /// Path of the bridge script, relative to the repository root.
        public static let bridgeScriptRelativePath = "sound/demo/macos/bridge/av_sound_bridge.py"

        /// Python variables of the app's own environment that the bridge does not get:
        /// they could make it import `av_sound` (or anything else) from somewhere other
        /// than the chosen checkout's `sound/src` (`PYTHONPATH` entries come before the
        /// environment's site-packages), use another Python installation (`PYTHONHOME`),
        /// run startup code, enter interactive mode after the script (`PYTHONINSPECT`), or
        /// strip the engine's `assert` checks (`PYTHONOPTIMIZE`, which at level 2 also
        /// strips docstrings). PROTOCOL.md, "Engine location": the bridge also refuses an
        /// engine from elsewhere.
        public static let removedEnvironmentKeys: Set<String> = [
            "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "PYTHONINSPECT",
            "PYTHONEXECUTABLE", "PYTHONPLATLIBDIR", "PYTHONSAFEPATH", "PYTHONOPTIMIZE",
        ]

        /// The protocol's launch command for a uv executable and a repository root.
        ///
        /// `--frozen` (PROTOCOL.md, "Safety rules"): uv runs from `sound/uv.lock` as it is
        /// and never re-resolves or rewrites that tracked file, even when `pyproject.toml`
        /// or the user's uv settings no longer match it. The only write inside the work
        /// tree stays `sound/.venv` (ignored by git).
        public static func bridge(
            uv: URL, repoRoot: URL,
            baseEnvironment: [String: String] = ProcessInfo.processInfo.environment
        ) -> Configuration {
            let root = repoRoot.standardizedFileURL
            let project = root.appendingPathComponent("sound").path
            let script = root.appendingPathComponent(bridgeScriptRelativePath).path
            var environment = augmentedEnvironment(baseEnvironment)
            for key in removedEnvironmentKeys { environment[key] = nil }
            // No user site-packages either (the engine's environment does not use one).
            environment["PYTHONNOUSERSITE"] = "1"
            environment["PYTHONUNBUFFERED"] = "1"
            environment["PYTHONIOENCODING"] = "utf-8"
            environment["NO_COLOR"] = "1"
            // PROTOCOL.md, "Safety rules": no __pycache__ folders in the work tree.
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            return Configuration(
                executableURL: uv,
                arguments: ["run", "--frozen", "--project", project, "python", script],
                currentDirectoryURL: root,
                environment: environment)
        }

        /// `base` with the usual uv install directories appended to `PATH` (an app started
        /// from Finder gets a minimal `PATH`).
        public static func augmentedEnvironment(_ base: [String: String]) -> [String: String] {
            var environment = base
            let home = base["HOME"] ?? NSHomeDirectory()
            var path = (base["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin")
                .split(separator: ":").map(String.init)
            for dir in ["/opt/homebrew/bin", "/usr/local/bin", "\(home)/.local/bin", "\(home)/.cargo/bin",
                        "/usr/bin", "/bin"]
            where !path.contains(dir) {
                path.append(dir)
            }
            environment["PATH"] = path.joined(separator: ":")
            return environment
        }

        /// The command line, for the log.
        public var commandLine: String {
            ([executableURL.path] + arguments).map { arg in
                arg.contains(" ") ? "\"\(arg)\"" : arg
            }.joined(separator: " ")
        }
    }

    private struct State {
        var process: Process?
        var stdin: FileHandle?
        var continuation: AsyncStream<BridgeTransportEvent>.Continuation?
        var stdoutSplitter: LineSplitter
        var stderrSplitter: LineSplitter
        /// No more stdout lines are delivered (read and dropped): a line was too long, or
        /// `terminate` began (nobody waits for a response any more). So a bridge that
        /// floods stdout cannot fill the event stream while it is being stopped.
        var stdoutClosed = false
        var stdoutDone = false
        var stderrDone = false
        var exitStatus: Int32?
        var finished = false
        var started = false
    }

    public let configuration: Configuration
    /// The longest stdout and stderr lines of this transport (the defaults above; tests
    /// use smaller ones).
    public let stdoutLineLimit: Int
    public let stderrLineLimit: Int
    private let state: Mutex<State>
    private let writeQueue = DispatchQueue(label: "AVSoundDemoCore.ProcessBridgeTransport.stdin")

    public init(
        configuration: Configuration, stdoutLineLimit: Int = ProcessBridgeTransport.maxStdoutLineBytes,
        stderrLineLimit: Int = ProcessBridgeTransport.maxStderrLineBytes
    ) {
        self.configuration = configuration
        self.stdoutLineLimit = stdoutLineLimit
        self.stderrLineLimit = stderrLineLimit
        state = Mutex(State(
            stdoutSplitter: LineSplitter(maxLineLength: stdoutLineLimit),
            stderrSplitter: LineSplitter(maxLineLength: stderrLineLimit)))
    }

    private static let ignoreSIGPIPE: Void = {
        // A write to a bridge that has exited must fail with EPIPE, not kill the app.
        signal(SIGPIPE, SIG_IGN)
    }()

    public func start() throws -> AsyncStream<BridgeTransportEvent> {
        _ = Self.ignoreSIGPIPE
        let (stream, continuation) = AsyncStream.makeStream(
            of: BridgeTransportEvent.self, bufferingPolicy: .unbounded)
        let process = Process()
        process.executableURL = configuration.executableURL
        process.arguments = configuration.arguments
        process.currentDirectoryURL = configuration.currentDirectoryURL
        process.environment = configuration.environment
        let stdinPipe = Pipe()
        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        process.standardInput = stdinPipe
        process.standardOutput = stdoutPipe
        process.standardError = stderrPipe

        stdoutPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil }
            self?.received(data, stdout: true)
        }
        stderrPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil }
            self?.received(data, stdout: false)
        }
        process.terminationHandler = { [weak self] process in
            let status = process.terminationReason == .uncaughtSignal
                ? 128 + process.terminationStatus : process.terminationStatus
            self?.terminated(status: status)
        }

        try state.withLock { s in
            guard !s.started else { throw BridgeError.launchFailed("transport already started") }
            s.started = true
            s.continuation = continuation
            do {
                try process.run()
            } catch {
                s.finished = true
                s.continuation = nil
                stdoutPipe.fileHandleForReading.readabilityHandler = nil
                stderrPipe.fileHandleForReading.readabilityHandler = nil
                continuation.finish()
                throw BridgeError.launchFailed(
                    "\(configuration.executableURL.path): \(error.localizedDescription)")
            }
            s.process = process
            s.stdin = stdinPipe.fileHandleForWriting
        }
        return stream
    }

    public func send(_ line: Data) throws {
        let handle = try state.withLock { s -> FileHandle in
            guard let stdin = s.stdin, !s.finished else { throw BridgeError.notRunning }
            return stdin
        }
        let payload = line + Data([0x0A])
        writeQueue.async {
            // EPIPE means the bridge has exited; the exit event reports it.
            try? handle.write(contentsOf: payload)
        }
    }

    public func closeInput() {
        let handle = state.withLock { s -> FileHandle? in
            defer { s.stdin = nil }
            return s.stdin
        }
        guard let handle else { return }
        writeQueue.async { try? handle.close() }
    }

    /// Closes stdin, then sends SIGTERM, then SIGKILL, each after `gracePeriod`.
    ///
    /// SIGTERM goes to the launched process only (`kill(pid)`), not through
    /// `Process.terminate()`: Foundation starts the child as a process-group leader and
    /// `terminate()` signals that whole group. `uv run` forwards SIGTERM to the bridge, so
    /// the bridge would get it twice within milliseconds, and the second signal could cut
    /// short the removal of its temp directory.
    public func terminate(gracePeriod: Duration) async {
        state.withLock { $0.stdoutClosed = true }
        closeInput()
        if await waitForExit(within: gracePeriod) { return }
        state.withLock { s in
            if let process = s.process, process.isRunning { kill(process.processIdentifier, SIGTERM) }
        }
        if await waitForExit(within: gracePeriod) { return }
        state.withLock { s in
            if let process = s.process, process.isRunning { kill(process.processIdentifier, SIGKILL) }
        }
        _ = await waitForExit(within: gracePeriod)
    }

    /// Process ID of the running bridge (for the log).
    public var processIdentifier: Int32? {
        state.withLock { s in s.process.map { $0.processIdentifier } }
    }

    private func waitForExit(within duration: Duration) async -> Bool {
        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: duration)
        while clock.now < deadline {
            if state.withLock({ $0.exitStatus != nil }) { return true }
            try? await Task.sleep(for: .milliseconds(20))
        }
        return state.withLock { $0.exitStatus != nil }
    }

    private func received(_ data: Data, stdout: Bool) {
        state.withLock { s in
            guard !s.finished else { return }
            if data.isEmpty {
                if stdout {
                    if let rest = s.stdoutSplitter.finish(), !s.stdoutClosed {
                        s.continuation?.yield(.stdoutLine(rest))
                    }
                    s.stdoutDone = true
                } else {
                    if let rest = s.stderrSplitter.finish() {
                        s.continuation?.yield(.stderrLine(String(decoding: rest, as: UTF8.self)))
                    }
                    s.stderrDone = true
                }
                Self.finishIfDone(&s)
                return
            }
            if stdout {
                for line in s.stdoutSplitter.appendLines(data) where !s.stdoutClosed {
                    if line.isCut {
                        s.stdoutClosed = true
                        s.continuation?.yield(.stdoutLineTooLong(limit: stdoutLineLimit))
                    } else {
                        s.continuation?.yield(.stdoutLine(line.data))
                    }
                }
            } else {
                for line in s.stderrSplitter.appendLines(data) {
                    let text = String(decoding: line.data, as: UTF8.self)
                    s.continuation?.yield(
                        .stderrLine(line.isCut ? text + " [cut after \(stderrLineLimit) bytes]" : text))
                }
            }
        }
    }

    private func terminated(status: Int32) {
        state.withLock { s in
            s.exitStatus = status
            Self.finishIfDone(&s)
        }
        // A child that outlives the bridge could hold the pipes open: report the exit
        // anyway after a short grace period.
        DispatchQueue.global().asyncAfter(deadline: .now() + 2) { [weak self] in
            self?.state.withLock { s in
                s.stdoutDone = true
                s.stderrDone = true
                Self.finishIfDone(&s)
            }
        }
    }

    private static func finishIfDone(_ s: inout State) {
        guard !s.finished, s.stdoutDone, s.stderrDone, let status = s.exitStatus else { return }
        s.finished = true
        s.continuation?.yield(.exited(status: status))
        s.continuation?.finish()
        s.continuation = nil
        s.stdin = nil
    }
}
