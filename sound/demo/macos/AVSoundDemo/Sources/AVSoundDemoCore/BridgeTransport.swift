import Foundation
import Synchronization

/// What a transport reports while the bridge runs.
public enum BridgeTransportEvent: Sendable, Hashable {
    /// One stdout line without its newline (a protocol message).
    case stdoutLine(Data)
    /// One stderr line without its newline (diagnostics).
    case stderrLine(String)
    /// A stdout line was longer than `limit` bytes: it is no protocol message, and no
    /// further stdout line is delivered.
    case stdoutLineTooLong(limit: Int)
    /// The bridge ended: exit status, or 128 + signal number. Always the last event.
    case exited(status: Int32)
}

/// A line-oriented connection to one bridge process.
///
/// `start()` is called once per transport; the client makes a new transport for every
/// (re)start. `send` must be non-blocking and keep the order of calls.
public protocol BridgeTransport: AnyObject, Sendable {
    /// Launches the bridge and returns its events. The stream finishes after `.exited`.
    func start() throws -> AsyncStream<BridgeTransportEvent>
    /// Queues one request line (without newline) for stdin.
    func send(_ line: Data) throws
    /// Closes stdin after the queued lines (the bridge exits at end of input).
    func closeInput()
    /// Ends the bridge: closes stdin, waits up to `gracePeriod` for it to exit, then
    /// terminates it (SIGTERM, then SIGKILL). Returns when it has exited.
    func terminate(gracePeriod: Duration) async
}

/// A request line as the bridge sees it (for in-memory bridges and tests).
public struct BridgeRequest: Decodable, Sendable, Hashable {
    public let id: Int
    public let cmd: String
    public let args: JSONValue

    public init(line: String) throws {
        self = try BridgeCoding.makeDecoder().decode(BridgeRequest.self, from: Data(line.utf8))
    }
}

/// A transport that runs entirely in memory: requests go to a handler, which answers by
/// emitting stdout lines. Used by tests and SwiftUI previews.
public final class InMemoryBridgeTransport: BridgeTransport {
    /// Called for every request line, outside any lock. Answer with `emitStdout`.
    public typealias Handler = @Sendable (_ line: String, _ transport: InMemoryBridgeTransport) -> Void

    private struct State {
        var handler: Handler?
        var continuation: AsyncStream<BridgeTransportEvent>.Continuation?
        var sentLines: [String] = []
        var startCount = 0
        var started = false
        var inputClosed = false
        var exited = false
        var startError: BridgeError?
    }

    private let state: Mutex<State>

    public init(handler: Handler? = nil) {
        state = Mutex(State(handler: handler))
    }

    /// Replaces the request handler.
    public func setHandler(_ handler: Handler?) {
        state.withLock { $0.handler = handler }
    }

    /// Makes the next `start()` fail with this error.
    public func failNextStart(with error: BridgeError?) {
        state.withLock { $0.startError = error }
    }

    /// Every request line received, in order.
    public var sentLines: [String] { state.withLock { $0.sentLines } }

    /// Every request received, decoded.
    public var sentRequests: [BridgeRequest] { sentLines.compactMap { try? BridgeRequest(line: $0) } }

    public var startCount: Int { state.withLock { $0.startCount } }
    public var isInputClosed: Bool { state.withLock { $0.inputClosed } }
    public var hasExited: Bool { state.withLock { $0.exited } }

    public func emitStdout(_ line: String) {
        state.withLock { s in
            guard !s.exited else { return }
            s.continuation?.yield(.stdoutLine(Data(line.utf8)))
        }
    }

    public func emitStderr(_ line: String) {
        state.withLock { s in
            guard !s.exited else { return }
            s.continuation?.yield(.stderrLine(line))
        }
    }

    /// Emits any event, for example `.stdoutLineTooLong` (`.exited` is `emitExit`).
    public func emitEvent(_ event: BridgeTransportEvent) {
        if case .exited(let status) = event {
            emitExit(status: status)
            return
        }
        state.withLock { s in
            guard !s.exited else { return }
            s.continuation?.yield(event)
        }
    }

    /// Simulates the process ending.
    public func emitExit(status: Int32) {
        state.withLock { s in
            guard !s.exited else { return }
            s.exited = true
            s.continuation?.yield(.exited(status: status))
            s.continuation?.finish()
            s.continuation = nil
        }
    }

    public func start() throws -> AsyncStream<BridgeTransportEvent> {
        let (stream, continuation) = AsyncStream.makeStream(
            of: BridgeTransportEvent.self, bufferingPolicy: .unbounded)
        try state.withLock { s in
            if let error = s.startError {
                s.startError = nil
                throw error
            }
            s.startCount += 1
            s.started = true
            s.exited = false
            s.inputClosed = false
            s.continuation = continuation
        }
        return stream
    }

    public func send(_ line: Data) throws {
        let text = String(decoding: line, as: UTF8.self)
        let handler = try state.withLock { s -> Handler? in
            guard s.started, !s.exited, !s.inputClosed else { throw BridgeError.notRunning }
            s.sentLines.append(text)
            return s.handler
        }
        handler?(text, self)
    }

    public func closeInput() {
        state.withLock { $0.inputClosed = true }
    }

    public func terminate(gracePeriod: Duration) async {
        closeInput()
        emitExit(status: 0)
    }
}
