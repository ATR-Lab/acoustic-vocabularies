import Foundation
import Synchronization
import Testing

@testable import AVSoundDemoCore

/// A request handler that answers `hello` and `shutdown` like the real bridge and passes
/// every other request to `other`.
func standardHandler(
    hello: String = Fixtures.hello,
    _ other: @escaping @Sendable (BridgeRequest, InMemoryBridgeTransport) -> Void = { _, _ in }
) -> InMemoryBridgeTransport.Handler {
    { line, transport in
        guard let request = try? BridgeRequest(line: line) else { return }
        switch request.cmd {
        case "hello":
            transport.emitStdout(reply(request.id, hello))
        case "shutdown":
            transport.emitStdout(reply(request.id, "{}"))
            transport.emitExit(status: 0)
        default:
            other(request, transport)
        }
    }
}

/// An in-memory bridge with `standardHandler`.
func fakeBridge(
    hello: String = Fixtures.hello,
    _ other: @escaping @Sendable (BridgeRequest, InMemoryBridgeTransport) -> Void = { _, _ in }
) -> InMemoryBridgeTransport {
    InMemoryBridgeTransport(handler: standardHandler(hello: hello, other))
}

/// A compact success line for `result` (JSON text).
func reply(_ id: Int, _ result: String) -> String {
    // swiftlint:disable:next force_try
    BridgeCoding.successLine(id: id, result: try! JSONValue(jsonString: result))
}

func startedClient(
    logCapacity: Int = 500, _ transport: InMemoryBridgeTransport
) async throws -> BridgeClient {
    let client = BridgeClient(logCapacity: logCapacity) { transport }
    try await client.start(timeout: .seconds(5))
    return client
}

@Suite("BridgeClient over an in-memory transport")
struct BridgeClientTests {
    @Test func handshakeMakesTheClientReady() async throws {
        let transport = fakeBridge()
        let client = BridgeClient { transport }
        #expect(await client.status == .stopped)
        let hello = try await client.start(timeout: .seconds(5))
        #expect(hello.bridgeVersion == 1)
        #expect(await client.status.isReady)
        #expect(await client.hello == hello)
        let first = try #require(transport.sentRequests.first)
        #expect(first.id == 1 && first.cmd == "hello" && first.args == [:])
        // A second start while ready returns the same hello without a new process.
        #expect(try await client.start() == hello)
        #expect(transport.startCount == 1)
    }

    @Test func idsIncreaseMonotonically() async throws {
        let transport = fakeBridge { request, t in t.emitStdout(reply(request.id, #"{"n":\#(request.id)}"#)) }
        let client = try await startedClient(transport)
        for _ in 0..<3 {
            let _: JSONValue = try await client.call("echo", EmptyArgs())
        }
        #expect(transport.sentRequests.map(\.id) == [1, 2, 3, 4])
        try await client.restart(timeout: .seconds(5))
        let _: JSONValue = try await client.call("echo", EmptyArgs())
        // Ids keep increasing across restarts (shutdown was 5, the new hello 6).
        #expect(transport.sentRequests.map(\.id) == [1, 2, 3, 4, 5, 6, 7])
        #expect(transport.sentRequests.map(\.cmd) == ["hello", "echo", "echo", "echo", "shutdown", "hello", "echo"])
    }

    @Test func outOfOrderResponsesReachTheirCallers() async throws {
        let held = Mutex<[BridgeRequest]>([])
        let transport = fakeBridge { request, t in
            let ready = held.withLock { list -> [BridgeRequest] in
                list.append(request)
                return list.count == 3 ? list : []
            }
            // Answer the three requests in reverse order once all have arrived.
            for r in ready.reversed() {
                t.emitStdout(reply(r.id, #"{"cmd":"\#(r.cmd)","id":\#(r.id)}"#))
            }
        }
        let client = try await startedClient(transport)
        async let a: JSONValue = client.call("a", EmptyArgs())
        async let b: JSONValue = client.call("b", EmptyArgs())
        async let c: JSONValue = client.call("c", EmptyArgs())
        let results = try await [a, b, c]
        #expect(results.map { $0["cmd"]?.stringValue } == ["a", "b", "c"])
        for result in results {
            let id = try #require(result["id"]?.intValue)
            let sent = try #require(transport.sentRequests.first { $0.id == id })
            #expect(sent.cmd == result["cmd"]?.stringValue)
        }
    }

    @Test func engineErrorsMapToBridgeError() async throws {
        let transport = fakeBridge { request, t in
            t.emitStdout(
                BridgeCoding.errorLine(
                    id: request.id, type: "HeldOutMessageError", code: "E_HELDOUT",
                    message: "K-a1-r2 is held out"))
        }
        let client = try await startedClient(transport)
        let atom = AtomReference(refID: "K-a1", recipe: .example)
        await #expect(
            throws: BridgeError.engine(
                type: "HeldOutMessageError", code: "E_HELDOUT", message: "K-a1-r2 is held out", details: nil)
        ) {
            _ = try await client.compose(action: atom, referent: atom, profile: .p1)
        }
        // The bridge keeps running after an engine error.
        #expect(await client.status.isReady)
    }

    @Test func unknownCommandError() async throws {
        let transport = fakeBridge { request, t in
            t.emitStdout(
                BridgeCoding.errorLine(id: request.id, type: "ProtocolError", code: "E_UNKNOWN_CMD", message: "nope"))
        }
        let client = try await startedClient(transport)
        do {
            let _: EmptyResult = try await client.call("frobnicate", EmptyArgs())
            Issue.record("expected an error")
        } catch let error as BridgeError {
            #expect(error.engineCode == "E_UNKNOWN_CMD")
            #expect(error.message == "ProtocolError E_UNKNOWN_CMD: nope")
        }
    }

    @Test func malformedLinesAreLoggedAndSkipped() async throws {
        let transport = fakeBridge { request, t in
            t.emitStdout("this is not json")
            t.emitStdout("[1, 2]")
            t.emitStdout(#"{"ok":true,"result":{}}"#)
            t.emitStdout("   ")
            t.emitStdout(reply(request.id, Fixtures.selfTest))
        }
        let client = try await startedClient(transport)
        let result = try await client.selfTest()
        #expect(result.ok)
        #expect(await client.protocolViolationCount == 3)
        let log = await client.logEntries()
        #expect(log.contains { $0.source == .stdout && $0.text.contains("this is not json") })
        #expect(await client.status.isReady)
    }

    @Test func wrongResultShapeIsADecodingError() async throws {
        let transport = fakeBridge { request, t in t.emitStdout(reply(request.id, #"{"ok":"yes"}"#)) }
        let client = try await startedClient(transport)
        await #expect(throws: BridgeError.decoding("self_test: wrong type at ok: expected Bool")) {
            _ = try await client.selfTest()
        }
    }

    @Test func timeoutThenLateResponseIsIgnored() async throws {
        let slow = Mutex<Int?>(nil)
        let transport = fakeBridge { request, t in
            if request.cmd == "slow" {
                slow.withLock { $0 = request.id }
            } else {
                t.emitStdout(reply(request.id, #"{"cmd":"\#(request.cmd)"}"#))
            }
        }
        let client = try await startedClient(transport)
        let clock = ContinuousClock()
        let started = clock.now
        await #expect(throws: BridgeError.timeout(cmd: "slow")) {
            let _: JSONValue = try await client.call("slow", EmptyArgs(), timeout: .milliseconds(100))
        }
        #expect(clock.now - started < .seconds(5))
        // The late answer is dropped; later calls still work.
        let lateID = try #require(slow.withLock { $0 })
        transport.emitStdout(reply(lateID, #"{"cmd":"slow"}"#))
        let next: JSONValue = try await client.call("fast", EmptyArgs())
        #expect(next["cmd"] == "fast")
        let log = await client.logEntries().map(\.text)
        #expect(log.contains("Request \(lateID) (slow) timed out."))
        #expect(log.contains("Ignored a response for request \(lateID), which is not pending."))
    }

    @Test func processExitFailsPendingCallsWithStderrTail() async throws {
        let transport = fakeBridge { _, t in
            t.emitStderr("Traceback (most recent call last):")
            t.emitStderr("RuntimeError: boom")
            t.emitExit(status: 1)
        }
        let client = try await startedClient(transport)
        await #expect(
            throws: BridgeError.processExited(
                status: 1, stderrTail: "Traceback (most recent call last):\nRuntimeError: boom")
        ) {
            _ = try await client.render(.example, profile: .p2)
        }
        let status = await client.status
        guard case .failed(let message) = status else {
            Issue.record("expected failed, got \(status)")
            return
        }
        #expect(message.contains("status 1"))
        #expect(message.contains("RuntimeError: boom"))
        await #expect(throws: BridgeError.notRunning) { _ = try await client.render(.example, profile: .p2) }
        // A restart brings it back.
        transport.setHandler(standardHandler())
        try await client.start(timeout: .seconds(5))
        #expect(await client.status.isReady)
    }

    @Test func exitDuringHandshake() async throws {
        let transport = InMemoryBridgeTransport { _, t in
            t.emitStderr("error: Failed to spawn: `python`")
            t.emitExit(status: 2)
        }
        let client = BridgeClient { transport }
        await #expect(throws: BridgeError.processExited(status: 2, stderrTail: "error: Failed to spawn: `python`")) {
            try await client.start(timeout: .seconds(5))
        }
        guard case .failed = await client.status else {
            Issue.record("expected failed")
            return
        }
    }

    @Test func notRunningBeforeStart() async throws {
        let client = BridgeClient { fakeBridge() }
        await #expect(throws: BridgeError.notRunning) { _ = try await client.grammar() }
    }

    @Test func launchFailure() async throws {
        let transport = fakeBridge()
        transport.failNextStart(with: .launchFailed("uv not found"))
        let client = BridgeClient { transport }
        await #expect(throws: BridgeError.launchFailed("uv not found")) { try await client.start() }
        #expect(await client.status == .failed("Could not start the bridge: uv not found"))
        try await client.start(timeout: .seconds(5))
        #expect(await client.status.isReady)
    }

    @Test func unsupportedBridgeVersion() async throws {
        let transport = fakeBridge(hello: Fixtures.hello.replacingOccurrences(
            of: #""bridge_version":1"#, with: #""bridge_version":2"#))
        let client = BridgeClient { transport }
        await #expect(throws: BridgeError.self) { try await client.start(timeout: .seconds(5)) }
        guard case .failed(let message) = await client.status else {
            Issue.record("expected failed")
            return
        }
        #expect(message.contains("bridge_version is 2"))
        #expect(transport.hasExited)
    }

    @Test func handshakeTimeout() async throws {
        let transport = InMemoryBridgeTransport()  // never answers
        let client = BridgeClient { transport }
        await #expect(throws: BridgeError.timeout(cmd: "hello")) {
            try await client.start(timeout: .milliseconds(100))
        }
        #expect(transport.hasExited)
        #expect(await client.status == .failed(BridgeError.timeout(cmd: "hello").message))
    }

    @Test func errorWithoutIDGoesToTheOldestPendingRequest() async throws {
        let transport = fakeBridge { _, t in
            t.emitStdout(#"{"id":null,"ok":false,"error":{"type":"ProtocolError","code":"E_BAD_REQUEST","message":"bad line"}}"#)
        }
        let client = try await startedClient(transport)
        do {
            let _: JSONValue = try await client.call("x", EmptyArgs())
            Issue.record("expected an error")
        } catch let error as BridgeError {
            #expect(error.engineCode == "E_BAD_REQUEST")
        }
    }

    @Test func stopSendsShutdownAndFailsPendingCalls() async throws {
        let transport = fakeBridge { _, _ in }  // "slow" never answers
        let client = try await startedClient(transport)
        let pendingCall = Task { () -> BridgeError? in
            do {
                let _: JSONValue = try await client.call("slow", EmptyArgs(), timeout: .seconds(30))
                return nil
            } catch {
                return error as? BridgeError
            }
        }
        while transport.sentRequests.count < 2 { try await Task.sleep(for: .milliseconds(5)) }
        await client.stop()
        #expect(await client.status == .stopped)
        #expect(transport.sentRequests.last?.cmd == "shutdown")
        let error = await pendingCall.value
        switch error {
        case .processExited(status: 0, _), .notRunning: break
        default: Issue.record("unexpected \(String(describing: error))")
        }
        await #expect(throws: BridgeError.notRunning) { _ = try await client.hello() }
        await client.stop()  // stopping twice is harmless
        #expect(await client.status == .stopped)
    }

    /// Restart while the bridge is still starting (its hello not answered yet): the stop
    /// ends that start, and the start after it launches a new bridge instead of joining
    /// the start that the stop ended.
    @Test func restartWhileStartingLaunchesANewBridge() async throws {
        let hellos = Mutex(0)
        let transport = InMemoryBridgeTransport { line, t in
            guard let request = try? BridgeRequest(line: line) else { return }
            switch request.cmd {
            case "hello":
                let count = hellos.withLock { n -> Int in
                    n += 1
                    return n
                }
                if count > 1 { t.emitStdout(reply(request.id, Fixtures.hello)) }  // not the first
            case "shutdown":
                t.emitStdout(reply(request.id, "{}"))
                t.emitExit(status: 0)
            default:
                break
            }
        }
        let client = BridgeClient { transport }
        let first = Task { try await client.start(timeout: .seconds(30)) }
        while transport.sentRequests.isEmpty { try await Task.sleep(for: .milliseconds(5)) }
        #expect(await client.status == .starting)
        let hello = try await client.restart(timeout: .seconds(5))
        #expect(hello.bridgeVersion == 1)
        #expect(await client.status.isReady)
        #expect(transport.startCount == 2)
        #expect(transport.sentRequests.map(\.cmd) == ["hello", "hello"])
        await #expect(throws: BridgeError.notRunning) { try await first.value }
        // Nothing stale is left to join: a later start returns the running bridge.
        #expect(try await client.start(timeout: .seconds(5)) == hello)
        #expect(transport.startCount == 2)
        await client.stop()
        #expect(await client.status == .stopped)
    }

    @Test func cancellationEndsTheCall() async throws {
        let transport = fakeBridge { _, _ in }
        let client = try await startedClient(transport)
        let task = Task {
            let _: JSONValue = try await client.call("slow", EmptyArgs(), timeout: .seconds(30))
        }
        while transport.sentRequests.count < 2 { try await Task.sleep(for: .milliseconds(5)) }
        task.cancel()
        await #expect(throws: CancellationError.self) { try await task.value }
        #expect(await client.status.isReady)
    }

    @Test func statusUpdatesStream() async throws {
        let transport = fakeBridge()
        let client = BridgeClient { transport }
        let stream = await client.statusUpdates()
        try await client.start(timeout: .seconds(5))
        await client.stop()
        var seen: [String] = []
        for await status in stream {
            seen.append(status.label.components(separatedBy: " ").first ?? "")
            if seen.count == 4 { break }
        }
        #expect(seen == ["Stopped", "Starting", "Ready", "Stopped"])
    }

    @Test func logIsBoundedAndStreamed() async throws {
        let transport = fakeBridge { request, t in
            for i in 1...12 { t.emitStderr("line \(i)") }
            t.emitStdout(reply(request.id, "{}"))
        }
        let client = try await startedClient(logCapacity: 5, transport)
        let updates = await client.logUpdates()
        let _: EmptyResult = try await client.call("noisy", EmptyArgs())
        let log = await client.logEntries()
        #expect(log.count == 5)
        #expect(log.last?.text == "line 12")
        #expect(log.allSatisfy { $0.source == .stderr })
        #expect(zip(log, log.dropFirst()).allSatisfy { $0.id < $1.id })
        var streamed: [String] = []
        for await entry in updates {
            streamed.append(entry.text)
            if streamed.count == 12 { break }
        }
        #expect(streamed.first == "line 1")
        #expect(await client.recentStderr().hasSuffix("line 11\nline 12"))
        await client.clearLog()
        #expect(await client.logEntries().isEmpty)
    }

    @Test func typedCommandsSendTheDocumentedArguments() async throws {
        let results: [String: String] = [
            "self_test": Fixtures.selfTest, "render": Fixtures.render, "random_recipe": Fixtures.randomRecipe,
            "features": Fixtures.features, "distance": Fixtures.distance, "validate": Fixtures.validationRejected,
            "nearest": "null", "grammar": Fixtures.grammar, "compose": Fixtures.compose,
            "composite_hash": Fixtures.compositeHash, "synthetic_book": Fixtures.syntheticBook,
            "nonlexical_list": Fixtures.nonlexicalList, "nonlexical_get": Fixtures.nonlexicalGet,
            "vectors_check": Fixtures.vectorsCheck, "golden_check": Fixtures.goldenCheck,
            "store_reset": Fixtures.storeReset, "store_create": Fixtures.storeCreate,
            "store_commit": Fixtures.storeCommit, "store_list": Fixtures.storeList,
            "store_records": Fixtures.storeRecords, "store_freeze": Fixtures.storeFreeze,
            "store_verify": Fixtures.storeVerify, "store_tamper": Fixtures.storeTamper,
            "fallback_demo": Fixtures.fallbackDemo, "fallback_scan": Fixtures.fallbackScan,
            "package_demo": Fixtures.packageDemo,
        ]
        let transport = fakeBridge { request, t in
            guard let result = results[request.cmd] else { return }
            t.emitStdout(reply(request.id, result))
        }
        let client = try await startedClient(transport)
        let atom = AtomReference(refID: "K-a1", recipe: .example)
        func lastArgs() -> JSONValue { transport.sentRequests.last?.args ?? [:] }

        _ = try await client.selfTest()
        #expect(lastArgs() == [:])
        _ = try await client.render(.example, profile: .p2)
        #expect(lastArgs()["profile"] == "P2")
        #expect(lastArgs()["recipe"]?["rhythm_weights"] == [2, 1, 3])
        #expect(try await client.randomRecipe(seed: 42) == .example)
        #expect(lastArgs() == ["seed": 42, "admissible_only": true])
        _ = try await client.features(.example)
        #expect(lastArgs()["recipe"]?["total_ms"] == 600)
        _ = try await client.distance(.example, .example)
        #expect(lastArgs()["a"] != nil && lastArgs()["b"] != nil)
        _ = try await client.validate(.text("{\"total_ms\":450"), profile: .p1, committed: [atom], threshold: "0.10")
        #expect(lastArgs()["candidate"] == "{\"total_ms\":450")
        #expect(lastArgs()["committed"]?[0]?["ref_id"] == "K-a1")
        #expect(lastArgs()["threshold"] == "0.10")
        #expect(lastArgs()["use_reserved"] == true)
        #expect(try await client.nearest(.example, committed: [], profile: .p1) == nil)
        _ = try await client.grammar()
        _ = try await client.compose(action: atom, referent: atom, profile: .p1, bookID: "DEMO-P1")
        #expect(lastArgs()["book_id"] == "DEMO-P1")
        #expect(lastArgs()["action"]?["recipe"] != nil)
        _ = try await client.compositeHash(action: atom, referent: atom, profile: .p1)
        #expect(lastArgs()["book_id"] == nil)
        _ = try await client.syntheticBook(profile: .p3)
        #expect(lastArgs() == ["profile": "P3"])
        #expect(try await client.nonlexicalList().count == 2)
        _ = try await client.nonlexicalGet(id: "ready-cue")
        #expect(lastArgs() == ["id": "ready-cue"])
        _ = try await client.vectorsCheck()
        _ = try await client.goldenCheck()
        _ = try await client.storeReset()
        _ = try await client.storeCreate(bookID: "DEMO-1", profile: .p1, threshold: "0.1")
        #expect(lastArgs() == ["book_id": "DEMO-1", "profile": "P1", "threshold": "0.1"])
        _ = try await client.storeCommit(bookID: "DEMO-1", atomID: "K-a1", semanticLabel: nil, recipe: .example)
        #expect(lastArgs()["semantic_label"] == .null)
        _ = try await client.storeList(bookID: "DEMO-1")
        #expect(try await client.storeRecords(bookID: "DEMO-1").count == 1)
        _ = try await client.storeFreeze(bookID: "DEMO-1")
        _ = try await client.storeVerify(bookID: "DEMO-1", expectedHead: Fixtures.hash64)
        #expect(lastArgs()["expected_head"]?.stringValue == Fixtures.hash64)
        _ = try await client.storeTamper(bookID: "DEMO-1", kind: .flipBlobByte)
        #expect(lastArgs()["kind"] == "flip_blob_byte")
        _ = try await client.fallbackDemo(profile: .p1)
        _ = try await client.fallbackScan(profile: .p1, book: [atom], used: [3])
        #expect(lastArgs()["used"] == [3])
        _ = try await client.packageDemo()
        let sent = Set(transport.sentRequests.map(\.cmd))
        #expect(sent.isSuperset(of: Set(results.keys)))
    }
}
