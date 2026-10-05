import Foundation

/// The lifecycle of the bridge as the UI shows it.
public enum BridgeStatus: Sendable, Hashable {
    case stopped
    case starting
    case ready(Hello)
    case failed(String)

    public var isReady: Bool {
        if case .ready = self { return true }
        return false
    }

    public var hello: Hello? {
        if case .ready(let hello) = self { return hello }
        return nil
    }

    /// Short text for a status bar.
    public var label: String {
        switch self {
        case .stopped: "Stopped"
        case .starting: "Starting"
        case .ready(let hello): "Ready (renderer \(hello.rendererVersion))"
        case .failed(let message): "Failed: \(message)"
        }
    }
}

/// The client side of PROTOCOL.md: one bridge process, requests correlated by id.
///
/// Calls may run concurrently; the bridge answers them one at a time, in order. Every call
/// has a timeout. The client keeps the last `logCapacity` log lines (bridge stderr plus
/// its own notes) for a log view.
public actor BridgeClient {
    /// The `bridge_version` this client implements.
    public static let supportedBridgeVersion = 1
    public static let defaultTimeout: Duration = .seconds(30)
    /// `uv run` may have to prepare the environment on the first start.
    public static let defaultStartTimeout: Duration = .seconds(180)
    /// Number of stderr lines carried by `BridgeError.processExited`.
    public static let stderrTailLines = 20
    /// The most stdout lines that are not responses (blank lines included) one bridge may
    /// write: a bridge (or launcher) that writes more is broken and is ended, so that its
    /// output is not buffered without bound (PROTOCOL.md, "Envelope"). A response to a
    /// request that was never sent, or that was already answered, counts too.
    public static let maxStrayStdoutLines = 1_000
    /// stderr lines logged per second at most; the rest of that second is counted in one
    /// log note (the stderr tail of `processExited` still gets every line).
    public static let maxStderrLogLinesPerSecond = 200

    public typealias TransportFactory = @Sendable () throws -> any BridgeTransport

    private struct PendingCall {
        let cmd: String
        let continuation: CheckedContinuation<Data, any Error>
        var timeoutTask: Task<Void, Never>?
    }

    private enum Phase { case normal, starting, stopping }

    private let makeTransport: TransportFactory
    public private(set) var status: BridgeStatus = .stopped
    /// Lines on stdout that were not protocol messages, and other protocol problems.
    public private(set) var protocolViolationCount = 0

    private var transport: (any BridgeTransport)?
    private var generation = 0
    private var nextID = 1
    private var pending: [Int: PendingCall] = [:]
    /// Requests of the current bridge that the client gave up on before their answer
    /// (timeout, cancellation), by id, with their command. The bridge still answers each
    /// once: that late answer is logged and ignored. Any other answer whose id is not
    /// pending is a stray stdout line.
    private var unansweredCalls: [Int: String] = [:]
    private var isStopping = false
    /// The start in progress, which concurrent `start()` calls share. A stop ends it and
    /// clears it, so a later `start()` (as in `restart()`) launches a new bridge.
    private var startTask: Task<Hello, any Error>?
    /// Counts stops: a start whose bridge was not launched before a later stop never
    /// launches it.
    private var stopCount = 0
    private var stopTask: Task<Void, Never>?
    /// The termination of the last bridge that `abandonBridge` ended. A later start or
    /// stop waits for it: two bridges of one client never run at once, and `stop()`
    /// returns once every bridge of this client has ended.
    private var abandonedTermination: Task<Void, Never>?
    private var log: BoundedBuffer<BridgeLogEntry>
    private var stderrTail = BoundedBuffer<String>(capacity: BridgeClient.stderrTailLines)
    /// stdout lines of the current bridge that were not responses.
    private var strayStdoutLines = 0
    /// The current second of stderr logging, the lines logged in it, and those not logged.
    private var stderrWindowStart: ContinuousClock.Instant?
    private var stderrWindowLines = 0
    private var stderrUnlogged = 0
    private var nextLogID = 1
    private var statusSubscribers: [UUID: AsyncStream<BridgeStatus>.Continuation] = [:]
    private var logSubscribers: [UUID: AsyncStream<BridgeLogEntry>.Continuation] = [:]

    /// A client whose transports come from `transportFactory` (one per start).
    public init(logCapacity: Int = 500, transportFactory: @escaping TransportFactory) {
        self.makeTransport = transportFactory
        self.log = BoundedBuffer(capacity: max(1, logCapacity))
    }

    /// A client for the real bridge process.
    public init(configuration: ProcessBridgeTransport.Configuration, logCapacity: Int = 500) {
        self.init(logCapacity: logCapacity) { ProcessBridgeTransport(configuration: configuration) }
    }

    deinit {
        transport?.closeInput()
    }

    /// `hello` of the running bridge, when ready.
    public var hello: Hello? { status.hello }

    // MARK: Lifecycle

    /// Launches the bridge and performs the `hello` handshake. Returns at once when the
    /// bridge is already ready; concurrent callers share one start.
    @discardableResult
    public func start(timeout: Duration = BridgeClient.defaultStartTimeout) async throws -> Hello {
        if let stopTask { await stopTask.value }
        if case .ready(let hello) = status, transport != nil { return hello }
        if let startTask { return try await startTask.value }
        let stops = stopCount
        let task = Task { try await self.performStart(timeout: timeout, stops: stops) }
        startTask = task
        // Only this start's own task is cleared: after a stop, `startTask` may already be
        // the next start's.
        defer { if startTask == task { startTask = nil } }
        return try await withTaskCancellationHandler {
            try await task.value
        } onCancel: {
            task.cancel()
        }
    }

    /// Asks the bridge to shut down (`shutdown`), then makes sure the process has ended.
    /// Pending calls fail. The status becomes `.stopped`.
    public func stop() async {
        if let stopTask {
            await stopTask.value
            return
        }
        let task = Task { await self.performStop() }
        stopTask = task
        await task.value
        stopTask = nil
    }

    /// `stop()` then `start()`. A start in progress (the bridge is still starting) ends
    /// with the stop, and a new bridge is launched.
    @discardableResult
    public func restart(timeout: Duration = BridgeClient.defaultStartTimeout) async throws -> Hello {
        await stop()
        return try await start(timeout: timeout)
    }

    private func performStart(timeout: Duration, stops: Int) async throws -> Hello {
        await awaitAbandonedBridge()
        // A stop that came after this start was asked for ends it before any launch.
        guard stops == stopCount else { throw BridgeError.notRunning }
        isStopping = false
        setStatus(.starting)
        let newTransport: any BridgeTransport
        let events: AsyncStream<BridgeTransportEvent>
        do {
            newTransport = try makeTransport()
            events = try newTransport.start()
        } catch {
            let bridgeError = (error as? BridgeError) ?? .launchFailed(error.localizedDescription)
            appendLog(.client, bridgeError.message)
            setStatus(.failed(bridgeError.message))
            throw bridgeError
        }
        generation += 1
        let gen = generation
        transport = newTransport
        stderrTail.removeAll()
        strayStdoutLines = 0
        unansweredCalls.removeAll()
        appendLog(.client, "Started the bridge.")
        Task { [weak self] in
            for await event in events {
                await self?.handle(event, generation: gen)
            }
            await self?.eventsEnded(generation: gen)
        }
        do {
            let data = try await sendRaw(cmd: "hello", args: Data("{}".utf8), timeout: timeout, phase: .starting)
            let hello = try BridgeCoding.decodeResult(Hello.self, from: data, cmd: "hello")
            guard hello.bridgeVersion == Self.supportedBridgeVersion else {
                throw BridgeError.protocolViolation(
                    "hello: bridge_version is \(hello.bridgeVersion), this app implements \(Self.supportedBridgeVersion)")
            }
            guard gen == generation, !isStopping, stops == stopCount else { throw BridgeError.notRunning }
            appendLog(
                .client,
                "Bridge ready: renderer \(hello.rendererVersion), validator \(hello.validatorVersion), "
                    + "Python \(hello.python), numpy \(hello.numpy).")
            setStatus(.ready(hello))
            return hello
        } catch {
            if gen == generation, !isStopping {
                let cancelled = error is CancellationError
                let message = (error as? BridgeError)?.message
                    ?? (cancelled ? "Start cancelled." : error.localizedDescription)
                appendLog(.client, message)
                generation += 1
                let failed = transport === newTransport ? newTransport : nil
                if failed != nil { transport = nil }
                // The state changes come before the wait for the process to end (up to the
                // grace period and more for an unresponsive `uv`): the actor is re-entrant
                // there, and a stop or a new start (`restart()`) may run meanwhile. After
                // the wait this start changes nothing, so it can neither fail the new
                // start's pending hello nor overwrite its status.
                failAllPending(.notRunning)
                setStatus(cancelled ? .stopped : .failed(message))
                await failed?.terminate(gracePeriod: .seconds(1))
            }
            throw error
        }
    }

    private func performStop() async {
        // The start in progress, if any, ends here: its hello fails below (or it has not
        // launched yet and never will), and no later `start()` may join it.
        stopCount += 1
        startTask = nil
        guard let current = transport else {
            await awaitAbandonedBridge()
            if status != .stopped { setStatus(.stopped) }
            return
        }
        isStopping = true
        if case .ready = status {
            _ = try? await sendRaw(cmd: "shutdown", args: Data("{}".utf8), timeout: .seconds(3), phase: .stopping)
        }
        await current.terminate(gracePeriod: .seconds(2))
        if let still = transport, still === current { transport = nil }
        generation += 1
        failAllPending(.notRunning)
        appendLog(.client, "Stopped the bridge.")
        isStopping = false
        setStatus(.stopped)
    }

    // MARK: Calls

    /// Sends `cmd` with `args` and decodes the `result` as `R`.
    ///
    /// Throws `BridgeError`: `.engine` for an engine refusal (`ok: false`), `.timeout`,
    /// `.processExited`, `.notRunning`, `.protocolViolation` or `.decoding`; and
    /// `CancellationError` when the calling task is cancelled.
    public nonisolated func call<A: Encodable, R: Decodable>(
        _ cmd: String, _ args: A, timeout: Duration = BridgeClient.defaultTimeout
    ) async throws -> R {
        let argsData: Data
        do {
            argsData = try BridgeCoding.makeEncoder().encode(args)
        } catch {
            throw BridgeError.invalidArguments("\(cmd): \(error.localizedDescription)")
        }
        let line = try await sendRaw(cmd: cmd, args: argsData, timeout: timeout, phase: .normal)
        return try BridgeCoding.decodeResult(R.self, from: line, cmd: cmd)
    }

    private func sendRaw(cmd: String, args: Data, timeout: Duration, phase: Phase) async throws -> Data {
        guard let transport else { throw BridgeError.notRunning }
        if phase == .normal {
            guard case .ready = status, !isStopping else { throw BridgeError.notRunning }
        }
        let id = nextID
        nextID += 1
        let line = BridgeCoding.requestLine(id: id, cmd: cmd, argsJSON: args)
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Data, any Error>) in
                if Task.isCancelled {
                    continuation.resume(throwing: CancellationError())
                    return
                }
                let timeoutTask = Task { [weak self] in
                    do {
                        try await Task.sleep(for: timeout)
                    } catch {
                        return
                    }
                    await self?.expire(id: id)
                }
                pending[id] = PendingCall(cmd: cmd, continuation: continuation, timeoutTask: timeoutTask)
                do {
                    try transport.send(line)
                } catch {
                    if let call = pending.removeValue(forKey: id) {
                        call.timeoutTask?.cancel()
                        call.continuation.resume(throwing: (error as? BridgeError) ?? BridgeError.notRunning)
                    }
                }
            }
        } onCancel: {
            Task { await self.cancelCall(id: id) }
        }
    }

    private func expire(id: Int) {
        guard let call = pending.removeValue(forKey: id) else { return }
        unansweredCalls[id] = call.cmd
        appendLog(.client, "Request \(id) (\(call.cmd)) timed out.")
        call.continuation.resume(throwing: BridgeError.timeout(cmd: call.cmd))
    }

    private func cancelCall(id: Int) {
        guard let call = pending.removeValue(forKey: id) else { return }
        unansweredCalls[id] = call.cmd
        call.timeoutTask?.cancel()
        call.continuation.resume(throwing: CancellationError())
    }

    private func failAllPending(_ error: BridgeError) {
        let calls = pending
        pending.removeAll()
        for (_, call) in calls.sorted(by: { $0.key < $1.key }) {
            call.timeoutTask?.cancel()
            call.continuation.resume(throwing: error)
        }
    }

    // MARK: Events

    private func handle(_ event: BridgeTransportEvent, generation gen: Int) {
        guard gen == generation else { return }
        switch event {
        case .stdoutLine(let data):
            handleStdout(data)
        case .stderrLine(let text):
            stderrTail.append(text)
            logStderr(text)
        case .stdoutLineTooLong(let limit):
            abandonBridge("the bridge wrote a stdout line longer than \(limit) bytes, which is no protocol message")
        case .exited(let status):
            handleExit(status: status)
        }
    }

    /// Logs a stderr line, at most `maxStderrLogLinesPerSecond` per second.
    private func logStderr(_ text: String) {
        let now = ContinuousClock.now
        if let start = stderrWindowStart, now - start < .seconds(1) {
            stderrWindowLines += 1
        } else {
            noteUnloggedStderr()
            stderrWindowStart = now
            stderrWindowLines = 1
        }
        if stderrWindowLines <= Self.maxStderrLogLinesPerSecond {
            appendLog(.stderr, text)
        } else {
            stderrUnlogged += 1
        }
    }

    private func noteUnloggedStderr() {
        guard stderrUnlogged > 0 else { return }
        appendLog(
            .client,
            "\(stderrUnlogged) more stderr line(s) were not logged (at most \(Self.maxStderrLogLinesPerSecond) per second).")
        stderrUnlogged = 0
    }

    /// A stdout line that is not a response (blank, not JSON, without a usable id, or for
    /// a request that was never sent or was answered already).
    private func noteStrayStdoutLine() {
        strayStdoutLines += 1
        if strayStdoutLines > Self.maxStrayStdoutLines {
            abandonBridge("the bridge wrote more than \(Self.maxStrayStdoutLines) stdout lines that are not protocol messages")
        }
    }

    /// Ends a bridge that broke the protocol beyond repair (a stdout flood, an overlong
    /// line): its pending calls fail, the status becomes `.failed`, and the process is
    /// terminated at once (the transport drops its further stdout meanwhile); a later
    /// `start()` or `stop()` waits until it has exited. A stop in progress ends it anyway.
    private func abandonBridge(_ reason: String) {
        guard let current = transport, !isStopping else { return }
        let error = BridgeError.protocolViolation(reason)
        noteUnloggedStderr()
        appendLog(.client, "\(error.message). Stopping the bridge.")
        transport = nil
        generation += 1
        failAllPending(error)
        setStatus(.failed(error.message))
        let earlier = abandonedTermination
        abandonedTermination = Task {
            await earlier?.value
            await current.terminate(gracePeriod: .milliseconds(500))
        }
    }

    /// Waits until the bridges that `abandonBridge` ended have exited.
    private func awaitAbandonedBridge() async {
        guard let termination = abandonedTermination else { return }
        await termination.value
        if abandonedTermination == termination { abandonedTermination = nil }
    }

    private func eventsEnded(generation gen: Int) {
        guard gen == generation, transport != nil else { return }
        handleExit(status: -1)
    }

    private func handleStdout(_ data: Data) {
        if data.allSatisfy({ $0 == 0x20 || $0 == 0x09 || $0 == 0x0D }) {
            noteStrayStdoutLine()  // tolerated, but counted
            return
        }
        guard let header = try? BridgeCoding.makeDecoder().decode(ResponseHeader.self, from: data) else {
            protocolViolation("stdout line is not a JSON object: \(Self.preview(data))", source: .stdout)
            noteStrayStdoutLine()
            return
        }
        if let id = header.id {
            guard let call = pending.removeValue(forKey: id) else {
                if let cmd = unansweredCalls.removeValue(forKey: id) {
                    appendLog(.client, "Ignored the late response to request \(id) (\(cmd)), which had ended.")
                } else {
                    // Never sent, or answered already: a bridge that repeats such lines
                    // must not get around the stray-line limit.
                    protocolViolation(
                        "response to request \(id), which was never sent or was answered already: "
                            + Self.preview(data), source: .stdout)
                    noteStrayStdoutLine()
                }
                return
            }
            call.timeoutTask?.cancel()
            call.continuation.resume(returning: data)
        } else if header.hasIDKey, let error = header.error {
            // The bridge answers requests in order, so an answer without an id (a request
            // line it could not read) belongs to the oldest pending request.
            guard let oldest = pending.keys.min(), let call = pending.removeValue(forKey: oldest) else {
                protocolViolation("error without a request id: \(error.bridgeError.message)", source: .client)
                noteStrayStdoutLine()
                return
            }
            appendLog(.client, "An error without an id (\(error.code ?? error.type)) was matched to request \(oldest) (\(call.cmd)).")
            call.timeoutTask?.cancel()
            call.continuation.resume(throwing: error.bridgeError)
        } else {
            protocolViolation("response without a usable id: \(Self.preview(data))", source: .stdout)
            noteStrayStdoutLine()
        }
    }

    private func handleExit(status exitStatus: Int32) {
        noteUnloggedStderr()
        let tail = stderrTail.elements.joined(separator: "\n")
        let error = BridgeError.processExited(status: exitStatus, stderrTail: tail)
        appendLog(.client, "The bridge exited with status \(exitStatus).")
        transport = nil
        generation += 1
        failAllPending(error)
        if !isStopping, status != .stopped {
            setStatus(.failed(error.message))
        }
    }

    private func protocolViolation(_ text: String, source: BridgeLogEntry.Source) {
        protocolViolationCount += 1
        appendLog(source, "Protocol violation: \(text)")
    }

    private static func preview(_ data: Data) -> String {
        let text = String(decoding: data.prefix(200), as: UTF8.self)
        return data.count > 200 ? text + "..." : text
    }

    // MARK: Status and log

    /// The current status first, then every change, until the stream is dropped.
    public func statusUpdates() -> AsyncStream<BridgeStatus> {
        let (stream, continuation) = AsyncStream.makeStream(
            of: BridgeStatus.self, bufferingPolicy: .bufferingNewest(64))
        let key = UUID()
        statusSubscribers[key] = continuation
        continuation.yield(status)
        continuation.onTermination = { [weak self] _ in
            Task { await self?.removeStatusSubscriber(key) }
        }
        return stream
    }

    /// Every new log entry, until the stream is dropped (use `logEntries()` for history).
    public func logUpdates() -> AsyncStream<BridgeLogEntry> {
        let (stream, continuation) = AsyncStream.makeStream(
            of: BridgeLogEntry.self, bufferingPolicy: .bufferingNewest(1000))
        let key = UUID()
        logSubscribers[key] = continuation
        continuation.onTermination = { [weak self] _ in
            Task { await self?.removeLogSubscriber(key) }
        }
        return stream
    }

    /// The last `logCapacity` log entries, oldest first.
    public func logEntries() -> [BridgeLogEntry] { log.elements }

    /// The last stderr lines (up to `stderrTailLines`), joined with newlines.
    public func recentStderr() -> String { stderrTail.elements.joined(separator: "\n") }

    public func clearLog() { log.removeAll() }

    private func removeStatusSubscriber(_ key: UUID) { statusSubscribers[key] = nil }
    private func removeLogSubscriber(_ key: UUID) { logSubscribers[key] = nil }

    private func setStatus(_ newStatus: BridgeStatus) {
        guard newStatus != status else { return }
        status = newStatus
        for continuation in statusSubscribers.values { continuation.yield(newStatus) }
    }

    private func appendLog(_ source: BridgeLogEntry.Source, _ text: String) {
        let entry = BridgeLogEntry(id: nextLogID, date: Date(), source: source, text: text)
        nextLogID += 1
        log.append(entry)
        for continuation in logSubscribers.values { continuation.yield(entry) }
    }
}
