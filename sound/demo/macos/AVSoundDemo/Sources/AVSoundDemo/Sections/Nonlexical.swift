import AVSoundDemoCore
import Observation
import SwiftUI

// MARK: - Model

@MainActor
@Observable
final class NonlexicalModel {
    @ObservationIgnored weak var app: AppModel?

    private(set) var assets: [NonlexicalAsset] = []
    var selection: NonlexicalAsset.ID?
    /// Verified audio by its `file_sha256` (fetched on first play). Not by asset ID: an ID
    /// can list other bytes after a Reload (another checkout or engine version), and a
    /// clip is played and shown as verified only for the hashes it was checked against.
    private(set) var clips: [String: AudioClip] = [:]
    /// The list came from a bridge that has stopped since: the next `loadIfNeeded` lists
    /// again (the new bridge may run another checkout).
    private(set) var isListingStale = false
    let activity = Activity()

    var selected: NonlexicalAsset? { assets.first { $0.id == selection } }

    /// The verified clip of `asset`: a fetched WAV that matched both of its listed hashes.
    func clip(for asset: NonlexicalAsset) -> AudioClip? {
        guard let clip = clips[asset.fileSHA256], clip.audio.fileSHA256 == asset.fileSHA256,
            clip.audio.pcmSHA256 == asset.pcmSHA256
        else { return nil }
        return clip
    }

    /// Keeps a clip that was checked against both hashes of a listed asset.
    func remember(_ clip: AudioClip) {
        clips[clip.audio.fileSHA256] = clip
    }

    func load() {
        guard let app, let client = app.client else { return }
        activity.run("list") { [weak self] in
            let assets = try await client.nonlexicalList()
            // A replaced bridge's list may not be the new bridge's (another checkout).
            guard let self, self.app?.isCurrent(client) == true else { return }
            self.assets = assets
            self.isListingStale = false
            // Clips of bytes the new list no longer names are dropped.
            let listed = Set(assets.map(\.fileSHA256))
            self.clips = self.clips.filter { listed.contains($0.key) }
            if !assets.contains(where: { $0.id == self.selection }) { self.selection = assets.first?.id }
        }
    }

    func loadIfNeeded() {
        if assets.isEmpty || isListingStale, app?.isReady == true, !activity.isRunning("list") { load() }
    }

    /// The bridge stopped: its list may not be the next bridge's.
    func bridgeDidStop() {
        if !assets.isEmpty { isListingStale = true }
    }

    func play(_ asset: NonlexicalAsset) {
        guard let app, let client = app.client else { return }
        selection = asset.id
        if let clip = clip(for: asset) {
            app.play(clip)
            return
        }
        activity.run("play-\(asset.id)") { [weak self] in
            let fetched = try await client.nonlexicalGet(id: asset.id)
            let clip = try await Offload.clip(fetched.audio)
            guard clip.audio.pcmSHA256 == asset.pcmSHA256, clip.audio.fileSHA256 == asset.fileSHA256 else {
                throw AppError("The audio of \(asset.id) does not match the hashes listed by nonlexical_list.")
            }
            guard app.isCurrent(client) else { return }  // the bridge was replaced meanwhile
            self?.remember(clip)
            app.play(clip)
        }
    }
}

// MARK: - View

struct NonlexicalView: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        let model = app.nonlexical
        SectionPage(.nonlexical, summary: "The reserved nonlexical assets: calibration tones, the READY cue and grammar clicks. They are not vocabulary; the validator keeps every atom away from them.") {
            AssetsCard()
            if let asset = model.selected {
                AssetDetail(asset: asset, clip: model.clip(for: asset))
            }
        }
        .onAppear { model.loadIfNeeded() }
        .onChange(of: app.isReady) { model.loadIfNeeded() }
    }
}

private struct AssetsCard: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        @Bindable var model = app.nonlexical
        Card("Reserved assets (\(model.assets.count))", systemImage: "speaker.wave.2",
             subtitle: "nonlexical_list. Play fetches the WAV with nonlexical_get and checks it against both listed hashes.") {
            ActionButton("Reload", systemImage: "arrow.clockwise", isRunning: model.activity.isRunning("list")) {
                model.load()
            }
            .requiresBridge()
        } content: {
            if let error = model.activity.error {
                ErrorBanner(message: error) { model.activity.error = nil }
            }
            if model.assets.isEmpty {
                if model.activity.isRunning("list") {
                    ProgressView("Loading assets\u{2026}")
                } else {
                    Text(app.isReady ? "No assets loaded." : "Waiting for the bridge.").foregroundStyle(.secondary)
                }
            } else {
                Table(model.assets, selection: $model.selection) {
                    TableColumn("") { asset in
                        AssetPlayButton(asset: asset)
                    }
                    .width(28)
                    TableColumn("ID") { asset in
                        Text(asset.id).font(.body.monospaced())
                    }
                    .width(min: 120, ideal: 170)
                    TableColumn("Kind") { asset in Text(asset.kind) }
                        .width(min: 70, ideal: 90)
                    TableColumn("Profile") { asset in
                        Text(asset.profile?.rawValue ?? "any")
                    }
                    .width(50)
                    TableColumn("Duration") { asset in
                        Text("\(Fmt.number(asset.durationMs, digits: 1)) ms").monospacedDigit()
                    }
                    .width(min: 70, ideal: 80)
                    TableColumn("Peak") { asset in
                        Text(Fmt.dbfs(asset.peakDbfs)).monospacedDigit()
                    }
                    .width(min: 80, ideal: 90)
                    TableColumn("RMS") { asset in
                        Text(Fmt.dbfs(asset.rmsDbfs)).monospacedDigit()
                    }
                    .width(min: 80, ideal: 90)
                    TableColumn("Active RMS") { asset in
                        Text(Fmt.dbfs(asset.activeRmsDbfs)).monospacedDigit()
                    }
                    .width(min: 80, ideal: 90)
                    TableColumn("pcm_sha256") { asset in
                        Text(Fmt.shortHash(asset.pcmSHA256)).font(.body.monospaced()).help(asset.pcmSHA256)
                    }
                    .width(min: 100, ideal: 120)
                }
                .frame(height: CGFloat(min(model.assets.count, 12)) * 26 + 34)
                .requiresBridge()
            }
        }
    }
}

private struct AssetPlayButton: View {
    @Environment(AppModel.self) private var app
    let asset: NonlexicalAsset

    var body: some View {
        let model = app.nonlexical
        let clip = model.clip(for: asset)
        Button {
            if app.isPlaying(clip) { app.stopPlayback() } else { model.play(asset) }
        } label: {
            if model.activity.isRunning("play-\(asset.id)") {
                ProgressView().controlSize(.mini)
            } else {
                Image(systemName: app.isPlaying(clip) ? "stop.fill" : "play.fill")
            }
        }
        .buttonStyle(.borderless)
        .help("Play \(asset.id)")
        .accessibilityLabel(app.isPlaying(clip) ? "Stop \(asset.id)" : "Play \(asset.id)")
    }
}

private struct AssetDetail: View {
    @Environment(AppModel.self) private var app
    let asset: NonlexicalAsset
    let clip: AudioClip?

    var body: some View {
        Card(asset.id, systemImage: "speaker.wave.2", subtitle: asset.description) {
            Button {
                if app.isPlaying(clip) { app.stopPlayback() } else { app.nonlexical.play(asset) }
            } label: {
                Label(app.isPlaying(clip) ? "Stop" : "Play", systemImage: app.isPlaying(clip) ? "stop.fill" : "play.fill")
            }
            .buttonStyle(.borderedProminent)
            .requiresBridge()
        } content: {
            if let clip {
                ClipWaveform(clip: clip, sampleCount: clip.audio.nSamples, height: 150)
            }
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
                InfoRow("Kind", asset.kind)
                InfoRow("Profile", asset.profile.map { "\($0.rawValue) (\($0.f0Hz) Hz)" } ?? "profile independent")
                InfoRow("Length", "\(Fmt.int(asset.nSamples)) samples \u{00B7} \(Fmt.number(asset.durationMs, digits: 1)) ms")
                InfoRow("Levels", "peak \(Fmt.dbfs(asset.peakDbfs)) \u{00B7} RMS \(Fmt.dbfs(asset.rmsDbfs)) \u{00B7} active RMS \(Fmt.dbfs(asset.activeRmsDbfs))")
                InfoRow("pcm_sha256") { HashText(hash: asset.pcmSHA256) }
                InfoRow("file_sha256") { HashText(hash: asset.fileSHA256) }
                if clip != nil {
                    InfoRow("Verified") { PassFail(ok: true, text: "the fetched WAV matches both listed hashes") }
                }
            }
        }
    }
}
