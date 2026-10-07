import AppKit
import Foundation
import SwiftUI
import Testing

@testable import AVSoundDemo
@testable import AVSoundDemoCore

/// The repository root of this checkout (Tests/AVSoundDemoAppTests is seven levels down).
enum AppRepo {
    static let root: URL = (0..<7).reduce(URL(fileURLWithPath: #filePath)) { url, _ in
        url.deletingLastPathComponent()
    }

    /// The bridge launch for the live suites (`AV_SOUND_BRIDGE_TESTS=1`, uv installed).
    static var liveConfiguration: ProcessBridgeTransport.Configuration? {
        guard ProcessInfo.processInfo.environment["AV_SOUND_BRIDGE_TESTS"] == "1",
            RepoLocator.isValidRepo(root),
            let uv = UVLocator.locate()
        else { return nil }
        return .bridge(uv: uv, repoRoot: root)
    }
}

struct WaitTimeout: Error, CustomStringConvertible {
    let what: String
    var description: String { "timed out waiting for \(what)" }
}

/// Polls `condition` on the main actor (letting the app's tasks run) until it holds.
@MainActor
func waitUntil(
    _ what: String, timeout: Duration = .seconds(180), _ condition: () -> Bool
) async throws {
    let clock = ContinuousClock()
    let deadline = clock.now.advanced(by: timeout)
    while !condition() {
        guard clock.now < deadline else { throw WaitTimeout(what: what) }
        try await Task.sleep(for: .milliseconds(10))
    }
}

@Suite("Self-check exit status")
struct SelfCheckExitStatusTests {
    @Test func statusTable() {
        #expect(SelfCheck.exitStatus(bridgeStarted: true, allOK: true) == 0)
        #expect(SelfCheck.exitStatus(bridgeStarted: true, allOK: false) == 1)
        #expect(SelfCheck.exitStatus(bridgeStarted: false, allOK: false) == 2)
        #expect(SelfCheck.exitStatus(bridgeStarted: false, allOK: true) == 2)
    }

    /// README: "2 when the bridge cannot start". A launcher that exits at once (here
    /// /usr/bin/false in place of uv) is a bridge that cannot start.
    @Test(.enabled(if: RepoLocator.isValidRepo(AppRepo.root)))
    func aBridgeThatCannotStartExitsTwo() async {
        let options = SelfCheck.Options(arguments: ["--repo", AppRepo.root.path, "--uv", "/usr/bin/false"])
        #expect(await SelfCheck.run(options) == 2)
    }

    @Test func aMissingRepositoryExitsTwo() async {
        let options = SelfCheck.Options(arguments: ["--repo", "/nonexistent-av-sound-repo", "--uv", "/usr/bin/false"])
        #expect(await SelfCheck.run(options) == 2)
    }

    /// Options that cannot be used fail `locate` (exit 2) instead of being ignored: the
    /// search would otherwise check whatever checkout it finds, and exit 0.
    @Test func unusableOptionsFailLocate() async throws {
        let bad: [[String]] = [
            ["--count", "1", "--repo"], ["--repo="], ["--repo", ""], ["--repo", "  "], ["--uv"], ["--uv="],
            ["--count", "abc"], ["--count=abc"], ["--count", "0"], ["--count"], ["--repo", "--count", "1"],
            ["--cout", "3"], ["--repo-path", "/x"],
        ]
        for arguments in bad {
            let options = SelfCheck.Options(arguments: [SelfCheck.flag] + arguments)
            #expect(!options.problems.isEmpty, "\(arguments)")
            #expect(throws: AppError.self, "\(arguments)") {
                try SelfCheck.locate(options, environment: ["HOME": "/nonexistent-home", "PATH": ""])
            }
        }
        #expect(await SelfCheck.run(SelfCheck.Options(arguments: [SelfCheck.flag, "--count", "1", "--repo"])) == 2)

        let good = SelfCheck.Options(arguments: [
            SelfCheck.flag, "--repo=/some/checkout", "--uv", "/some/uv", "--count", "3", "-AVSoundDemo.section", "messages",
        ])
        #expect(good.problems.isEmpty)
        #expect(good.repo == "/some/checkout" && good.uv == "/some/uv" && good.conformanceCount == 3)
        let defaults = SelfCheck.Options(arguments: [SelfCheck.flag])
        #expect(defaults.problems.isEmpty && defaults.repo == nil && defaults.conformanceCount == 10)
    }

    /// An `AV_SOUND_REPO` that is not the repository fails `locate` (exit 2) even when
    /// the search would find this checkout from the test binary or the current directory;
    /// an explicit `--repo` still comes first.
    @Test(.enabled(if: RepoLocator.isValidRepo(AppRepo.root)))
    func anInvalidEnvironmentRepoFailsLocate() throws {
        let wrong = "/nonexistent-av-sound-checkout"
        var environment = ProcessInfo.processInfo.environment
        environment[RepoLocator.environmentKey] = wrong
        let options = SelfCheck.Options(arguments: ["--uv", "/usr/bin/true"])
        #expect(throws: BridgeSetupError.environmentRepoInvalid(path: wrong)) {
            try SelfCheck.locate(options, environment: environment)
        }
        let explicit = SelfCheck.Options(arguments: ["--repo", AppRepo.root.path, "--uv", "/usr/bin/true"])
        let configuration = try SelfCheck.locate(explicit, environment: environment)
        #expect(configuration.currentDirectoryURL?.standardizedFileURL.path == AppRepo.root.standardizedFileURL.path)
    }

    /// swift_conformance fails when the Swift tables differ from spec D4, even when every
    /// sampled recipe matched (the recipes reach only a few table entries).
    @Test func conformanceNeedsTheTableDigests() {
        #expect(SelfCheck.conformancePassed(checked: 10, expected: 10, mismatches: 0, tableDigestsMatch: true))
        #expect(!SelfCheck.conformancePassed(checked: 10, expected: 10, mismatches: 0, tableDigestsMatch: false))
        #expect(!SelfCheck.conformancePassed(checked: 10, expected: 10, mismatches: 1, tableDigestsMatch: true))
        #expect(!SelfCheck.conformancePassed(checked: 9, expected: 10, mismatches: 0, tableDigestsMatch: true))
    }
}

@Suite("Launch search")
struct LaunchSearchTests {
    /// The app's launch and the self-check use one search (`AppModel.launchConfiguration`):
    /// a saved repository comes after the checkout the app is in (README: "an app built
    /// inside a checkout always runs that checkout's engine") and is used when the app is
    /// in none. `AV_SOUND_REPO` comes before both, and `--repo` before that; the saved uv
    /// comes before the uv search.
    @Test func aSavedRepositoryComesAfterTheAppsOwnCheckout() throws {
        let fileManager = FileManager.default
        let dir = fileManager.temporaryDirectory.appendingPathComponent("av-sound-launch-\(UUID().uuidString)")
        defer { try? fileManager.removeItem(at: dir) }
        func create(_ url: URL, executable: Bool = false) throws {
            try fileManager.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            #expect(fileManager.createFile(
                atPath: url.path, contents: Data(), attributes: executable ? [.posixPermissions: 0o755] : nil))
        }
        func checkout(_ name: String) throws -> URL {
            let root = dir.appendingPathComponent(name)
            for file in RepoLocator.requiredFiles { try create(root.appendingPathComponent(file)) }
            return root.standardizedFileURL
        }
        let saved = try checkout("A")
        let own = try checkout("B")
        let uv = dir.appendingPathComponent("tools/uv")
        try create(uv, executable: true)

        // One fixed suite name (macOS keeps a plist per suite name).
        let suiteName = "AVSoundDemoAppTests.launch"
        let defaults = try #require(UserDefaults(suiteName: suiteName))
        defaults.removePersistentDomain(forName: suiteName)
        defer { defaults.removePersistentDomain(forName: suiteName) }
        RepoLocator.persist(saved, defaults: defaults)
        defaults.set(uv.path, forKey: AppModel.uvDefaultsKey)

        let environment = ["HOME": dir.path, "PATH": ""]
        let inOwn = own.appendingPathComponent(
            "sound/demo/macos/AVSoundDemo/build/AV Sound Demo.app/Contents/MacOS/AVSoundDemo")
        let elsewhere = dir.appendingPathComponent("Applications/AV Sound Demo.app/Contents/MacOS/AVSoundDemo")
        func repo(_ repo: URL? = nil, env: [String: String] = environment, executable: URL) throws -> String? {
            let configuration = try AppModel.launchConfiguration(
                repo: repo, environment: env, executableURL: executable, bundleURL: nil,
                currentDirectory: dir, defaults: defaults)
            #expect(configuration.executableURL.path == uv.path)  // the saved uv
            return configuration.currentDirectoryURL?.path
        }
        #expect(try repo(executable: inOwn) == own.path)
        #expect(try repo(executable: elsewhere) == saved.path)
        var withVariable = environment
        withVariable[RepoLocator.environmentKey] = saved.path
        #expect(try repo(env: withVariable, executable: inOwn) == saved.path)
        #expect(try repo(own, env: withVariable, executable: elsewhere) == own.path)
    }

    /// The setup sheet names every launch problem. `launchConfiguration` stops at the
    /// first one (uv), so with uv missing too, an `AV_SOUND_REPO` that is not the
    /// repository must still be named, and its path (not the saved repository) fills the
    /// field (README: "the setup sheet names the variable and the path").
    @Test func theSetupSheetNamesAnInvalidEnvironmentRepoAlsoWithoutUV() throws {
        let fileManager = FileManager.default
        let dir = fileManager.temporaryDirectory.appendingPathComponent("av-sound-setup-\(UUID().uuidString)")
        defer { try? fileManager.removeItem(at: dir) }
        let saved = dir.appendingPathComponent("A").standardizedFileURL
        for file in RepoLocator.requiredFiles {
            let url = saved.appendingPathComponent(file)
            try fileManager.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            #expect(fileManager.createFile(atPath: url.path, contents: Data()))
        }
        let wrong = dir.appendingPathComponent("not-a-checkout").standardizedFileURL
        try fileManager.createDirectory(at: wrong, withIntermediateDirectories: true)
        let uv = dir.appendingPathComponent("tools/uv").path

        let suiteName = "AVSoundDemoAppTests.setup"
        let defaults = try #require(UserDefaults(suiteName: suiteName))
        defaults.removePersistentDomain(forName: suiteName)
        defer { defaults.removePersistentDomain(forName: suiteName) }
        RepoLocator.persist(saved, defaults: defaults)
        defaults.set(uv, forKey: AppModel.uvDefaultsKey)

        let environment = ["HOME": dir.path, "PATH": ""]
        var withVariable = environment
        withVariable[RepoLocator.environmentKey] = wrong.path
        func draft(_ error: BridgeSetupError, env: [String: String], uvFound: Bool) -> AppModel.SetupDraft {
            AppModel.setupDraft(
                after: error, environment: env, executableURL: nil, bundleURL: nil, currentDirectory: nil,
                defaults: defaults, isExecutable: { uvFound && $0 == uv })
        }

        // No uv and an invalid AV_SOUND_REPO: both are named, and the field shows the
        // variable's path (invalid), not the saved repository.
        let both = draft(.uvNotFound(searched: []), env: withVariable, uvFound: false)
        #expect(both.message.contains("uv was not found"))
        #expect(both.message.contains("\(RepoLocator.environmentKey) is set to \(wrong.path)"))
        #expect(both.repoPath == wrong.path)
        #expect(both.uvPath == uv)  // the saved path, shown as not executable

        // No uv, no variable: only uv is named, and the saved repository fills the field.
        let uvOnly = draft(.uvNotFound(searched: []), env: environment, uvFound: false)
        #expect(uvOnly.message.contains("uv was not found"))
        #expect(!uvOnly.message.contains(RepoLocator.environmentKey))
        #expect(uvOnly.repoPath == saved.path)

        // uv found, invalid variable: only the variable is named.
        let variableOnly = draft(.environmentRepoInvalid(path: wrong.path), env: withVariable, uvFound: true)
        #expect(!variableOnly.message.contains("uv was not found"))
        #expect(variableOnly.message.contains(wrong.path))
        #expect(variableOnly.repoPath == wrong.path && variableOnly.uvPath == uv)
    }
}

@Suite("AppModel playback state")
@MainActor
struct AppModelPlaybackTests {
    /// A sound cut off by an output device change runs no completion; the playhead
    /// (`nowPlaying`) must still be cleared. Plays at volume 0 through the audio device,
    /// so it is opt-in like the other playback tests (`AV_SOUND_AUDIO_TESTS=1`).
    @Test(.enabled(if: ProcessInfo.processInfo.environment["AV_SOUND_AUDIO_TESTS"] == "1"))
    func anInterruptionClearsNowPlaying() async throws {
        let app = AppModel()
        app.player.volume = 0
        let samples = (0..<96_000).map { Int16(($0 * 7) % 2_000 - 1_000) }
        let wav = CanonicalWAV.fileData(samples: samples)
        let clip = try await Offload.clip(AudioPayload(
            wavB64: wav.base64EncodedString(), fileSHA256: Hashing.sha256Hex(wav),
            pcmSHA256: Hashing.sha256Hex(CanonicalWAV.pcmData(samples: samples))))
        app.play(clip)
        #expect(app.nowPlaying?.clipID == clip.id)
        #expect(app.isPlaying(clip))
        app.player.configurationChanged()  // what AVAudioEngineConfigurationChange runs
        #expect(app.nowPlaying == nil)
        #expect(!app.isPlaying(clip))

        // The real order of a device change: the engine stops itself, and the buffer's
        // completion callback comes before the notification. The sound is reported as
        // cut off, not as played to the end.
        app.play(clip)
        #expect(app.isPlaying(clip))
        app.player.stopEngineForTesting()
        try await waitUntil("the interruption", timeout: .seconds(5)) { app.nowPlaying == nil }
        #expect(!app.isPlaying(clip))
        #expect(app.player.lastError == AudioPlayer.interruptionMessage)
    }

    /// The sidebar's list has a required selection: AppKit's outline view behind it does
    /// not allow an empty selection (a Command-click on the selected row used to set
    /// `selection` to nil while the window still showed the Recipe Lab), and deselecting
    /// leaves the section as it was.
    @Test func theSidebarSelectionCannotBeEmptied() throws {
        _ = NSApplication.shared
        let app = AppModel()
        app.selection = .messages
        let host = NSHostingView(rootView: Sidebar().environment(app))
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 260, height: 640), styleMask: [.titled],
            backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        defer { window.close() }
        window.contentView = host
        for _ in 0..<5 {
            host.layoutSubtreeIfNeeded()
            RunLoop.main.run(until: Date().addingTimeInterval(0.02))
        }
        let table = try #require(tableViews(in: host).first, "the sidebar list has no AppKit table view")
        #expect(!table.allowsEmptySelection)
        #expect(table.selectedRow >= 0)
        table.deselectAll(nil)
        RunLoop.main.run(until: Date().addingTimeInterval(0.05))
        #expect(table.selectedRow >= 0)
        #expect(app.selection == .messages && app.isMessagesVisible)
    }

    @Test func theInterruptionHandlerIsInstalled() {
        #expect(AppModel().player.onInterruption != nil)
    }

    /// A pending play (a render or fetch on its way) starts only while its request is the
    /// latest: Stop Playback, another section and a later request each cancel it. Nothing
    /// is played here (a cancelled request never reaches the player).
    @Test func aPendingPlayIsCancelledByAStopASectionChangeOrALaterRequest() async throws {
        let app = AppModel()
        app.selection = .fallback
        let samples = (0..<4_800).map { Int16(($0 * 7) % 2_000 - 1_000) }
        let wav = CanonicalWAV.fileData(samples: samples)
        let clip = try await Offload.clip(AudioPayload(
            wavB64: wav.base64EncodedString(), fileSHA256: Hashing.sha256Hex(wav),
            pcmSHA256: Hashing.sha256Hex(CanonicalWAV.pcmData(samples: samples))))

        let stopped = app.requestPlay()
        #expect(app.isLatest(stopped))
        app.stopPlayback()  // Cmd-. while the render is on its way
        #expect(!app.isLatest(stopped))
        #expect(!app.play(clip, for: stopped))

        let left = app.requestPlay()
        app.selection = .messages
        #expect(!app.play(clip, for: left))
        let same = app.requestPlay()
        app.selection = .messages  // not a change
        #expect(app.isLatest(same))

        let first = app.requestPlay()
        let second = app.requestPlay()  // the last click wins
        #expect(!app.play(clip, for: first))
        #expect(app.isLatest(second))
        #expect(app.nowPlaying == nil && app.player.current == nil && app.player.lastError == nil)
    }

    /// A profile change cancels the pending plays of a section that uses the profile (its
    /// sound was asked for under the old one), not those of a section that does not.
    @Test func aProfileChangeCancelsPendingPlaysOfSectionsThatUseIt() {
        let app = AppModel()
        for section in AppModel.Section.allCases {
            app.selection = section
            let request = app.requestPlay()
            app.profile = app.profile == .p1 ? .p2 : .p1
            #expect(app.isLatest(request) == !section.usesProfile, "\(section)")
        }
        app.selection = .fallback
        let request = app.requestPlay()
        app.profile = app.profile  // not a change
        #expect(app.isLatest(request))
    }

    /// The visible-section flags follow the section the window shows. `selection` is not
    /// optional, so there is no state in which the window shows the lab (the old
    /// `selection ?? .recipeLab`) while the model treats it as hidden.
    @Test func theLabIsVisibleOnlyWhenSelected() {
        let app = AppModel()
        for section in AppModel.Section.allCases {
            app.selection = section
            #expect(app.isLabVisible == (section == .recipeLab), "\(section)")
            #expect(app.isMessagesVisible == (section == .messages), "\(section)")
        }
    }
}

@Suite("Fallback model")
@MainActor
struct FallbackModelTests {
    @Test func noScanBeforeAnyScan() {
        let app = AppModel()
        #expect(app.fallback.currentScan == nil)
        #expect(app.fallback.selectedBankEntry == nil)
    }

    /// Every token of `used` must be an integer: none is dropped silently.
    @Test func usedIndicesAreParsedStrictly() throws {
        #expect(try FallbackModel.parseUsed("") == [])
        #expect(try FallbackModel.parseUsed("  ") == [])
        #expect(try FallbackModel.parseUsed("0, 3") == [0, 3])
        #expect(try FallbackModel.parseUsed("0 3,5\t7") == [0, 3, 5, 7])
        #expect(try FallbackModel.parseUsed("-1") == [-1])  // the bridge refuses the range
        for text in ["1-3", "1.5", "two", "0, 1.5", "0;3", "0, 3x"] {
            #expect(throws: AppError.self, "\(text)") { try FallbackModel.parseUsed(text) }
        }
    }

    /// A `used` field that does not parse shows an error and sends no scan.
    @Test func anUnparsableUsedFieldSendsNoScan() {
        let app = AppModel()
        let fallback = app.fallback
        fallback.usedText = "0;3"
        #expect(fallback.usedError != nil)
        fallback.scanCurrentBook()
        #expect(fallback.activity.error?.contains("0;3") == true)
        #expect(!fallback.activity.isRunning("scan"))
        fallback.usedText = "0, 3"
        #expect(fallback.usedError == nil)
    }
}

@Suite("Messages model")
@MainActor
struct MessagesModelTests {
    /// The atoms a message is made from: they change when an atom of the message is
    /// replaced or removed, or the book's origin changes; not for other atoms.
    @Test func theSourceFollowsTheMessageAtoms() throws {
        let message = try JSONDecoder().decode(MessageRef.self, from: Data("""
            {"message_id":"K-a1-r1","family":"K","action":"K-a1","referent":"K-r1","status":"trained",
             "training_wave":1,"heldout_set":null,"is_heldout":false}
            """.utf8))
        var book = ScratchBook()
        #expect(MessagesModel.source(of: message, in: book) == nil)
        book.set("K-a1", .example)
        #expect(MessagesModel.source(of: message, in: book) == nil)
        book.set("K-r1", .example)
        let first = try #require(MessagesModel.source(of: message, in: book))
        book.set("K-a2", Recipe(totalMs: 450, pitches: [0, 0, 0], rhythmWeights: [1, 1, 1], gapsMs: [40, 40], amplitudes: [1.0, 1.0, 1.0]))
        #expect(MessagesModel.source(of: message, in: book)?.action == first.action)
        #expect(MessagesModel.source(of: message, in: book)?.referent == first.referent)
        // A trained message also depends on the other atoms: they decide whether it equals
        // a held-out message of the book. A held-out message's hash does not.
        #expect(MessagesModel.source(of: message, in: book) != first)
        let heldOut = try JSONDecoder().decode(MessageRef.self, from: Data("""
            {"message_id":"K-a1-r2","family":"K","action":"K-a1","referent":"K-r2","status":"H-V1",
             "training_wave":null,"heldout_set":"H-V1","is_heldout":true}
            """.utf8))
        book.set("K-r2", .example)
        let heldOutFirst = try #require(MessagesModel.source(of: heldOut, in: book))
        #expect(heldOutFirst.bookAtoms == nil)
        book.set("K-a3", .example)
        #expect(MessagesModel.source(of: heldOut, in: book) == heldOutFirst)
        var changed = Recipe.example
        changed.pitches[0] += 1
        book.set("K-r1", changed)
        #expect(MessagesModel.source(of: message, in: book)?.referent != first.referent)
        book.set("K-a1", nil)
        #expect(MessagesModel.source(of: message, in: book) == nil)
    }

    /// Each held-out message is named once, under its first origin: this book, then the
    /// fixed books (DEMO, then fallback), then the earlier book states.
    @Test func heldOutTwinsAreNamedOnceInOriginOrder() throws {
        let a = try heldOutRef("K-a1-r2"), b = try heldOutRef("K-a2-r3"), c = try heldOutRef("K-a3-r4")
        let d = try heldOutRef("K-a4-r1")
        let none: [(origin: MessagesModel.HeldOutTwin.Origin, messages: [MessageRef])] = [
            (.demoBook("DEMO-P1"), []), (.fallbackBook("DEMO-fallback-v1"), []),
        ]
        #expect(MessagesModel.twins(inBook: [], fixed: none, earlier: []).isEmpty)
        let twins = MessagesModel.twins(
            inBook: [a],
            fixed: [(.demoBook("DEMO-P1"), [a, b]), (.fallbackBook("DEMO-fallback-v1"), [b, c])],
            earlier: [c, d])
        #expect(twins == [
            .init(message: a, origin: .scratchBook),
            .init(message: b, origin: .demoBook("DEMO-P1")),
            .init(message: c, origin: .fallbackBook("DEMO-fallback-v1")),
            .init(message: d, origin: .earlier),
        ])
        #expect(twins[1].text == "held-out K-a2-r3 of the DEMO book DEMO-P1")
        #expect(twins[2].text == "held-out K-a3-r4 of the DEMO fallback book (seed DEMO-fallback-v1)")
    }

    /// A fixed book's own message: a trained message with the two recipes it has in that
    /// book, whatever the other atoms are. A held-out message never is one.
    @Test func aFixedBooksOwnMessageHasItsTwoRecipes() throws {
        let trained = try trainedRef("K-a1-r1")
        let heldOut = try heldOutRef("K-a1-r2")
        var other = Recipe.example
        other.pitches[0] += 1
        let fixed: [String: Recipe] = ["K-a1": .example, "K-r1": other, "K-r2": .example]
        #expect(MessagesModel.isOwnMessage(trained, of: fixed, in: fixed))
        var edited = fixed
        edited["K-r2"] = other  // another atom: still the fixed book's own message
        edited["Q-a1"] = .example
        #expect(MessagesModel.isOwnMessage(trained, of: fixed, in: edited))
        edited["K-r1"] = .example  // one of its atoms changed
        #expect(!MessagesModel.isOwnMessage(trained, of: fixed, in: edited))
        #expect(!MessagesModel.isOwnMessage(trained, of: fixed, in: ["K-a1": .example]))
        #expect(!MessagesModel.isOwnMessage(trained, of: ["K-a1": .example], in: fixed))
        #expect(!MessagesModel.isOwnMessage(heldOut, of: fixed, in: fixed))
    }

    /// The refusal card explains each origin and offers only what helps: the duplicate
    /// slots for this book, the recorded state (until the app quits) for an earlier one,
    /// and the DEMO book only when the book is not the DEMO book already.
    @Test func theRefusalCardExplainsEachOrigin() throws {
        let trained = try trainedRef("K-a1-r1")
        let heldOut = try heldOutRef("K-a1-r2")
        let earlier = MessagesModel.refusalExplanation(
            for: trained, twins: [.init(message: heldOut, origin: .earlier)], bookIsDemo: false)
        #expect(earlier.contains("earlier state of a scratch book in this session"))
        #expect(earlier.contains("until the app quits"))
        #expect(!earlier.contains("two slots hold the same recipe"))
        #expect(earlier.contains("load the DEMO book"))
        let inBook = MessagesModel.refusalExplanation(
            for: trained, twins: [.init(message: heldOut, origin: .scratchBook)], bookIsDemo: false)
        #expect(inBook.contains("two slots hold the same recipe"))
        #expect(!inBook.contains("until the app quits"))
        let fixed = MessagesModel.refusalExplanation(
            for: trained, twins: [.init(message: heldOut, origin: .demoBook("DEMO-P2"))], bookIsDemo: false)
        #expect(fixed.contains("held-out K-a1-r2 of the DEMO book DEMO-P2"))
        #expect(!fixed.contains("two slots hold the same recipe"))
        let inDemo = MessagesModel.refusalExplanation(
            for: trained, twins: [.init(message: heldOut, origin: .scratchBook)], bookIsDemo: true)
        #expect(!inDemo.contains("load the DEMO book"))
        #expect(inDemo.contains("a recipe of its own"))
    }

    /// The held-out messages of a book state are those with both atoms in it, with the
    /// recipes they have there; trained messages are never among them.
    @Test func heldOutPairsHoldTheAtomsOfTheBook() throws {
        let heldOut = try heldOutRef("K-a1-r2")
        let trained = try JSONDecoder().decode(MessageRef.self, from: Data("""
            {"message_id":"K-a1-r1","family":"K","action":"K-a1","referent":"K-r1","status":"trained",
             "training_wave":1,"heldout_set":null,"is_heldout":false}
            """.utf8))
        var other = Recipe.example
        other.pitches[0] += 1
        #expect(MessagesModel.heldOutPairs(of: [heldOut, trained], in: ["K-a1": .example], profile: .p1).isEmpty)
        let pairs = MessagesModel.heldOutPairs(
            of: [heldOut, trained], in: ["K-a1": .example, "K-r1": .example, "K-r2": other], profile: .p1)
        #expect(pairs == [.init(message: heldOut, profile: .p1, action: .example, referent: other)])
        #expect(pairs.first?.key == .init(profile: .p1, action: .example, referent: other))
    }

    /// A scratch book copied from a fixed book is labeled as that book until its first
    /// edit; only the synthetic DEMO book has a book ID to send (`origin`).
    @Test func aCopiedBookKeepsItsLabelUntilItsFirstEdit() throws {
        let synthetic = try JSONDecoder().decode(SyntheticBook.self, from: Data("""
            {"book_id":"DEMO-P2","atoms":[{"atom_id":"K-a1","recipe":\(Recipe.example.canonicalJSON),
             "pcm_sha256":"\(String(repeating: "a", count: 64))","n_samples":28800}]}
            """.utf8))
        var demo = ScratchBook.demo(synthetic)
        #expect(demo.origin == "DEMO-P2" && demo.label == "DEMO-P2")
        demo.set("K-a1", .example)  // no change
        #expect(demo.label == "DEMO-P2")
        demo.set("K-r1", .example)
        #expect(demo.origin == nil && demo.label == nil)

        let fallback = try JSONDecoder().decode(FallbackDemo.self, from: Data("""
            {"seed_label":"DEMO-fallback-v1","fallback_bank_hash":"\(String(repeating: "b", count: 64))",
             "bank":[],"book":[{"atom_id":"K-a1","recipe":\(Recipe.example.canonicalJSON),
             "pcm_sha256":"\(String(repeating: "c", count: 64))"}]}
            """.utf8))
        var copy = ScratchBook.fallback(fallback, profile: .p2)
        #expect(copy.origin == nil)
        #expect(copy.label == "DEMO fallback P2 (DEMO-fallback-v1)")
        #expect(copy.atoms == ["K-a1": .example])
        copy.set("K-a1", nil)
        #expect(copy.label == nil)
    }

    @Test func theMissingAtomsAreReadFromTheBook() throws {
        let message = try JSONDecoder().decode(MessageRef.self, from: Data("""
            {"message_id":"K-a1-r1","family":"K","action":"K-a1","referent":"K-r1","status":"trained",
             "training_wave":1,"heldout_set":null,"is_heldout":false}
            """.utf8))
        var book = ScratchBook()
        #expect(MessagesModel.missingAtoms(of: message, in: book) == ["K-a1", "K-r1"])
        book.set("K-a1", .example)
        #expect(MessagesModel.missingAtoms(of: message, in: book) == ["K-r1"])
        book.set("K-r1", .example)
        #expect(MessagesModel.missingAtoms(of: message, in: book).isEmpty)
    }
}

@Suite("Raw-JSON editor")
@MainActor
struct PlainTextEditorTests {
    /// SwiftUI's TextEditor turned typed straight quotes into curly ones (E_JSON for valid
    /// JSON). The editor's text view has every automatic substitution off, and its own
    /// checking pass (what runs between keystrokes) leaves the JSON as typed.
    @Test func typedJSONIsNotSubstituted() {
        let textView = NSTextView(frame: NSRect(x: 0, y: 0, width: 400, height: 100))
        PlainTextEditor.configure(textView)
        #expect(!textView.isAutomaticQuoteSubstitutionEnabled)
        #expect(!textView.isAutomaticDashSubstitutionEnabled)
        #expect(!textView.isAutomaticTextReplacementEnabled)
        #expect(!textView.isAutomaticSpellingCorrectionEnabled)
        #expect(!textView.isContinuousSpellCheckingEnabled)
        #expect(!textView.isAutomaticLinkDetectionEnabled && !textView.isAutomaticDataDetectionEnabled)
        #expect(!textView.smartInsertDeleteEnabled && !textView.isRichText)
        let json = #"{"total_ms": 600, "pitches": [-3, 0, 4], "note": "a -- b ... (c)"}"#
        textView.string = json
        textView.checkTextInDocument(nil)
        #expect(textView.string == json)
    }

    /// The editor as the Validator shows it: its AppKit text view is configured, edits
    /// reach the binding unchanged, and it reports the focus (Cmd-Return then validates
    /// the text).
    @Test func theHostedEditorKeepsTypedJSONAndReportsItsFocus() async throws {
        _ = NSApplication.shared
        final class State {
            var text = ""
            var focus: [Bool] = []
        }
        let state = State()
        let editor = PlainTextEditor(
            text: Binding(get: { state.text }, set: { state.text = $0 }),
            onFocusChange: { state.focus.append($0) }, accessibilityLabel: "Raw JSON text")
        let host = NSHostingView(rootView: editor.frame(width: 400, height: 120))
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 400, height: 120), styleMask: [.titled],
            backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        defer { window.close() }
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        try await Task.sleep(for: .milliseconds(50))
        let textView = try #require(textViews(in: host).first, "the editor has no AppKit text view")
        #expect(!textView.isAutomaticQuoteSubstitutionEnabled && !textView.isAutomaticDashSubstitutionEnabled)
        #expect(!textView.isAutomaticTextReplacementEnabled)
        #expect(textView.accessibilityLabel() == "Raw JSON text")

        // The focus is reported after the current update (from the main queue).
        #expect(window.makeFirstResponder(textView))
        try await waitUntil("the focus", timeout: .seconds(5)) { state.focus.last == true }
        let typed = #"{"total_ms": 600}"#
        textView.insertText(typed, replacementRange: NSRange(location: 0, length: 0))
        textView.checkTextInDocument(nil)
        try await Task.sleep(for: .milliseconds(50))
        #expect(textView.string == typed)
        #expect(state.text == typed)
        #expect(window.makeFirstResponder(nil))
        try await waitUntil("the focus loss", timeout: .seconds(5)) { state.focus.last == false }
    }
}

@Suite("Nonlexical model")
@MainActor
struct NonlexicalModelTests {
    static func asset(id: String, file: String, pcm: String) throws -> NonlexicalAsset {
        try JSONDecoder().decode(NonlexicalAsset.self, from: Data("""
            {"id":"\(id)","kind":"calibration","profile":null,"n_samples":4800,"duration_ms":100.0,
             "peak_dbfs":-20.0,"rms_dbfs":-23.0,"active_rms_dbfs":-23.0,
             "pcm_sha256":"\(pcm)","file_sha256":"\(file)","description":"synthetic test asset"}
            """.utf8))
    }

    /// A fetched clip is played and shown as verified only for the two hashes it was
    /// checked against: after a Reload that lists other bytes under the same asset ID
    /// (another checkout or engine version), the old clip is not used.
    @Test func aClipBelongsToItsHashesNotToTheAssetID() async throws {
        let samples = (0..<4_800).map { Int16(($0 * 7) % 2_000 - 1_000) }
        let wav = CanonicalWAV.fileData(samples: samples)
        let file = Hashing.sha256Hex(wav)
        let pcm = Hashing.sha256Hex(CanonicalWAV.pcmData(samples: samples))
        let clip = try await Offload.clip(AudioPayload(wavB64: wav.base64EncodedString(), fileSHA256: file, pcmSHA256: pcm))
        let model = NonlexicalModel()
        let listed = try Self.asset(id: "CAL-TEST", file: file, pcm: pcm)
        #expect(model.clip(for: listed) == nil)
        model.remember(clip)
        #expect(model.clip(for: listed) == clip)
        let other = String(repeating: "a", count: 64)
        #expect(model.clip(for: try Self.asset(id: "CAL-TEST", file: other, pcm: other)) == nil)
        #expect(model.clip(for: try Self.asset(id: "CAL-TEST", file: file, pcm: other)) == nil)
        #expect(model.clip(for: try Self.asset(id: "OTHER-ID", file: file, pcm: pcm)) == clip)
    }

    @Test func aStopWithoutAListChangesNothing() {
        let model = NonlexicalModel()
        model.bridgeDidStop()
        #expect(!model.isListingStale)
    }
}

/// The app models against the real bridge. Opt-in: `AV_SOUND_BRIDGE_TESTS=1 swift test`.
@Suite("AppModel with the live bridge (AV_SOUND_BRIDGE_TESTS=1)",
       .enabled(if: AppRepo.liveConfiguration != nil), .serialized)
@MainActor
struct AppModelLiveTests {
    /// Settings "Apply & Restart" (and the setup sheet's "Start Bridge") call
    /// `start(with:)` while a bridge runs; the new bridge has a fresh, empty temp store,
    /// so the Store section must forget the old book, root and head, and the Packages
    /// section the package whose directory went with the old bridge.
    @Test func replacingTheBridgeResetsTheStoreAndPackagesSections() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.selection = .store
        app.start(with: configuration)
        try await waitUntil("the bridge") { app.isReady }
        app.store.reset()
        try await waitUntil("store_reset") { app.store.root != nil }
        app.store.createBook()
        try await waitUntil("the book") { app.store.hasBook && app.store.listing != nil && !app.store.records.isEmpty }
        #expect(app.store.recordedHead != nil)
        app.packages.build()
        try await waitUntil("the package") { app.packages.package != nil && !app.packages.activity.isRunning("build") }
        let directory = try #require(app.packages.package?.dir)
        #expect(FileManager.default.fileExists(atPath: directory))

        app.start(with: configuration)
        #expect(app.store.root == nil)
        #expect(!app.store.hasBook)
        #expect(app.store.recordedHead == nil)
        #expect(app.store.listing == nil && app.store.records.isEmpty)
        #expect(app.packages.package == nil && app.packages.builtAt == nil)
        #expect(app.packages.notice != nil)
        try await waitUntil("the new bridge") { app.isReady }
        #expect(!app.store.hasBook)
        #expect(!FileManager.default.fileExists(atPath: directory))  // gone with the old bridge
        await app.shutdown()
    }

    /// While the old bridge stops (seconds when it is busy), a restart shows "Starting",
    /// never "Stopped" with a Start button.
    @Test func aRestartShowsStartingUntilTheNewBridgeIsReady() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.selection = .settings
        app.start(with: configuration)
        try await waitUntil("the bridge") { app.isReady }
        let old = try #require(app.client)
        let busy = Task { try? await old.goldenCheck() }  // the old bridge is busy when stopped
        try await Task.sleep(for: .milliseconds(50))
        app.start(with: configuration)
        var seen: [BridgeStatus] = [app.status]
        try await waitUntil("the new bridge") {
            if seen.last != app.status { seen.append(app.status) }
            return app.isReady
        }
        _ = await busy.value
        #expect(!seen.contains(.stopped), "\(seen.map(\.label))")
        #expect(seen.first == .starting)
        await app.shutdown()
    }

    /// With "Play after every change" off, Play (or Space) pressed while the render of an
    /// edit is pending plays that render once it lands; an edit alone plays nothing.
    @Test func playPlaysAPendingRenderWithAutoPlayOff() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.player.volume = 0
        app.selection = .recipeLab
        app.start(with: configuration)
        try await waitUntil("the first render") { app.isReady && app.lab.result != nil && !app.lab.isRendering }
        let lab = app.lab
        lab.autoPlay = false

        app.recipe.pitches[0] = app.recipe.pitches[0] == 0 ? 1 : 0
        #expect(!lab.isCurrent)
        lab.play()
        try await waitUntil("the render") { lab.isCurrent && !lab.isRendering }
        let clip = try #require(lab.clip)
        // A play was started: the clip plays (or, without an audio output, the player
        // reports why it could not).
        #expect(app.player.current?.pcmSHA256 == clip.audio.pcmSHA256 || app.player.lastError != nil)

        app.stopPlayback()
        app.recipe.pitches[1] = app.recipe.pitches[1] == 0 ? 1 : 0
        try await waitUntil("the second render") { lab.isCurrent && !lab.isRendering }
        #expect(app.player.current == nil && !app.player.isPlaying)
        #expect(app.nowPlaying == nil)
        await app.shutdown()
    }

    /// A click on a held-out cell stops the message that plays (the held-out card says
    /// nothing is played). A composed message follows the scratch book: replacing one of
    /// its atoms composes it again, without playing; clearing the book shows the missing
    /// atoms.
    @Test func messagesFollowTheBookAndPlayNothingUnderARefusal() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.player.volume = 0
        app.selection = .messages
        app.profile = .p2
        app.start(with: configuration)
        try await waitUntil("the grammar") { app.isReady && app.grammar != nil }
        let grammar = try #require(app.grammar)
        let trained = try #require(grammar.messages.first { $0.family == "K" && !$0.isHeldout })
        let heldOut = try #require(grammar.messages.first { $0.family == "K" && $0.isHeldout })
        app.loadDemoBook()
        try await waitUntil("the DEMO book") { app.book.count == 16 }
        let messages = app.messages

        messages.select(trained)
        try await waitUntil("the composed message", timeout: .seconds(30)) { composedClip(messages) != nil }
        let played = try #require(composedClip(messages))
        #expect(app.player.current?.pcmSHA256 == played.audio.pcmSHA256 || app.player.lastError != nil)
        messages.select(heldOut)
        #expect(app.nowPlaying == nil && !app.player.isPlaying && app.player.current == nil)
        try await waitUntil("the refusal", timeout: .seconds(30)) { isHeldOut(messages) }
        #expect(app.nowPlaying == nil && !app.player.isPlaying)

        messages.select(trained)
        try await waitUntil("the composed message", timeout: .seconds(30)) { composedClip(messages) != nil }
        let before = try #require(composedClip(messages))
        app.setAtom(trained.action, recipe: .example)  // Recipe Lab "Add to Book (replace)"
        #expect(!app.isPlaying(before))
        try await waitUntil("the recomposed message", timeout: .seconds(30)) {
            composedClip(messages).map { $0.audio.pcmSHA256 != before.audio.pcmSHA256 } ?? false
        }
        guard case .composed(_, _, let clip, let expected)? = messages.outcome else {
            Issue.record("not composed")
            return
        }
        #expect(clip.audio.pcmSHA256 == expected.compositeSHA256)
        #expect(app.nowPlaying == nil && app.player.current == nil)  // a recomposition is silent

        app.clearBook()
        guard case .missingAtoms(let message, let missing)? = messages.outcome else {
            Issue.record("the cleared book still shows a composed message")
            return
        }
        #expect(message.messageID == trained.messageID)
        #expect(Set(missing) == [trained.action, trained.referent])
        await app.shutdown()
    }

    /// A trained message that the scratch book makes equal to a held-out message (here:
    /// the referent slot holds the recipe of the held-out message's referent) is never
    /// composed or played: neither when it is clicked, nor when the book changes under a
    /// composed message. With distinct atoms again it is composed (silently).
    @Test func aTrainedMessageEqualToAHeldOutMessageIsNotComposed() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.player.volume = 0
        app.selection = .messages
        app.profile = .p2
        app.start(with: configuration)
        try await waitUntil("the grammar") { app.isReady && app.grammar != nil }
        let grammar = try #require(app.grammar)
        // A trained (a, r) and a held-out (a, r') of the same family.
        let pair = grammar.trainedMessages.lazy.compactMap { trained in
            grammar.heldoutMessages.first { $0.action == trained.action && $0.referent != trained.referent }
                .map { (trained, $0) }
        }.first
        let (trained, heldOut) = try #require(pair)
        app.loadDemoBook()
        try await waitUntil("the DEMO book") { app.book.count == 16 }
        let demo = app.book
        let client = try #require(app.client)
        let heldOutHash = try await client.compositeHash(
            action: try #require(demo.reference(heldOut.action)),
            referent: try #require(demo.reference(heldOut.referent)), profile: .p2, bookID: demo.origin)
        let messages = app.messages

        // Clicked with the duplicate in place: refused before any audio exists.
        app.setAtom(trained.referent, recipe: demo.atoms[heldOut.referent])  // Recipe Lab "Add to Book"
        messages.select(trained)
        try await waitUntil("the refusal", timeout: .seconds(30)) { sameAsHeldOut(messages) != nil }
        let refused = try #require(sameAsHeldOut(messages))
        #expect(refused.heldOut.contains(.init(message: heldOut, origin: .scratchBook)))
        #expect(refused.hash.compositeSHA256 == heldOutHash.compositeSHA256)
        #expect(app.nowPlaying == nil && app.player.current == nil && app.player.lastError == nil)

        // Distinct atoms again: composed, and its audio is not the held-out message's.
        app.books[.p2] = demo
        try await waitUntil("the composed message", timeout: .seconds(30)) { composedClip(messages) != nil }
        let clip = try #require(composedClip(messages))
        #expect(clip.audio.pcmSHA256 != heldOutHash.compositeSHA256)

        // The duplicate arrives under the composed message: its clip goes, and it is refused.
        app.play(clip)
        app.setAtom(trained.referent, recipe: demo.atoms[heldOut.referent])
        #expect(composedClip(messages) == nil)
        #expect(!app.isPlaying(clip) && app.nowPlaying == nil)
        try await waitUntil("the refusal", timeout: .seconds(30)) { sameAsHeldOut(messages) != nil }
        #expect(app.player.current == nil)
        await app.shutdown()
    }

    /// Following the refusal card by changing another atom does not make the refused audio
    /// playable: the pair is still refused, as the DEMO book's held-out message (here the
    /// edited book is no longer the DEMO book) or as the held-out message the refusal
    /// showed (a book of random recipes). Nothing is composed or played.
    @Test func aRefusedPairStaysRefusedWhenAnotherAtomChanges() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.player.volume = 0
        app.selection = .messages
        app.profile = .p1
        app.start(with: configuration)
        try await waitUntil("the grammar") { app.isReady && app.grammar != nil }
        let grammar = try #require(app.grammar)
        let pair = grammar.trainedMessages.lazy.compactMap { trained in
            grammar.heldoutMessages.first { $0.action == trained.action && $0.referent != trained.referent }
                .map { (trained, $0) }
        }.first
        let (trained, heldOut) = try #require(pair)
        let client = try #require(app.client)
        let messages = app.messages
        func expectRefused(as origin: MessagesModel.HeldOutTwin.Origin, hash: String) async throws {
            try await waitUntil("the refusal", timeout: .seconds(60)) {
                sameAsHeldOut(messages) != nil && !messages.isAsking
            }
            let refused = try #require(sameAsHeldOut(messages))
            #expect(refused.heldOut.contains(.init(message: heldOut, origin: origin)), "\(refused.heldOut.map(\.text))")
            #expect(refused.hash.compositeSHA256 == hash)
            #expect(composedClip(messages) == nil)
            #expect(app.nowPlaying == nil && app.player.current == nil && app.player.lastError == nil)
        }

        // The DEMO book: the referent slot gets the held-out referent's recipe (refused),
        // then the held-out referent gets a recipe of its own, as the card advises.
        app.loadDemoBook()
        try await waitUntil("the DEMO book") { app.book.count == 16 }
        let demo = app.book
        let demoHash = try await client.compositeHash(
            action: try #require(demo.reference(heldOut.action)),
            referent: try #require(demo.reference(heldOut.referent)), profile: .p1, bookID: demo.origin)
        app.setAtom(trained.referent, recipe: demo.atoms[heldOut.referent])
        messages.select(trained)
        try await expectRefused(as: .scratchBook, hash: demoHash.compositeSHA256)
        app.setAtom(heldOut.referent, recipe: try await client.randomRecipe(seed: 9_001))
        try await expectRefused(as: .demoBook("DEMO-P1"), hash: demoHash.compositeSHA256)

        // A book of random recipes, which the DEMO book does not know.
        var book = ScratchBook()
        for (index, slot) in [trained.action, trained.referent, heldOut.referent].enumerated() {
            book.set(slot, try await client.randomRecipe(seed: 7_100 + index))
        }
        app.book = book
        try await waitUntil("the composed message", timeout: .seconds(60)) { composedClip(messages) != nil }
        app.stopPlayback()
        let randomHash = try await client.compositeHash(
            action: try #require(book.reference(heldOut.action)),
            referent: try #require(book.reference(heldOut.referent)), profile: .p1, bookID: nil)
        app.setAtom(trained.referent, recipe: book.atoms[heldOut.referent])
        try await expectRefused(as: .scratchBook, hash: randomHash.compositeSHA256)
        app.setAtom(heldOut.referent, recipe: try await client.randomRecipe(seed: 7_200))
        try await expectRefused(as: .earlier, hash: randomHash.compositeSHA256)
        await app.shutdown()
    }

    /// A message that waits for its atoms names the atoms that are missing now: adding or
    /// removing one of them updates the list (the card and the cell agree).
    @Test func theMissingAtomsFollowTheBook() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.selection = .messages
        app.profile = .p2
        app.start(with: configuration)
        try await waitUntil("the grammar") { app.isReady && app.grammar != nil }
        let trained = try #require(app.grammar?.messages.first { $0.family == "K" && !$0.isHeldout })
        let messages = app.messages
        func missing() -> [String]? {
            if case .missingAtoms(_, let missing)? = messages.outcome { return missing }
            return nil
        }
        messages.select(trained)
        #expect(missing() == [trained.action, trained.referent])
        app.setAtom(trained.action, recipe: app.recipe)  // Recipe Lab "Add to Book"
        #expect(missing() == [trained.referent])
        app.setAtom(trained.action, recipe: nil)
        #expect(missing() == [trained.action, trained.referent])
        app.setAtom(trained.referent, recipe: app.recipe)
        #expect(missing() == [trained.action])
        await app.shutdown()
    }

    /// A message that waits for its atoms is composed when they arrive from another
    /// section, but plays only when Messages is visible: elsewhere it would sound like
    /// that section's own sound.
    @Test func aWaitingMessagePlaysOnlyInMessages() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.player.volume = 0
        app.selection = .messages
        app.profile = .p2
        app.start(with: configuration)
        try await waitUntil("the grammar") { app.isReady && app.grammar != nil }
        let trained = try #require(app.grammar?.messages.first { $0.family == "K" && !$0.isHeldout })
        let messages = app.messages
        messages.select(trained)
        guard case .missingAtoms? = messages.outcome else {
            Issue.record("an empty book must leave the message waiting")
            return
        }

        app.selection = .validator
        app.loadDemoBook()
        try await waitUntil("the composed message", timeout: .seconds(30)) { composedClip(messages) != nil }
        #expect(app.nowPlaying == nil && app.player.current == nil)
        #expect(app.player.lastError == nil)  // no sound was even attempted

        app.selection = .messages
        app.clearBook()
        guard case .missingAtoms? = messages.outcome else {
            Issue.record("the cleared book must leave the message waiting")
            return
        }
        app.loadDemoBook()
        try await waitUntil("the composed message", timeout: .seconds(30)) { composedClip(messages) != nil }
        let clip = try #require(composedClip(messages))
        #expect(app.player.current?.pcmSHA256 == clip.audio.pcmSHA256 || app.player.lastError != nil)
        await app.shutdown()
    }

    /// A profile change while a fallback set loads is not lost: the current profile's set
    /// is loaded after the first one, and its scans are shown.
    @Test func aProfileChangeDuringAFallbackLoadIsFollowed() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.selection = .fallback
        app.profile = .p1
        app.start(with: configuration)
        try await waitUntil("the bridge") { app.isReady }
        let fallback = app.fallback
        fallback.loadIfNeeded()
        #expect(fallback.activity.isRunning("load"))
        app.profile = .p2
        fallback.loadIfNeeded()  // what the view's onChange(of: profile) does
        try await waitUntil("the P2 set", timeout: .seconds(60)) {
            fallback.demoProfile == .p2 && !fallback.activity.isRunning("load")
        }
        #expect(!fallback.isStale)
        fallback.scanCurrentBook()
        try await waitUntil("the scan") { !fallback.activity.isRunning("scan") && fallback.scan != nil }
        #expect(fallback.currentScan != nil)
        await app.shutdown()
    }

    /// A profile change outside the Recipe Lab re-renders the lab's motif without playing
    /// it (no sound is even attempted: no current sound, no audio error).
    @Test func aProfileChangeOutsideTheLabPlaysNothing() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.player.volume = 0
        app.selection = .messages
        app.profile = .p2
        app.start(with: configuration)
        try await waitUntil("the first render") { app.isReady && app.lab.result != nil && !app.lab.isRendering }
        for profile in [Profile.p3, .p1] {
            app.profile = profile
            try await waitUntil("the \(profile.rawValue) render") {
                app.lab.result?.profile == profile && !app.lab.isRendering
            }
            #expect(app.nowPlaying == nil)
            #expect(app.player.current == nil && !app.player.isPlaying)
            #expect(app.player.lastError == nil)
        }
        await app.shutdown()
    }

    /// A scan belongs to its profile's bank: after a profile change it is not shown (or
    /// starred) against the new bank, and it is dropped once the new bank loads. Putting
    /// a bank recipe into the scratch book makes the scan reject it.
    @Test func fallbackScanFollowsTheProfile() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.selection = .fallback
        app.profile = .p1
        app.start(with: configuration)
        try await waitUntil("the bridge") { app.isReady }
        let fallback = app.fallback
        fallback.load()
        try await waitUntil("the P1 bank") { fallback.demoProfile == .p1 && !fallback.activity.isRunning("load") }

        // Before any scan, nothing says which row a scan reaches.
        fallback.bankSelection = 10
        #expect(fallback.putHint?.hasPrefix("Scan first") == true)
        fallback.scanCurrentBook()
        try await waitUntil("the first scan") { fallback.currentScan != nil && !fallback.activity.isRunning("scan") }
        #expect(fallback.currentScan?.selectedIndex == 0)
        #expect(fallback.bankSelection == 0)  // the scan selects its entry in the table
        #expect(fallback.putHint == nil)
        fallback.bankSelection = 10  // never reached: putting it in the book shows nothing
        #expect(fallback.putHint?.contains("stopped at #0") == true)
        fallback.usedText = "10"
        #expect(fallback.putHint?.contains("used") == true)
        fallback.usedText = ""

        // The selected bank #0 put into K-a1: the scan rejects #0 (E_DUPLICATE) and selects #1.
        fallback.bankSelection = 0
        fallback.targetSlot = "K-a1"
        fallback.putSelectedInScratchBook()
        #expect(app.books[.p1]?.atoms["K-a1"] == fallback.demo?.bank.first?.recipe)
        fallback.scanCurrentBook()
        try await waitUntil("the scan") {
            fallback.currentScan?.selectedIndex == 1 && !fallback.activity.isRunning("scan")
        }
        let scan = try #require(fallback.currentScan)
        #expect(scan.log.first?.outcome == "rejected")
        #expect(scan.log.first?.codes.contains("E_DUPLICATE") == true)
        #expect(scan.selectedIndex == 1)

        // Again with the newly selected #1, in another slot: both are rejected.
        #expect(fallback.bankSelection == 1 && fallback.putHint == nil)
        fallback.targetSlot = "K-a2"
        fallback.putSelectedInScratchBook()
        fallback.scanCurrentBook()
        try await waitUntil("the second scan") {
            (fallback.currentScan?.selectedIndex ?? 0) > 1 && !fallback.activity.isRunning("scan")
        }
        let second = try #require(fallback.currentScan)
        #expect(Array(second.log.prefix(2).map(\.outcome)) == ["rejected", "rejected"])

        app.profile = .p2
        #expect(fallback.currentScan == nil)  // at once, before the P2 bank arrives
        fallback.loadIfNeeded()
        try await waitUntil("the P2 bank") { fallback.demoProfile == .p2 && !fallback.activity.isRunning("load") }
        #expect(fallback.scan == nil && fallback.scanProfile == nil)
        await app.shutdown()
    }

    /// While the bank shown is another profile's (its load failed, or has not run: here
    /// the profile changed without the view's reload), Scan is off: a scan would run for
    /// the current profile and never be shown. The load's error stays shown. Once the
    /// current profile's set is loaded, Scan works again.
    @Test func scanIsOffWhileAnotherProfilesBankIsShown() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.selection = .fallback
        app.profile = .p2
        app.start(with: configuration)
        try await waitUntil("the bridge") { app.isReady }
        let fallback = app.fallback
        fallback.load()
        try await waitUntil("the P2 bank") { fallback.demoProfile == .p2 && !fallback.activity.isRunning("load") }
        #expect(fallback.scanBlocker == nil)

        app.profile = .p3  // no reload: the P2 bank stays shown
        #expect(fallback.isStale)
        let blocker = try #require(fallback.scanBlocker)
        #expect(blocker.contains("Reload") && blocker.contains("P2") && blocker.contains("P3"))
        fallback.activity.error = "the P3 load failed"  // as a failed load leaves it
        fallback.scanCurrentBook()
        #expect(!fallback.activity.isRunning("scan"))
        #expect(fallback.scan == nil && fallback.bankSelection == nil)
        #expect(fallback.activity.error == "the P3 load failed")

        fallback.load()  // Reload
        try await waitUntil("the P3 bank") { fallback.demoProfile == .p3 && !fallback.activity.isRunning("load") }
        #expect(fallback.scanBlocker == nil)
        fallback.scanCurrentBook()
        try await waitUntil("the scan") { fallback.currentScan != nil && !fallback.activity.isRunning("scan") }
        #expect(fallback.scanProfile == .p3)
        await app.shutdown()
    }

    /// Going back to a book with "Open DEMO-Px" (the button's title once the store has the
    /// book) brings back that book's own recorded head and tamper state: not the other
    /// book's head, damage or verification. The switch is logged as information, never as
    /// a refusal.
    @Test func returningToABookKeepsItsOwnHeadAndDamage() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.selection = .store
        app.profile = .p2
        app.start(with: configuration)
        try await waitUntil("the bridge") { app.isReady }
        let store = app.store
        store.reset()
        try await waitUntil("store_reset") { store.root != nil && !store.activity.isBusy }
        #expect(store.bookAction(for: .p2) == .create("DEMO-P2"))
        store.createOrOpenBook()
        try await waitUntil("DEMO-P2") { store.bookID == "DEMO-P2" && store.listing != nil && !store.activity.isBusy }
        #expect(store.bookAction(for: .p2) == .current("DEMO-P2"))
        store.commitCurrent()  // the example recipe as K-a1 (ADD_ONE)
        try await waitUntil("the commit") { store.listing?.entries.count == 1 && !store.activity.isBusy }
        let p2Head = try #require(store.listing?.chainHead)
        #expect(store.recordedHead == p2Head)

        app.profile = .p1
        #expect(store.bookAction(for: .p1) == .create("DEMO-P1"))
        store.createOrOpenBook()
        try await waitUntil("DEMO-P1") { store.bookID == "DEMO-P1" && store.listing != nil && !store.activity.isBusy }
        let p1Head = try #require(store.recordedHead)
        #expect(store.verification == nil)
        store.tamper(.editLogLine)
        try await waitUntil("the tamper") { store.verification != nil && !store.activity.isBusy }
        #expect(store.isTampered)
        #expect(store.verification?.ok == false)

        app.profile = .p2
        #expect(store.bookAction(for: .p2) == .open("DEMO-P2"))
        let refusals = store.events.filter { $0.kind == .refused }.count
        store.createOrOpenBook()  // "Open DEMO-P2"
        try await waitUntil("back to DEMO-P2") {
            store.bookID == "DEMO-P2" && store.listing != nil && !store.activity.isBusy
        }
        #expect(store.events.first?.kind == .info && store.events.first?.title.hasPrefix("Opened DEMO-P2") == true)
        #expect(store.events.filter { $0.kind == .refused }.count == refusals)
        #expect(store.bookAction(for: .p2) == .current("DEMO-P2"))
        #expect(store.bookAction(for: .p1) == .open("DEMO-P1"))
        #expect(store.bookProfile == .p2)
        #expect(!store.isTampered)
        #expect(store.recordedHead == p2Head)
        #expect(store.listing?.chainHead == p2Head)
        #expect(store.verification == nil && store.verifiedAgainstHead == nil)
        #expect(store.listingError == nil && store.recordsError == nil && store.records.count == 2)
        store.verifyAgainstRecordedHead = true
        store.verify()
        try await waitUntil("the verification") { store.verification != nil && !store.activity.isBusy }
        #expect(store.verification?.ok == true, "\(store.verification?.issues.map(\.code) ?? [])")
        #expect(store.verifiedAgainstHead == p2Head)

        app.profile = .p1
        store.createOrOpenBook()  // back to the damaged DEMO-P1: its damage is still reported
        try await waitUntil("back to DEMO-P1") { store.bookID == "DEMO-P1" && !store.activity.isBusy }
        #expect(store.events.filter { $0.kind == .refused }.count == refusals)
        #expect(store.isTampered)
        #expect(store.recordedHead == p1Head)
        #expect(store.verification == nil)
        #expect(store.listing == nil && store.listingError != nil)  // the store refuses the damaged book
        store.verify()
        try await waitUntil("the verification") { store.verification != nil && !store.activity.isBusy }
        #expect(store.verification?.ok == false)
        #expect(store.verifiedAgainstHead == p1Head)
        await app.shutdown()
    }

    /// A book the section did not know (created by another client of the same bridge) is
    /// offered as "Create"; the store answers BookExists and the section opens the book,
    /// logged as information, not as a refusal.
    @Test func creatingABookTheStoreAlreadyHasOpensIt() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.selection = .store
        app.profile = .p3
        app.start(with: configuration)
        try await waitUntil("the bridge") { app.isReady }
        let store = app.store
        store.reset()
        try await waitUntil("store_reset") { store.root != nil && !store.activity.isBusy }
        let client = try #require(app.client)
        let created = try await client.storeCreate(bookID: "DEMO-P3", profile: .p3)
        #expect(store.bookAction(for: .p3) == .create("DEMO-P3"))
        store.createOrOpenBook()
        try await waitUntil("DEMO-P3") { store.bookID == "DEMO-P3" && store.listing != nil && !store.activity.isBusy }
        #expect(store.events.first?.kind == .info)
        #expect(store.events.first?.title.contains("already has it") == true)
        #expect(!store.events.contains { $0.kind == .refused })
        #expect(store.recordedHead == created.chainHead)
        #expect(store.bookAction(for: .p3) == .current("DEMO-P3"))
        await app.shutdown()
    }

    /// "Flip a blob byte" pressed again never repairs the book (PROTOCOL.md, "Damaged
    /// books"): with one committed atom the second flip is refused (logged), and the
    /// verification that follows still reports the damage.
    @Test func aRepeatedBlobFlipKeepsTheBookDamaged() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.selection = .store
        app.profile = .p2
        app.start(with: configuration)
        try await waitUntil("the bridge") { app.isReady }
        let store = app.store
        store.reset()
        try await waitUntil("store_reset") { store.root != nil && !store.activity.isBusy }
        store.createOrOpenBook()
        try await waitUntil("DEMO-P2") { store.bookID == "DEMO-P2" && store.listing != nil && !store.activity.isBusy }
        store.commitCurrent()
        try await waitUntil("the commit") { store.listing?.entries.count == 1 && !store.activity.isBusy }

        store.tamper(.flipBlobByte)
        try await waitUntil("the first flip") { store.verification != nil && !store.activity.isBusy }
        #expect(store.verification?.ok == false)
        #expect(store.verification?.issues.map(\.code).contains("E_BLOB_HASH") == true)
        let refusals = store.events.filter { $0.kind == .refused }.count

        store.tamper(.flipBlobByte)
        try await waitUntil("the second flip") { !store.activity.isBusy }
        #expect(store.events.filter { $0.kind == .refused }.count == refusals + 1)
        #expect(store.events.contains { $0.kind == .refused && ($0.detail ?? "").contains("already damaged") })
        #expect(!store.events.contains { $0.title.hasSuffix(": intact") })
        #expect(store.verification?.ok == false)
        #expect(store.verification?.issues.map(\.code).contains("E_BLOB_HASH") == true)
        #expect(store.activity.error == nil)
        await app.shutdown()
    }

    /// "Edit a log line" pressed again never repairs the book either: each press edits one
    /// more record, so the issues found only grow, and with every record edited the press
    /// is refused (logged) and changes nothing.
    @Test func aRepeatedLogEditKeepsTheBookDamaged() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.selection = .store
        app.profile = .p2
        app.start(with: configuration)
        try await waitUntil("the bridge") { app.isReady }
        let store = app.store
        store.reset()
        try await waitUntil("store_reset") { store.root != nil && !store.activity.isBusy }
        store.createOrOpenBook()
        try await waitUntil("DEMO-P2") { store.bookID == "DEMO-P2" && store.listing != nil && !store.activity.isBusy }
        store.commitCurrent()  // create_book (line 0) and one commit (line 1)
        try await waitUntil("the commit") { store.listing?.entries.count == 1 && !store.activity.isBusy }

        func issues() -> Set<String> {
            Set((store.verification?.issues ?? []).map { "\($0.code)@\($0.line.map(String.init) ?? "-")" })
        }
        var seen: Set<String> = []
        for line in [1, 0] {
            store.tamper(.editLogLine)
            try await waitUntil("the edit of line \(line)") { !store.activity.isBusy && issues().count > seen.count }
            #expect(store.verification?.ok == false)
            #expect(issues().isSuperset(of: seen) && issues().contains("E_RECORD_HASH@\(line)"))
            #expect(store.events.contains { $0.kind == .info && ($0.detail ?? "").contains("log line \(line) (") })
            seen = issues()
        }
        let refusals = store.events.filter { $0.kind == .refused }.count
        store.tamper(.editLogLine)
        try await waitUntil("the refusal") {
            !store.activity.isBusy && store.events.filter { $0.kind == .refused }.count == refusals + 1
        }
        #expect(store.events.contains { $0.kind == .refused && ($0.detail ?? "").contains("already edited") })
        #expect(issues() == seen)
        #expect(store.activity.error == nil)
        await app.shutdown()
    }

    /// A failed validation (here a threshold the engine refuses) shows its error without
    /// the previous verdict, which was about another request.
    @Test func aFailedValidationShowsNoEarlierVerdict() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.selection = .validator
        app.start(with: configuration)
        try await waitUntil("the bridge") { app.isReady }
        let validator = app.validator
        validator.validateCurrent()
        try await waitUntil("the verdict") { validator.outcome != nil && !validator.activity.isRunning("validate") }
        #expect(validator.outcome?.result.ok == true)
        validator.thresholdText = "1/10"
        #expect(!validator.isThresholdValid)
        validator.validateCurrent()
        #expect(validator.outcome == nil)  // cleared when the request starts
        try await waitUntil("the refusal") { !validator.activity.isRunning("validate") }
        #expect(validator.activity.error?.contains("1/10") == true)
        #expect(validator.outcome == nil)
        validator.resetThreshold()
        validator.validateCurrent()
        try await waitUntil("the verdict") { validator.outcome != nil && !validator.activity.isRunning("validate") }
        #expect(validator.activity.error == nil)
        await app.shutdown()
    }

    /// A message request that fails (here: the bridge is stopped) leaves nothing to wait
    /// for: the card shows the failure and a retry instead of "Asking the engine", also
    /// after the error banner is dismissed, and the retry composes the message once the
    /// bridge is back.
    @Test func aFailedMessageRequestOffersARetry() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.player.volume = 0
        app.selection = .messages
        app.profile = .p2
        app.start(with: configuration)
        try await waitUntil("the grammar") { app.isReady && app.grammar != nil }
        let trained = try #require(app.grammar?.messages.first { $0.family == "K" && !$0.isHeldout })
        app.loadDemoBook()
        try await waitUntil("the DEMO book") { app.book.count == 16 }
        let messages = app.messages
        #expect(!messages.hasFailed)

        app.stopBridge()
        try await waitUntil("the stop") { app.status == .stopped }
        messages.select(trained)
        try await waitUntil("the failed request", timeout: .seconds(30)) { !messages.isAsking }
        #expect(messages.selectedID == trained.messageID)
        #expect(messages.outcome == nil)
        #expect(messages.hasFailed)
        #expect(messages.activity.error != nil)
        messages.activity.error = nil  // the banner's close button
        #expect(messages.hasFailed)

        app.startBridge()
        try await waitUntil("the bridge") { app.isReady }
        messages.retry()
        #expect(messages.isAsking && !messages.hasFailed)
        try await waitUntil("the composed message", timeout: .seconds(30)) { composedClip(messages) != nil }
        #expect(!messages.hasFailed)
        await app.shutdown()
    }

    /// The Nonlexical list belongs to the bridge that sent it: after a restart (which may
    /// run another checkout) it is fetched again.
    @Test func theNonlexicalListIsFetchedAgainAfterARestart() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.selection = .nonlexical
        app.start(with: configuration)
        try await waitUntil("the bridge") { app.isReady }
        let model = app.nonlexical
        model.loadIfNeeded()
        try await waitUntil("the list") { !model.assets.isEmpty && !model.activity.isRunning("list") }
        #expect(!model.isListingStale)
        model.loadIfNeeded()
        #expect(!model.activity.isRunning("list"))  // a current list is not fetched again

        app.start(with: configuration)  // Settings: Apply & Restart
        #expect(model.isListingStale)
        try await waitUntil("the new bridge") { app.isReady }
        model.loadIfNeeded()  // what the view's onChange(of: isReady) does
        #expect(model.activity.isRunning("list"))
        try await waitUntil("the new list") { !model.isListingStale && !model.activity.isRunning("list") }
        #expect(!model.assets.isEmpty)
        await app.shutdown()
    }

    /// A commit or freeze after a self-consistent log cut does not move the recorded head
    /// (README: only "Require the recorded chain head" finds that cut): it moves only while
    /// the log still holds it, so Verify keeps reporting the cut (E_ANCHOR), never "intact".
    @Test func aWriteAfterALogCutKeepsTheRecordedHead() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.selection = .store
        app.profile = .p2
        app.start(with: configuration)
        try await waitUntil("the bridge") { app.isReady }
        let synthetic = try await #require(app.client).syntheticBook(profile: .p2)
        let store = app.store
        store.verifyAgainstRecordedHead = true
        store.reset()
        try await waitUntil("store_reset") { store.root != nil && !store.activity.isBusy }
        store.createOrOpenBook()
        try await waitUntil("DEMO-P2") { store.bookID == "DEMO-P2" && store.listing != nil && !store.activity.isBusy }
        func commit(_ atomID: String) async throws {
            app.recipe = try #require(synthetic.atom(atomID)?.recipe)
            store.commitAtomID = atomID
            store.commitCurrent()
            try await waitUntil("the commit of \(atomID)") {
                store.listing?.entries.contains { $0.atomID == atomID } == true && !store.activity.isBusy
            }
        }
        func verify() async throws -> [String] {
            store.verify()
            try await waitUntil("the verification") { !store.activity.isBusy }
            return store.verification?.issues.map(\.code) ?? ["not verified"]
        }
        try await commit("K-a1")
        try await commit("K-a2")
        let beforeCut = try #require(store.recordedHead)
        #expect(beforeCut == store.listing?.chainHead)  // writes move it while the log holds it

        store.tamper(.truncateLog)
        try await waitUntil("the cut") { store.verification != nil && !store.activity.isBusy }
        #expect(store.verification?.issues.map(\.code) == ["E_ANCHOR"])
        #expect(store.listing?.entries.map(\.atomID) == ["K-a1"])  // a self-consistent log
        #expect(store.recordedHead == beforeCut)
        #expect(store.events.contains { $0.title.contains("no longer holds the recorded head") })

        try await commit("K-a3")  // accepted by the store
        #expect(store.listing?.entries.map(\.atomID) == ["K-a1", "K-a3"])
        #expect(store.listing?.chainHead != beforeCut)
        #expect(store.recordedHead == beforeCut)
        #expect(try await verify() == ["E_ANCHOR"])

        store.freeze()
        try await waitUntil("the freeze") { store.listing?.isFrozen == true && !store.activity.isBusy }
        #expect(store.recordedHead == beforeCut)
        #expect(try await verify() == ["E_ANCHOR"])
        #expect(!store.events.contains { $0.title.hasSuffix(": intact") })
        await app.shutdown()
    }

    /// Settings "Apply & Restart" while requests are queued on the old bridge: they still
    /// succeed there, but the Store and Packages sections drop what they return (the old
    /// temp store and package are gone), so nothing of the old bridge is shown as current.
    @Test func repliesOfAReplacedBridgeAreDropped() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.selection = .packages
        app.start(with: configuration)
        try await waitUntil("the bridge") { app.isReady }
        let old = try #require(app.client)
        let busy = Task { try? await old.goldenCheck() }  // the requests below queue behind it
        try await Task.sleep(for: .milliseconds(30))
        app.packages.build()
        app.store.reset()
        app.start(with: configuration)
        #expect(app.packages.package == nil && app.store.root == nil)
        _ = await busy.value
        try await waitUntil("the new bridge and the old replies") {
            app.isReady && !app.packages.activity.isBusy && !app.store.activity.isBusy
        }
        #expect(app.packages.package == nil && app.packages.builtAt == nil)
        #expect(app.store.root == nil && !app.store.hasBook)
        #expect(!app.store.events.contains { $0.title == "Fresh temp store" })
        await app.shutdown()
    }

    /// Bridge > Stop Bridge while a replacement waits for the old bridge to stop: the new
    /// bridge is not launched, and the app shows Stopped (not Starting, then Ready). Start
    /// then launches it.
    @Test func aStopDuringAReplacementIsNotLost() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.selection = .settings
        app.start(with: configuration)
        try await waitUntil("the bridge") { app.isReady }
        let old = try #require(app.client)
        let busy = Task { try? await old.goldenCheck() }  // the old bridge stops slowly
        try await Task.sleep(for: .milliseconds(50))
        app.start(with: configuration)
        let new = try #require(app.client)
        try await Task.sleep(for: .milliseconds(100))
        try #require(app.isReplacing, "the old bridge stopped too early for this test")
        #expect(app.status == .starting)
        app.stopBridge()
        #expect(app.status == .stopped && !app.isReplacing)
        _ = await busy.value
        await old.stop()  // joins the old bridge's stop
        try await Task.sleep(for: .milliseconds(500))
        #expect(app.status == .stopped)
        #expect(await new.status == .stopped)
        app.startBridge()
        try await waitUntil("the new bridge") { app.isReady }
        #expect(app.client === new)
        await app.shutdown()
    }

    /// A row render that returns after another profile's set was loaded (the load was
    /// queued before it) marks only the row it checked: no row of the new set gets the
    /// "Rendered audio matched" seal or the row spinner.
    @Test func aRenderForAnotherProfilesSetMarksNoRowOfTheNewSet() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.player.volume = 0
        app.selection = .fallback
        app.profile = .p2
        app.start(with: configuration)
        try await waitUntil("the bridge") { app.isReady }
        let fallback = app.fallback
        fallback.load()
        try await waitUntil("the P2 set") { fallback.demoProfile == .p2 && !fallback.activity.isRunning("load") }
        let p2Row = try #require(fallback.demo?.bank.first { $0.index == 3 })
        let client = try #require(app.client)
        let busy = Task { try? await client.goldenCheck() }  // the P3 load queues behind it
        try await Task.sleep(for: .milliseconds(30))
        app.profile = .p3
        fallback.loadIfNeeded()  // what the view's onChange(of: profile) does
        fallback.play(bank: p2Row)  // a click on the P2 row still shown
        #expect(fallback.activity.isRunning(FallbackModel.playKey(p2Row.pcmSHA256)))
        _ = await busy.value
        try await waitUntil("the P3 set and the render") { fallback.demoProfile == .p3 && !fallback.activity.isBusy }
        let p3Row = try #require(fallback.demo?.bank.first { $0.index == 3 })
        #expect(p3Row.pcmSHA256 != p2Row.pcmSHA256)
        #expect(!fallback.isVerified(p3Row.pcmSHA256))
        #expect(fallback.isVerified(p2Row.pcmSHA256))
        #expect(fallback.activity.error == nil)
        await app.shutdown()
    }

    /// The DEMO fallback book, copied into the scratch book with "Use as Scratch Book"
    /// (labeled as that book until the first edit), is guarded like the DEMO book: moving
    /// a held-out message's referent recipe into a trained message's referent slot does
    /// not compose that held-out message's audio under the trained ID.
    @Test func aTrainedMessageEqualToAHeldOutMessageOfTheFallbackBookIsNotComposed() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.player.volume = 0
        app.selection = .fallback
        app.profile = .p1
        app.start(with: configuration)
        try await waitUntil("the grammar") { app.isReady && app.grammar != nil }
        let (trained, heldOut) = try #require(trainedAndHeldOutPair(app.grammar))
        let fallback = app.fallback
        fallback.load()
        try await waitUntil("the P1 set") { fallback.demoProfile == .p1 && !fallback.activity.isRunning("load") }
        fallback.useBookAsScratch()
        let copy = app.book
        #expect(copy.count == 16 && copy.origin == nil)
        #expect(copy.label == "DEMO fallback P1 (DEMO-fallback-v1)")
        let heldOutHash = try await #require(app.client).compositeHash(
            action: try #require(copy.reference(heldOut.action)),
            referent: try #require(copy.reference(heldOut.referent)), profile: .p1, bookID: nil)

        // Validator & Book: open the held-out referent in the Recipe Lab, remove it and the
        // trained referent, then put the current recipe into the trained referent.
        app.recipe = try #require(copy.atoms[heldOut.referent])
        app.setAtom(heldOut.referent, recipe: nil)
        app.setAtom(trained.referent, recipe: nil)
        app.setAtom(trained.referent, recipe: app.recipe)
        #expect(app.book.label == nil)  // edited
        app.selection = .messages
        let messages = app.messages
        messages.select(trained)
        try await waitUntil("the refusal", timeout: .seconds(60)) { sameAsHeldOut(messages) != nil && !messages.isAsking }
        let refused = try #require(sameAsHeldOut(messages))
        #expect(refused.heldOut.contains(.init(message: heldOut, origin: .fallbackBook("DEMO-fallback-v1"))),
                "\(refused.heldOut.map(\.text))")
        #expect(refused.hash.compositeSHA256 == heldOutHash.compositeSHA256)
        #expect(composedClip(messages) == nil)
        #expect(app.nowPlaying == nil && app.player.current == nil && app.player.lastError == nil)
        await app.shutdown()
    }

    /// A held-out message of a book state that Messages never showed (here a book of
    /// random recipes, edited before any message was selected) is not composed under a
    /// trained ID either: every state of the scratch book is recorded.
    @Test func aHeldOutMessageOfAnEarlierBookStateIsNotComposed() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.player.volume = 0
        app.selection = .validator
        app.profile = .p2
        app.start(with: configuration)
        try await waitUntil("the grammar") { app.isReady && app.grammar != nil }
        let (trained, heldOut) = try #require(trainedAndHeldOutPair(app.grammar))
        let client = try #require(app.client)
        var book = ScratchBook()
        for (index, slot) in [trained.action, trained.referent, heldOut.referent].enumerated() {
            book.set(slot, try await client.randomRecipe(seed: 8_300 + index))
        }
        app.book = book
        let heldOutHash = try await client.compositeHash(
            action: try #require(book.reference(heldOut.action)),
            referent: try #require(book.reference(heldOut.referent)), profile: .p2, bookID: nil)

        app.setAtom(trained.referent, recipe: book.atoms[heldOut.referent])
        app.setAtom(heldOut.referent, recipe: try await client.randomRecipe(seed: 8_400))
        app.selection = .messages
        let messages = app.messages
        messages.select(trained)
        try await waitUntil("the refusal", timeout: .seconds(60)) { sameAsHeldOut(messages) != nil && !messages.isAsking }
        let refused = try #require(sameAsHeldOut(messages))
        #expect(refused.heldOut == [.init(message: heldOut, origin: .earlier)], "\(refused.heldOut.map(\.text))")
        #expect(refused.hash.compositeSHA256 == heldOutHash.compositeSHA256)
        #expect(composedClip(messages) == nil)
        #expect(app.nowPlaying == nil && app.player.current == nil && app.player.lastError == nil)

        // A recipe of its own for the trained referent: composed.
        app.setAtom(trained.referent, recipe: try await client.randomRecipe(seed: 8_500))
        try await waitUntil("the composed message", timeout: .seconds(60)) { composedClip(messages) != nil }
        #expect(composedClip(messages)?.audio.pcmSHA256 != heldOutHash.compositeSHA256)
        await app.shutdown()
    }

    /// The review's sequence: in the DEMO book, the held-out referent's slot gets the
    /// trained referent's recipe, so the held-out message makes the trained message's
    /// audio (both are refused while the duplicate is in the book). Loading the DEMO book
    /// again, as the card offers, brings the trained message back with the audio it had
    /// before: it is the DEMO book's own message, which an earlier state never refuses.
    /// So does a book that keeps the trained message's two DEMO recipes. The held-out
    /// audio of the earlier state stays refused under other recipes.
    @Test func loadingTheDemoBookAgainBringsItsOwnMessagesBack() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.player.volume = 0
        app.selection = .messages
        app.profile = .p2
        app.start(with: configuration)
        try await waitUntil("the grammar") { app.isReady && app.grammar != nil }
        let (trained, heldOut) = try #require(trainedAndHeldOutPair(app.grammar))
        let client = try #require(app.client)
        app.loadDemoBook()
        try await waitUntil("the DEMO book") { app.book.count == 16 }
        let demo = app.book
        let messages = app.messages
        messages.select(trained)
        try await waitUntil("the composed message", timeout: .seconds(60)) { composedClip(messages) != nil }
        let original = try #require(composedClip(messages)).audio.pcmSHA256

        // The duplicate: refused, as this book's held-out message.
        app.setAtom(heldOut.referent, recipe: demo.atoms[trained.referent])
        try await waitUntil("the refusal", timeout: .seconds(60)) { sameAsHeldOut(messages) != nil && !messages.isAsking }
        let refused = try #require(sameAsHeldOut(messages))
        #expect(refused.heldOut.first == .init(message: heldOut, origin: .scratchBook), "\(refused.heldOut.map(\.text))")
        #expect(refused.hash.compositeSHA256 == original)
        #expect(app.nowPlaying == nil && app.player.current == nil)

        // Load DEMO Book: composed again, with the DEMO book's audio.
        app.loadDemoBook()
        try await waitUntil("the composed message", timeout: .seconds(60)) { composedClip(messages) != nil }
        #expect(app.book.origin == demo.origin)
        #expect(composedClip(messages)?.audio.pcmSHA256 == original)

        // An edited book that keeps the trained message's two DEMO recipes: still composed.
        app.setAtom(heldOut.referent, recipe: try await client.randomRecipe(seed: 9_300))
        try await waitUntil("the composed message", timeout: .seconds(60)) {
            composedClip(messages) != nil && !messages.isAsking
        }
        #expect(composedClip(messages)?.audio.pcmSHA256 == original)

        // The earlier state's held-out audio under another trained ID (not the DEMO book's
        // own message there): still refused, as that earlier state's held-out message.
        let grammar = try #require(app.grammar)
        let another = try #require(grammar.trainedMessages.first {
            $0.family == trained.family && $0.messageID != trained.messageID
        })
        let demoAction: Recipe = try #require(demo.atoms[trained.action])
        let demoReferent: Recipe = try #require(demo.atoms[trained.referent])
        var book = ScratchBook()
        book.set(another.action, demoAction)
        book.set(another.referent, demoReferent)
        app.book = book
        messages.select(another)
        try await waitUntil("the refusal", timeout: .seconds(60)) { sameAsHeldOut(messages) != nil && !messages.isAsking }
        let earlier = try #require(sameAsHeldOut(messages))
        #expect(earlier.heldOut == [.init(message: heldOut, origin: .earlier)], "\(earlier.heldOut.map(\.text))")
        #expect(earlier.hash.compositeSHA256 == original)
        #expect(app.nowPlaying == nil && app.player.current == nil)
        await app.shutdown()
    }

    /// A play still waiting on the bridge (a render queued behind golden_check) does not
    /// start its sound after Stop Playback, or after the section was left: Fallback and
    /// Nonlexical. The row is still marked verified.
    @Test func aPendingPlayDoesNotStartAfterAStopOrASectionChange() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.player.volume = 0
        app.selection = .fallback
        app.profile = .p2
        app.start(with: configuration)
        try await waitUntil("the bridge") { app.isReady && app.grammar != nil }
        let client = try #require(app.client)
        let fallback = app.fallback
        fallback.load()
        try await waitUntil("the P2 set") { fallback.demo != nil && !fallback.activity.isRunning("load") }
        let row = try #require(fallback.demo?.bank.first)

        // Fallback: Play, then Cmd-. while the render waits behind golden_check.
        var busy = Task { try? await client.goldenCheck() }
        try await Task.sleep(for: .milliseconds(30))
        fallback.play(bank: row)
        try await Task.sleep(for: .milliseconds(30))
        app.stopPlayback()
        _ = await busy.value
        try await waitUntil("the render") { !fallback.activity.isBusy }
        #expect(fallback.isVerified(row.pcmSHA256))
        #expect(app.nowPlaying == nil && app.player.current == nil && !app.player.isPlaying)

        // Fallback: Play, then another section (a held-out cell in Messages).
        busy = Task { try? await client.goldenCheck() }
        try await Task.sleep(for: .milliseconds(30))
        let other = try #require(fallback.demo?.bank.dropFirst().first)
        fallback.play(bank: other)
        try await Task.sleep(for: .milliseconds(30))
        app.selection = .messages
        _ = await busy.value
        try await waitUntil("the render") { !fallback.activity.isBusy }
        #expect(fallback.isVerified(other.pcmSHA256))
        #expect(app.nowPlaying == nil && app.player.current == nil && !app.player.isPlaying)

        // Nonlexical: Play fetches the asset; Cmd-. while the fetch waits.
        app.selection = .nonlexical
        let nonlexical = app.nonlexical
        nonlexical.loadIfNeeded()
        try await waitUntil("the asset list") { !nonlexical.assets.isEmpty && !nonlexical.activity.isBusy }
        let asset = try #require(nonlexical.assets.first)
        busy = Task { try? await client.goldenCheck() }
        try await Task.sleep(for: .milliseconds(30))
        nonlexical.play(asset)
        try await Task.sleep(for: .milliseconds(30))
        app.stopPlayback()
        _ = await busy.value
        try await waitUntil("the fetch") { !nonlexical.activity.isBusy }
        #expect(nonlexical.clip(for: asset) != nil)  // fetched and verified
        #expect(app.nowPlaying == nil && app.player.current == nil && !app.player.isPlaying)
        await app.shutdown()
    }

    /// Settings "Apply & Restart" (here to the same checkout) replaces the bridge, which
    /// may run another engine: the grammar (which names the held-out messages), the
    /// Determinism results and the Fallback set of the old bridge are dropped, and the
    /// grammar is loaded again from the new bridge.
    @Test func replacingTheBridgeDropsEverythingItsEngineSent() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.selection = .determinism
        app.profile = .p1
        app.start(with: configuration)
        try await waitUntil("the grammar") { app.isReady && app.grammar != nil }
        app.determinism.runSelfTest()
        app.fallback.load()
        try await waitUntil("the results") {
            app.determinism.selfTest != nil && app.fallback.demo != nil && !app.fallback.activity.isBusy
        }
        #expect(app.determinism.notice == nil)

        app.start(with: configuration)
        #expect(app.grammar == nil)
        #expect(app.determinism.selfTest == nil && app.determinism.checkedAt.isEmpty)
        #expect(app.determinism.notice != nil)
        #expect(app.fallback.demo == nil && app.fallback.currentScan == nil)
        try await waitUntil("the new bridge's grammar") { app.isReady && app.grammar != nil }
        #expect(app.determinism.selfTest == nil)
        app.determinism.runSelfTest()
        #expect(app.determinism.notice == nil)
        try await waitUntil("the new self-test") { app.determinism.selfTest != nil }
        await app.shutdown()
    }

    /// With "Play after every change" off, Play then Stop Playback (or a profile change)
    /// before the render lands cancels that Play also for the render of a later edit: no
    /// edit plays by itself. Play on a shown render still plays.
    @Test func aCancelledLabPlayDoesNotCarryOverToTheNextEdit() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.player.volume = 0
        app.selection = .recipeLab
        app.profile = .p2
        app.start(with: configuration)
        try await waitUntil("the first render") { app.isReady && app.lab.result != nil && !app.lab.isRendering }
        let lab = app.lab
        lab.autoPlay = false

        app.recipe.pitches[0] = app.recipe.pitches[0] == 0 ? 1 : 0
        lab.play()  // the render is on its way
        app.stopPlayback()  // Cmd-.
        app.recipe.pitches[1] = app.recipe.pitches[1] == 0 ? 1 : 0  // an edit before it lands
        try await waitUntil("the render") { lab.isCurrent && !lab.isRendering }
        #expect(app.nowPlaying == nil && app.player.current == nil && !app.player.isPlaying)

        app.recipe.pitches[0] = app.recipe.pitches[0] == 0 ? 1 : 0
        lab.play()
        app.profile = .p3  // also cancels it: the sound was asked for under P2
        try await waitUntil("the P3 render") { lab.isCurrent && !lab.isRendering }
        #expect(lab.result?.profile == .p3)
        #expect(app.nowPlaying == nil && app.player.current == nil && !app.player.isPlaying)

        lab.play()  // the render shown is current: it plays at once
        let clip = try #require(lab.clip)
        #expect(app.player.current?.pcmSHA256 == clip.audio.pcmSHA256 || app.player.lastError != nil)
        await app.shutdown()
    }

    /// A lab render in the background (its Play was cancelled by leaving the lab; then an
    /// edit or a profile change) takes no play request: it does not cancel the pending
    /// sound of the section shown, and it plays nothing itself.
    @Test func aBackgroundLabRenderKeepsAnotherSectionsPendingPlay() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.player.volume = 0
        app.selection = .recipeLab
        app.profile = .p2
        app.start(with: configuration)
        try await waitUntil("the first render") { app.isReady && app.lab.result != nil && !app.lab.isRendering }
        let lab = app.lab
        lab.autoPlay = false
        app.recipe.pitches[0] = app.recipe.pitches[0] == 0 ? 1 : 0
        lab.play()  // pending
        app.selection = .nonlexical  // leaves the lab: its Play is cancelled
        let other = app.requestPlay()  // a Nonlexical sound on its way
        app.recipe.pitches[1] = app.recipe.pitches[1] == 0 ? 1 : 0  // re-renders the lab's motif
        #expect(app.isLatest(other))
        app.profile = .p3  // Nonlexical does not use the profile; the lab re-renders again
        #expect(app.isLatest(other))
        try await waitUntil("the lab render") { lab.isCurrent && !lab.isRendering }
        #expect(app.isLatest(other))
        #expect(app.nowPlaying == nil && app.player.current == nil && !app.player.isPlaying)
        await app.shutdown()
    }

    /// A row render still on its way when the profile changes does not play the old
    /// profile's sound under the new profile's page (Fallback, and a Validator & Book atom
    /// through `renderAndPlay` with the request of its click). The row is still verified.
    @Test func aProfileChangeCancelsAPendingRowPlay() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.player.volume = 0
        app.selection = .fallback
        app.profile = .p2
        app.start(with: configuration)
        try await waitUntil("the bridge") { app.isReady }
        let fallback = app.fallback
        fallback.load()
        try await waitUntil("the P2 set") { fallback.demoProfile == .p2 && !fallback.activity.isRunning("load") }
        let row = try #require(fallback.demo?.bank.first)
        fallback.play(bank: row)  // the click
        app.profile = .p3  // Cmd-3 in the same moment
        fallback.loadIfNeeded()  // what the view's onChange(of: profile) does
        try await waitUntil("the render and the P3 set") { fallback.demoProfile == .p3 && !fallback.activity.isBusy }
        #expect(fallback.isVerified(row.pcmSHA256))
        #expect(fallback.activity.error == nil)
        #expect(app.nowPlaying == nil && app.player.current == nil && !app.player.isPlaying)

        app.selection = .validator
        let request = app.requestPlay()  // a click on a P3 book atom's Play
        let render = Task { try await app.renderAndPlay(.example, profile: .p3, request: request) }
        app.profile = .p1
        let clip = try await render.value
        #expect(clip.audio.durationSeconds > 0)  // rendered and verified, not played
        #expect(app.nowPlaying == nil && app.player.current == nil && !app.player.isPlaying)
        await app.shutdown()
    }

    /// A validation still running when the profile changes shows nothing afterwards:
    /// neither its verdict, which was about the old profile's book, nor its error.
    @Test func aValidationOfTheOldProfileShowsNothingAfterAProfileChange() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.selection = .validator
        app.profile = .p2
        app.start(with: configuration)
        try await waitUntil("the bridge") { app.isReady }
        let validator = app.validator
        validator.validateCurrent()
        app.profile = .p3
        try await waitUntil("the validation") { !validator.activity.isRunning("validate") }
        #expect(validator.outcome == nil)
        #expect(validator.activity.error == nil)

        validator.thresholdText = "1/10"  // refused by the engine
        validator.validateCurrent()
        app.profile = .p1
        try await waitUntil("the refusal") { !validator.activity.isRunning("validate") }
        #expect(validator.outcome == nil)
        #expect(validator.activity.error == nil)

        validator.resetThreshold()
        validator.validateCurrent()
        try await waitUntil("the P1 verdict") { validator.outcome != nil && !validator.activity.isRunning("validate") }
        #expect(validator.outcome?.profile == .p1)
        #expect(validator.activity.error == nil)
        await app.shutdown()
    }

    /// Right after a restart the bridge is ready a moment before its grammar arrives. A
    /// message asked for then (Try Again, or a book change) waits for the grammar instead
    /// of failing with nothing that asks again.
    @Test func aMessageRequestRightAfterARestartWaitsForTheGrammar() async throws {
        let configuration = try #require(AppRepo.liveConfiguration)
        let app = AppModel()
        app.player.volume = 0
        app.selection = .messages
        app.profile = .p2
        app.start(with: configuration)
        try await waitUntil("the grammar") { app.isReady && app.grammar != nil }
        let trained = try #require(app.grammar?.messages.first { $0.family == "K" && !$0.isHeldout })
        app.loadDemoBook()
        try await waitUntil("the DEMO book") { app.book.count == 16 }
        let messages = app.messages
        messages.select(trained)
        try await waitUntil("the composed message", timeout: .seconds(30)) { composedClip(messages) != nil }

        /// Restarts the bridge and returns as soon as it is ready (the grammar may still
        /// be on its way).
        func restartUntilReady() async throws {
            app.restartBridge()
            try await waitUntil("the stop") { !app.isReady }
            let clock = ContinuousClock()
            let deadline = clock.now.advanced(by: .seconds(180))
            while !app.isReady {
                guard clock.now < deadline else { throw WaitTimeout(what: "the restarted bridge") }
                await Task.yield()
            }
        }

        try await restartUntilReady()
        messages.retry()  // Try Again
        try await waitUntil("the composed message", timeout: .seconds(30)) {
            !messages.isAsking && composedClip(messages) != nil
        }
        #expect(!messages.hasFailed && messages.activity.error == nil)

        try await restartUntilReady()
        app.setAtom("Q-a4", recipe: .example)  // a book change: the message is asked for again
        #expect(messages.isAsking)
        try await waitUntil("the recomposed message", timeout: .seconds(30)) {
            !messages.isAsking && composedClip(messages) != nil
        }
        #expect(!messages.hasFailed && messages.activity.error == nil)
        await app.shutdown()
    }
}

@MainActor
private func textViews(in view: NSView) -> [NSTextView] {
    let own: [NSTextView] = (view as? NSTextView).map { [$0] } ?? []
    return own + view.subviews.flatMap { textViews(in: $0) }
}

@MainActor
private func tableViews(in view: NSView) -> [NSTableView] {
    let own: [NSTableView] = (view as? NSTableView).map { [$0] } ?? []
    return own + view.subviews.flatMap { tableViews(in: $0) }
}

@MainActor
private func composedClip(_ messages: MessagesModel) -> AudioClip? {
    if case .composed(_, _, let clip, _)? = messages.outcome { return clip }
    return nil
}

@MainActor
private func isHeldOut(_ messages: MessagesModel) -> Bool {
    if case .heldOut? = messages.outcome { return true }
    return false
}

@MainActor
private func sameAsHeldOut(
    _ messages: MessagesModel
) -> (heldOut: [MessagesModel.HeldOutTwin], hash: CompositeHashResult)? {
    if case .sameAsHeldOut(_, let heldOut, let hash)? = messages.outcome { return (heldOut, hash) }
    return nil
}

/// A trained message reference such as `K-a1-r1`.
private func trainedRef(_ id: String) throws -> MessageRef {
    let parts = id.split(separator: "-")
    return try JSONDecoder().decode(MessageRef.self, from: Data("""
        {"message_id":"\(id)","family":"\(parts[0])","action":"\(parts[0])-\(parts[1])","referent":"\(parts[0])-\(parts[2])",
         "status":"trained","training_wave":1,"heldout_set":null,"is_heldout":false}
        """.utf8))
}

/// A held-out message reference such as `K-a1-r2`.
private func heldOutRef(_ id: String) throws -> MessageRef {
    let parts = id.split(separator: "-")
    return try JSONDecoder().decode(MessageRef.self, from: Data("""
        {"message_id":"\(id)","family":"\(parts[0])","action":"\(parts[0])-\(parts[1])","referent":"\(parts[0])-\(parts[2])",
         "status":"H-V1","training_wave":null,"heldout_set":"H-V1","is_heldout":true}
        """.utf8))
}

/// A trained message (a, r) and a held-out message (a, r') of the same family.
private func trainedAndHeldOutPair(_ grammar: Grammar?) -> (MessageRef, MessageRef)? {
    guard let grammar else { return nil }
    return grammar.trainedMessages.lazy.compactMap { trained in
        grammar.heldoutMessages.first { $0.action == trained.action && $0.referent != trained.referent }
            .map { (trained, $0) }
    }.first
}
