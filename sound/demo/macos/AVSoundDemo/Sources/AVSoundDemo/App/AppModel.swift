import AVSoundDemoCore
import Foundation
import Observation

/// The app's state: the bridge (one `BridgeClient`), playback, the shared recipe and
/// profile, the scratch books, and one model per section.
///
/// Every bridge call is an `await` on the client actor; responses are decoded and audio is
/// verified off the main actor, so the UI never blocks.
@MainActor
@Observable
final class AppModel {
    enum Section: String, CaseIterable, Identifiable, Hashable {
        case recipeLab, validator, messages, nonlexical, store, fallback, packages, determinism, settings

        var id: String { rawValue }

        var title: String {
            switch self {
            case .recipeLab: "Recipe Lab"
            case .validator: "Validator & Book"
            case .messages: "Messages"
            case .nonlexical: "Nonlexical"
            case .store: "Store"
            case .fallback: "Fallback"
            case .packages: "Packages"
            case .determinism: "Determinism"
            case .settings: "Settings & About"
            }
        }

        var systemImage: String {
            switch self {
            case .recipeLab: "waveform"
            case .validator: "checkmark.seal"
            case .messages: "square.grid.4x3.fill"
            case .nonlexical: "speaker.wave.2"
            case .store: "lock.doc"
            case .fallback: "arrow.triangle.branch"
            case .packages: "shippingbox"
            case .determinism: "equal.circle"
            case .settings: "gearshape"
            }
        }

        /// Whether the shared profile applies to this section.
        var usesProfile: Bool {
            switch self {
            case .recipeLab, .validator, .messages, .store, .fallback, .determinism: true
            case .nonlexical, .packages, .settings: false
            }
        }

        static let groups: [(title: String, sections: [Section])] = [
            ("Sounds", [.recipeLab, .validator, .messages, .nonlexical]),
            ("Engine", [.store, .fallback, .packages, .determinism]),
            ("App", [.settings]),
        ]
    }

    /// A log entry with an app-wide ID (client IDs restart with each new client).
    struct LogLine: Identifiable, Hashable {
        let id: Int
        let entry: BridgeLogEntry
    }

    /// The clip being played. The playhead comes from `player.playbackPosition` (what the
    /// listener hears), not from the time since `play`: the output's latency can be
    /// longer than a message's 200 ms gap.
    struct NowPlaying: Equatable {
        let clipID: String
        let duration: TimeInterval
    }

    /// `UserDefaults` key of the uv path the user chose.
    nonisolated static let uvDefaultsKey = "AVSoundDemo.uvPath"

    /// `UserDefaults` key of the selected section (a launch argument
    /// `-AVSoundDemo.section messages` opens that section).
    nonisolated static let sectionDefaultsKey = "AVSoundDemo.section"

    /// The section the window shows. Never `nil`: the sidebar's list has a required
    /// selection (a Command-click on the selected row cannot empty it), so the shown
    /// section, `isLabVisible` and `isMessagesVisible` always agree.
    var selection: Section = AppModel.savedSection() {
        didSet { UserDefaults.standard.set(selection.rawValue, forKey: Self.sectionDefaultsKey) }
    }

    // MARK: Bridge

    private(set) var status: BridgeStatus = .stopped
    private(set) var client: BridgeClient?
    private(set) var configuration: ProcessBridgeTransport.Configuration?
    /// Bridge log lines (stderr, stray stdout, client notes) across restarts.
    private(set) var log: [LogLine] = []
    /// Why the bridge cannot be launched (shown in the setup sheet).
    var setupMessage: String?
    var isSetupPresented = false
    /// Paths edited in the setup sheet and in Settings.
    var draftRepoPath = ""
    var draftUVPath = ""

    var hello: Hello? { status.hello }
    var isReady: Bool { status.isReady }
    var repoURL: URL? { configuration?.currentDirectoryURL }
    var uvURL: URL? { configuration?.executableURL }

    /// The recipe value domain (from `hello`, or the schema's when not connected).
    var domain: DomainInfo? { hello?.domain }

    // MARK: Shared sound state

    let player = AudioPlayer()
    private(set) var nowPlaying: NowPlaying?

    /// The render profile used by every section.
    var profile: Profile = .p2 {
        didSet { if oldValue != profile { profileDidChange() } }
    }

    /// The Recipe Lab's recipe (also the "current recipe" of the other sections).
    var recipe: Recipe = .example {
        didSet { if oldValue != recipe { lab.scheduleRender(autoPlay: isLabVisible) } }
    }

    /// Whether the Recipe Lab is the visible section: only then does a re-render of the
    /// lab's motif play by itself.
    var isLabVisible: Bool { selection == .recipeLab }

    /// Whether Messages is the visible section: only then does a composed message play.
    var isMessagesVisible: Bool { selection == .messages }

    /// One scratch book per profile.
    var books: [Profile: ScratchBook] = [:] {
        didSet { if oldValue != books { messages.bookDidChange() } }
    }
    private(set) var grammar: Grammar?

    // MARK: Section models

    let lab = RecipeLabModel()
    let validator = ValidatorModel()
    let messages = MessagesModel()
    let nonlexical = NonlexicalModel()
    let store = StoreModel()
    let fallback = FallbackModel()
    let packages = PackageModel()
    let determinism = DeterminismModel()
    /// Errors of app-wide actions (book loading, playback of book atoms).
    let activity = Activity()

    @ObservationIgnored private var statusTask: Task<Void, Never>?
    @ObservationIgnored private var logTask: Task<Void, Never>?

    init() {
        lab.app = self
        validator.app = self
        messages.app = self
        nonlexical.app = self
        store.app = self
        fallback.app = self
        packages.app = self
        determinism.app = self
        // A sound the player cut off itself (output device change) never runs its
        // completion, so clear the playhead here.
        player.onInterruption = { [weak self] in self?.nowPlaying = nil }
    }

    private static func savedSection() -> Section {
        UserDefaults.standard.string(forKey: sectionDefaultsKey).flatMap(Section.init(rawValue:)) ?? .recipeLab
    }

    // MARK: Launch and setup

    /// Locates uv and the repository and starts the bridge, or opens the setup sheet.
    ///
    /// Repository order: `AV_SOUND_REPO`, then the path saved from the setup sheet or
    /// Settings, then `RepoLocator`'s search (the app's location, the current directory).
    /// A set `AV_SOUND_REPO` overrides the rest: when it is not the repository, the setup
    /// sheet says so (no other checkout is used in its place).
    /// uv: the saved path, then `UVLocator`'s search.
    func launch() {
        let defaults = UserDefaults.standard
        let savedUV = defaults.string(forKey: Self.uvDefaultsKey)?.nonEmpty
        let hasEnvironmentRepo = RepoLocator.environmentPath(ProcessInfo.processInfo.environment) != nil
        let savedRepo = defaults.string(forKey: RepoLocator.defaultsKey)?.nonEmpty
            .map { URL(fileURLWithPath: $0) }
            .flatMap { RepoLocator.isValidRepo($0) && !hasEnvironmentRepo ? $0 : nil }
        do {
            let configuration = try ProcessBridgeTransport.Configuration.locate(
                explicitUV: savedUV, explicitRepo: savedRepo)
            draftRepoPath = configuration.currentDirectoryURL?.path ?? ""
            draftUVPath = configuration.executableURL.path
            start(with: configuration)
        } catch {
            if case BridgeSetupError.environmentRepoInvalid(let path) = error {
                draftRepoPath = path  // shown as invalid next to the message
            } else {
                draftRepoPath = RepoLocator.locate()?.url.path ?? savedRepo?.path ?? ""
            }
            draftUVPath = UVLocator.locate(explicit: savedUV)?.path ?? savedUV ?? ""
            setupMessage = userMessage(error)
            isSetupPresented = true
        }
    }

    /// Validates the draft paths, persists them, and (re)starts the bridge with them.
    func applySetup() {
        let repo = URL(fileURLWithPath: draftRepoPath.trimmed.expandingTilde).standardizedFileURL
        let uvPath = draftUVPath.trimmed.expandingTilde
        guard RepoLocator.isValidRepo(repo) else {
            setupMessage = "\(repo.path) is not the repository: it must contain "
                + RepoLocator.requiredFiles.joined(separator: " and ") + "."
            isSetupPresented = true
            return
        }
        guard isExecutableFile(uvPath) else {
            setupMessage = "\(uvPath) is not an executable file. Choose the uv binary."
            isSetupPresented = true
            return
        }
        RepoLocator.persist(repo)
        UserDefaults.standard.set(uvPath, forKey: Self.uvDefaultsKey)
        setupMessage = nil
        isSetupPresented = false
        start(with: .bridge(uv: URL(fileURLWithPath: uvPath), repoRoot: repo))
    }

    /// Forgets the saved repository and uv paths (the next launch searches again).
    func forgetSavedPaths() {
        RepoLocator.clearPersisted()
        UserDefaults.standard.removeObject(forKey: Self.uvDefaultsKey)
    }

    func chooseRepository() {
        let start = draftRepoPath.nonEmpty.map { URL(fileURLWithPath: $0.expandingTilde) }
        if let url = Panels.chooseRepository(startingAt: start) { draftRepoPath = url.path }
    }

    func chooseUV() {
        let start = draftUVPath.nonEmpty.map { URL(fileURLWithPath: $0.expandingTilde).deletingLastPathComponent() }
        if let url = Panels.chooseUV(startingAt: start) { draftUVPath = url.path }
    }

    var draftRepoIsValid: Bool {
        guard let path = draftRepoPath.nonEmpty else { return false }
        return RepoLocator.isValidRepo(URL(fileURLWithPath: path.expandingTilde))
    }

    var draftUVIsValid: Bool {
        guard let path = draftUVPath.nonEmpty else { return false }
        return isExecutableFile(path.expandingTilde)
    }

    // MARK: Bridge lifecycle

    /// Replaces the client with one for `configuration` and starts it.
    ///
    /// The old client's own status updates are no longer applied once it is replaced, so
    /// the change to `.starting` goes through `apply`: when the old bridge was ready, its
    /// temp store and package are gone and the Store and Packages sections are reset (as
    /// for a restart), and the Nonlexical list is fetched again from the new bridge. The new client stays `.stopped` until the old bridge has stopped
    /// (seconds when it is busy), so its status is applied only from its own start on:
    /// until then the app shows `.starting`, not `.stopped` with a Start button.
    func start(with configuration: ProcessBridgeTransport.Configuration) {
        let previous = client
        statusTask?.cancel()
        logTask?.cancel()
        let client = BridgeClient(configuration: configuration)
        self.client = client
        self.configuration = configuration
        apply(.starting)
        appendLocalLog("Launching: \(configuration.commandLine)")
        statusTask = Task { [weak self] in
            var started = false
            for await newStatus in await client.statusUpdates() {
                guard let self, self.client === client else { return }
                if !started, newStatus == .stopped { continue }  // not started yet
                started = true
                self.apply(newStatus)
            }
        }
        logTask = Task { [weak self] in
            for await entry in await client.logUpdates() {
                guard let self, self.client === client else { return }
                self.appendLog(entry)
            }
        }
        Task {
            await previous?.stop()
            _ = try? await client.start()
        }
    }

    func restartBridge() {
        guard let client else {
            launch()
            return
        }
        Task { _ = try? await client.restart() }
    }

    func stopBridge() {
        guard let client else { return }
        Task { await client.stop() }
    }

    func startBridge() {
        guard let client else {
            launch()
            return
        }
        Task { _ = try? await client.start() }
    }

    /// Stops playback and the bridge (application termination).
    func shutdown() async {
        player.stop()
        nowPlaying = nil
        await client?.stop()
    }

    func clearLog() {
        log.removeAll()
        if let client { Task { await client.clearLog() } }
    }

    var logText: String {
        log.map { "[\($0.entry.source.rawValue)] \($0.entry.text)" }.joined(separator: "\n")
    }

    private func apply(_ newStatus: BridgeStatus) {
        let wasReady = status.isReady
        status = newStatus
        if newStatus.isReady, !wasReady {
            bridgeDidBecomeReady()
        } else if wasReady, !newStatus.isReady {
            store.bridgeDidStop()
            packages.bridgeDidStop()
            nonlexical.bridgeDidStop()
        }
    }

    private func bridgeDidBecomeReady() {
        if let hello, validator.thresholdText.trimmed.isEmpty {
            validator.thresholdText = hello.threshold
        }
        lab.scheduleRender(autoPlay: false, debounce: false)
        if grammar == nil, let client {
            activity.run("grammar") { [weak self] in
                let grammar = try await client.grammar()
                self?.grammar = grammar
            }
        }
    }

    @ObservationIgnored private var nextLogLineID = 1

    private func appendLocalLog(_ text: String) {
        appendLog(BridgeLogEntry(id: 0, date: Date(), source: .client, text: text))
    }

    private func appendLog(_ entry: BridgeLogEntry) {
        log.append(LogLine(id: nextLogLineID, entry: entry))
        nextLogLineID += 1
        if log.count > 1_500 { log.removeFirst(log.count - 1_000) }
    }

    // MARK: Profile and book

    /// The scratch book of the current profile.
    var book: ScratchBook {
        get { books[profile] ?? ScratchBook() }
        set { books[profile] = newValue }
    }

    var atomIDs: [String] { grammar?.atomIDs ?? AtomSlots.all }

    func setAtom(_ atomID: String, recipe: Recipe?) {
        book.set(atomID, recipe)
    }

    func clearBook() {
        book = ScratchBook()
    }

    /// Replaces the scratch book of the current profile by the synthetic DEMO book.
    func loadDemoBook() {
        guard let client else { return }
        let profile = profile
        activity.run("demoBook") { [weak self] in
            let synthetic = try await client.syntheticBook(profile: profile)
            self?.books[profile] = .demo(synthetic)
        }
    }

    private func profileDidChange() {
        // Re-render the lab's motif for the new profile, but play it only where it is
        // shown: elsewhere it would sound like that section's own sound.
        lab.scheduleRender(autoPlay: isLabVisible)
        messages.profileDidChange()
        validator.profileDidChange()
    }

    // MARK: Playback

    func play(_ clip: AudioClip) {
        nowPlaying = NowPlaying(clipID: clip.id, duration: clip.audio.durationSeconds)
        player.play(clip.audio) { [weak self] in
            if self?.nowPlaying?.clipID == clip.id { self?.nowPlaying = nil }
        }
        if !player.isPlaying { nowPlaying = nil }
    }

    func stopPlayback() {
        player.stop()
        nowPlaying = nil
    }

    func isPlaying(_ clip: AudioClip?) -> Bool {
        guard let clip, let nowPlaying else { return false }
        return nowPlaying.clipID == clip.id && player.isPlaying
    }

    /// Renders `recipe` on the bridge, verifies the audio and plays it. When
    /// `expectedPCM` is given, the waveform hash must equal it.
    @discardableResult
    func renderAndPlay(_ recipe: Recipe, profile: Profile, expectedPCM: String? = nil) async throws -> AudioClip {
        guard let client else { throw BridgeError.notRunning }
        let result = try await client.render(recipe, profile: profile)
        guard result.audio.hasAudio else {
            throw AppError("The motif overflowed (peak \(result.peak)); no canonical WAV exists.")
        }
        let clip = try await Offload.clip(result.audio)
        if let expectedPCM, clip.audio.pcmSHA256 != expectedPCM {
            throw AppError(
                "Rendered waveform hash \(Fmt.shortHash(clip.audio.pcmSHA256)) differs from the expected "
                    + "\(Fmt.shortHash(expectedPCM)).")
        }
        play(clip)
        return clip
    }
}
