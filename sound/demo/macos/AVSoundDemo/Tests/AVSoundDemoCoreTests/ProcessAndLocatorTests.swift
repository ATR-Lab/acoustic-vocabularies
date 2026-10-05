import Foundation
import Testing

@testable import AVSoundDemoCore

/// A fresh directory under the system temp directory (never inside the repository).
func makeTempDirectory() throws -> URL {
    let url = FileManager.default.temporaryDirectory
        .appendingPathComponent("AVSoundDemoCoreTests-\(UUID().uuidString)", isDirectory: true)
    try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url.resolvingSymlinksInPath()
}

func touch(_ url: URL, executable: Bool = false) throws {
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    try Data("#!/bin/sh\n".utf8).write(to: url)
    if executable {
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: url.path)
    }
}

/// Collects transport events until `.exited` (or the timeout).
func collect(_ stream: AsyncStream<BridgeTransportEvent>, timeout: Duration = .seconds(10)) async -> [BridgeTransportEvent] {
    await withTaskGroup(of: [BridgeTransportEvent]?.self) { group in
        group.addTask {
            var events: [BridgeTransportEvent] = []
            for await event in stream { events.append(event) }
            return events
        }
        group.addTask {
            try? await Task.sleep(for: timeout)
            return nil
        }
        let first = await group.next() ?? nil
        group.cancelAll()
        return first ?? []
    }
}

@Suite("LineSplitter")
struct LineSplitterTests {
    @Test func splitsOnlyAtNewlines() {
        var splitter = LineSplitter()
        #expect(splitter.append(Data("{\"a\":".utf8)).isEmpty)
        #expect(splitter.append(Data("1}\n{\"b\"".utf8)) == [Data("{\"a\":1}".utf8)])
        #expect(splitter.append(Data(":\"x\u{2028}y\"}\r\n\n".utf8)) == [Data("{\"b\":\"x\u{2028}y\"}".utf8), Data()])
        #expect(splitter.append(Data("tail".utf8)).isEmpty)
        #expect(splitter.finish() == Data("tail".utf8))
        #expect(splitter.finish() == nil)
    }

    @Test func handlesLargeLinesInSmallChunks() {
        let line = String(repeating: "x", count: 300_000)
        var splitter = LineSplitter()
        var lines: [Data] = []
        let bytes = Data((line + "\n" + line + "\n").utf8)
        var offset = 0
        while offset < bytes.count {
            let end = min(offset + 65_536, bytes.count)
            lines += splitter.append(bytes.subdata(in: offset..<end))
            offset = end
        }
        #expect(lines.count == 2)
        #expect(lines.allSatisfy { $0.count == 300_000 })
    }
}

@Suite("ProcessBridgeTransport")
struct ProcessBridgeTransportTests {
    func configuration(_ executable: String, _ arguments: [String] = []) -> ProcessBridgeTransport.Configuration {
        .init(
            executableURL: URL(fileURLWithPath: executable), arguments: arguments,
            currentDirectoryURL: FileManager.default.temporaryDirectory,
            environment: ProcessInfo.processInfo.environment)
    }

    @Test func echoesLinesThroughCat() async throws {
        let transport = ProcessBridgeTransport(configuration: configuration("/bin/cat"))
        let stream = try transport.start()
        try transport.send(Data(#"{"id":1,"cmd":"hello","args":{}}"#.utf8))
        try transport.send(Data("second\u{2028}line".utf8))
        transport.closeInput()
        let events = await collect(stream)
        #expect(
            events == [
                .stdoutLine(Data(#"{"id":1,"cmd":"hello","args":{}}"#.utf8)),
                .stdoutLine(Data("second\u{2028}line".utf8)), .exited(status: 0),
            ])
        #expect(throws: BridgeError.notRunning) { try transport.send(Data("late".utf8)) }
    }

    @Test func reportsStderrUnterminatedOutputAndExitStatus() async throws {
        let transport = ProcessBridgeTransport(
            configuration: configuration("/bin/sh", ["-c", "echo oops >&2; printf partial; exit 3"]))
        let events = await collect(try transport.start())
        #expect(events.contains(.stderrLine("oops")))
        #expect(events.contains(.stdoutLine(Data("partial".utf8))))
        #expect(events.last == .exited(status: 3))
    }

    @Test func terminateEndsAProcessThatIgnoresStdin() async throws {
        let transport = ProcessBridgeTransport(configuration: configuration("/bin/sleep", ["30"]))
        let stream = try transport.start()
        let clock = ContinuousClock()
        let started = clock.now
        await transport.terminate(gracePeriod: .milliseconds(300))
        let events = await collect(stream)
        #expect(events.last == .exited(status: 128 + SIGTERM))
        #expect(clock.now - started < .seconds(10))
    }

    /// SIGTERM goes to the launched process (uv) only, never to its whole process group:
    /// uv forwards it to the bridge, and a second copy could interrupt the bridge's
    /// cleanup. Here a shell stands in for uv; its child traps SIGTERM and reports it.
    @Test func terminateSignalsTheLauncherButNotItsChildren() async throws {
        let script = """
            /bin/sh -c 'trap "echo child-got-TERM >&2; exit 0" TERM; echo child-ready >&2; \
            i=0; while [ $i -lt 200 ]; do sleep 0.05; i=$((i+1)); done' &
            child=$!
            trap 'echo launcher-got-TERM >&2; sleep 0.3; kill -KILL $child 2>/dev/null; exit 143' TERM
            wait
            """
        let transport = ProcessBridgeTransport(configuration: configuration("/bin/sh", ["-c", script]))
        let stream = try transport.start()
        let events = Task { await collect(stream, timeout: .seconds(20)) }
        try await Task.sleep(for: .milliseconds(300))  // the child has set its trap
        await transport.terminate(gracePeriod: .seconds(1))
        let received = await events.value
        #expect(received.contains(.stderrLine("child-ready")))
        #expect(received.contains(.stderrLine("launcher-got-TERM")))
        #expect(!received.contains(.stderrLine("child-got-TERM")))
        #expect(received.last == .exited(status: 143))
    }

    @Test func launchFailureIsReported() throws {
        let transport = ProcessBridgeTransport(configuration: configuration("/nonexistent/uv"))
        #expect(throws: BridgeError.self) { _ = try transport.start() }
    }

    /// The whole client over real pipes, with a shell script standing in for the bridge.
    @Test func clientOverRealPipes() async throws {
        let hello = try JSONValue(jsonString: Fixtures.hello).compactString
        let script = """
            read -r line; printf '%s\\n' '{"id":1,"ok":true,"result":\(hello)}'
            echo 'bridge: hello answered' >&2
            read -r line; printf '%s\\n' '{"id":2,"ok":true,"result":{"ok":true,"message":"fine"}}'
            read -r line; printf '%s\\n' '{"id":3,"ok":true,"result":{}}'
            exit 0
            """
        let config = configuration("/bin/sh", ["-c", script])
        let client = BridgeClient(configuration: config)
        let started = try await client.start(timeout: .seconds(10))
        #expect(started.rendererVersion == "0.1.0")
        let selfTest = try await client.selfTest(timeout: .seconds(10))
        #expect(selfTest.message == "fine")
        await client.stop()
        #expect(await client.status == .stopped)
        #expect(await client.logEntries().contains { $0.source == .stderr && $0.text == "bridge: hello answered" })
    }

    @Test func bridgeConfiguration() {
        let repo = URL(fileURLWithPath: "/repo/checkout")
        let config = ProcessBridgeTransport.Configuration.bridge(
            uv: URL(fileURLWithPath: "/opt/homebrew/bin/uv"), repoRoot: repo,
            baseEnvironment: ["HOME": "/home/example", "PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"])
        #expect(config.executableURL.path == "/opt/homebrew/bin/uv")
        #expect(
            config.arguments == [
                "run", "--frozen", "--project", "/repo/checkout/sound", "python",
                "/repo/checkout/sound/demo/macos/bridge/av_sound_bridge.py",
            ])
        #expect(config.currentDirectoryURL?.path == "/repo/checkout")
        #expect(config.environment["LANG"] == "en_US.UTF-8")
        #expect(config.environment["PYTHONUNBUFFERED"] == "1")
        // PROTOCOL.md safety rule: no __pycache__ folders in the work tree.
        #expect(config.environment["PYTHONDONTWRITEBYTECODE"] == "1")
        let path = config.environment["PATH"]?.split(separator: ":").map(String.init) ?? []
        #expect(Array(path.prefix(2)) == ["/usr/bin", "/bin"])
        #expect(path.contains("/opt/homebrew/bin"))
        #expect(path.contains("/usr/local/bin"))
        #expect(path.contains("/home/example/.local/bin"))
        #expect(path.contains("/home/example/.cargo/bin"))
        // PROTOCOL.md safety rule: uv never re-locks (rewrites the tracked sound/uv.lock).
        #expect(config.arguments.contains("--frozen"))
        #expect(!config.arguments.contains("--locked") && !config.arguments.contains("--upgrade"))
        #expect(config.commandLine.hasPrefix("/opt/homebrew/bin/uv run --frozen --project"))
    }
}

@Suite("Locators")
struct LocatorTests {
    @Test func uvSearchOrder() {
        let candidates = UVLocator.candidates(
            explicit: "~/tools/uv", environment: ["HOME": "/home/example", "PATH": "/a:/b:/opt/homebrew/bin"])
        #expect(
            candidates.map(\.path) == [
                "/home/example/tools/uv", "/a/uv", "/b/uv", "/opt/homebrew/bin/uv", "/usr/local/bin/uv",
                "/home/example/.local/bin/uv", "/home/example/.cargo/bin/uv",
            ])
    }

    @Test func uvLocateUsesTheFirstExecutable() throws {
        let dir = try makeTempDirectory()
        defer { try? FileManager.default.removeItem(at: dir) }
        let notExecutable = dir.appendingPathComponent("a/uv")
        let executable = dir.appendingPathComponent("b/uv")
        try touch(notExecutable)
        try touch(executable, executable: true)
        let env = ["HOME": dir.path, "PATH": "\(dir.path)/a:\(dir.path)/b"]
        #expect(UVLocator.locate(environment: env)?.path == executable.path)
        #expect(UVLocator.locate(explicit: executable.path, environment: ["HOME": dir.path, "PATH": ""])?.path == executable.path)
        #expect(UVLocator.locate(environment: env, isExecutable: { _ in false }) == nil)
        let fallback = UVLocator.locate(environment: ["HOME": "/home/example", "PATH": ""]) { $0 == "/home/example/.cargo/bin/uv" }
        #expect(fallback?.path == "/home/example/.cargo/bin/uv")
    }

    @Test func repoLocation() throws {
        let dir = try makeTempDirectory()
        defer { try? FileManager.default.removeItem(at: dir) }
        let repo = dir.appendingPathComponent("checkout")
        try touch(repo.appendingPathComponent("sound/pyproject.toml"))
        let deep = repo.appendingPathComponent("sound/demo/macos/AVSoundDemo/.build/debug")
        try FileManager.default.createDirectory(at: deep, withIntermediateDirectories: true)
        // Not valid until the bridge script exists.
        #expect(!RepoLocator.isValidRepo(repo))
        #expect(RepoLocator.walkUp(from: deep) == nil)
        try touch(repo.appendingPathComponent("sound/demo/macos/bridge/av_sound_bridge.py"))
        #expect(RepoLocator.isValidRepo(repo))
        #expect(RepoLocator.walkUp(from: deep)?.path == repo.path)

        // One fixed suite: macOS keeps an (empty) plist in ~/Library/Preferences for every
        // suite name even after removePersistentDomain, so a new name per run would pile up.
        let suiteName = "AVSoundDemoCoreTests.locator"
        let defaults = try #require(UserDefaults(suiteName: suiteName))
        defaults.removePersistentDomain(forName: suiteName)
        defer { defaults.removePersistentDomain(forName: suiteName) }
        func locate(explicit: URL? = nil, env: [String: String] = [:], exe: URL? = nil, cwd: URL? = nil) -> RepoLocator.Located? {
            RepoLocator.locate(
                explicit: explicit, environment: env, executableURL: exe, bundleURL: nil, currentDirectory: cwd,
                defaults: defaults)
        }
        #expect(locate()?.url == nil)
        #expect(locate(explicit: repo)?.source == .explicit)
        #expect(locate(explicit: dir, env: [RepoLocator.environmentKey: repo.path])?.source == .environment)
        #expect(locate(env: [RepoLocator.environmentKey: "  "], cwd: deep)?.source == .currentDirectory)  // blank: unset
        #expect(locate(exe: deep.appendingPathComponent("AVSoundDemo"))?.source == .executable)
        #expect(locate(exe: deep.appendingPathComponent("AVSoundDemo"))?.url.path == repo.path)
        #expect(locate(cwd: deep)?.source == .currentDirectory)
        RepoLocator.persist(repo, defaults: defaults)
        #expect(locate()?.source == .persisted)
        #expect(locate()?.url.path == repo.path)
        RepoLocator.clearPersisted(defaults: defaults)
        #expect(locate() == nil)
    }

    /// README: "`AV_SOUND_REPO=<path>` overrides the repository". A value that is not the
    /// repository is an error that names it; the search never falls back to another
    /// checkout (the executable's, the current directory's or the saved one), which would
    /// silently check and play that checkout's engine.
    @Test func anInvalidEnvironmentRepoIsAnErrorNotAFallThrough() throws {
        let dir = try makeTempDirectory()
        defer { try? FileManager.default.removeItem(at: dir) }
        let repo = dir.appendingPathComponent("checkout")
        try touch(repo.appendingPathComponent("sound/pyproject.toml"))
        try touch(repo.appendingPathComponent("sound/demo/macos/bridge/av_sound_bridge.py"))
        let deep = repo.appendingPathComponent("sound/demo/macos/AVSoundDemo/.build/debug")
        try FileManager.default.createDirectory(at: deep, withIntermediateDirectories: true)
        let wrong = dir.appendingPathComponent("not-a-checkout").path

        let suiteName = "AVSoundDemoCoreTests.locator.environment"
        let defaults = try #require(UserDefaults(suiteName: suiteName))
        defaults.removePersistentDomain(forName: suiteName)
        defer { defaults.removePersistentDomain(forName: suiteName) }
        RepoLocator.persist(repo, defaults: defaults)
        let env = [RepoLocator.environmentKey: wrong]
        func resolve(explicit: URL? = nil) throws(BridgeSetupError) -> RepoLocator.Located {
            try RepoLocator.resolve(
                explicit: explicit, environment: env, executableURL: deep.appendingPathComponent("AVSoundDemo"),
                bundleURL: nil, currentDirectory: deep, defaults: defaults)
        }
        // Every later candidate is a valid repository, and none is used.
        #expect(throws: BridgeSetupError.environmentRepoInvalid(path: wrong)) { try resolve() }
        #expect(RepoLocator.locate(
            environment: env, executableURL: deep.appendingPathComponent("AVSoundDemo"), bundleURL: nil,
            currentDirectory: deep, defaults: defaults) == nil)
        let message = BridgeSetupError.environmentRepoInvalid(path: wrong).message
        #expect(message.contains(RepoLocator.environmentKey) && message.contains(wrong))
        // A valid explicit path (--repo, or the setup sheet's choice) still comes first.
        #expect(try resolve(explicit: repo).source == .explicit)
        // Without the variable, the search goes on as before.
        #expect(try RepoLocator.resolve(
            environment: [:], executableURL: nil, bundleURL: nil, currentDirectory: deep, defaults: defaults
        ).source == .currentDirectory)
        #expect(throws: BridgeSetupError.repoNotFound) {
            try RepoLocator.resolve(
                environment: [:], executableURL: nil, bundleURL: nil, currentDirectory: dir, defaults: nil)
        }
    }

    /// `Configuration.locate` (the app's launch and the self-check) reports the invalid
    /// variable instead of starting another checkout's engine.
    @Test func configurationLocateRefusesAnInvalidEnvironmentRepo() throws {
        let dir = try makeTempDirectory()
        defer { try? FileManager.default.removeItem(at: dir) }
        let uv = dir.appendingPathComponent("uv")
        try touch(uv, executable: true)
        let wrong = dir.appendingPathComponent("not-a-checkout").path
        #expect(throws: BridgeSetupError.environmentRepoInvalid(path: wrong)) {
            try ProcessBridgeTransport.Configuration.locate(
                explicitUV: uv.path, environment: ["HOME": dir.path, "PATH": "", RepoLocator.environmentKey: wrong])
        }
    }

    @Test func configurationLocateReportsWhatIsMissing() throws {
        do {
            _ = try ProcessBridgeTransport.Configuration.locate(
                explicitUV: "/nonexistent/uv", environment: ["HOME": "/nonexistent-home", "PATH": ""])
        } catch let error as BridgeSetupError {
            switch error {
            case .uvNotFound(let searched): #expect(searched.first == "/nonexistent/uv")
            case .repoNotFound, .environmentRepoInvalid: break  // uv exists in a standard location on this machine
            }
        }
    }
}
