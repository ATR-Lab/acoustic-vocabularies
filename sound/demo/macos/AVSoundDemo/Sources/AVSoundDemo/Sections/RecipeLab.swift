import AVSoundDemoCore
import Observation
import SwiftUI

// MARK: - Model

@MainActor
@Observable
final class RecipeLabModel {
    @ObservationIgnored weak var app: AppModel?

    private(set) var result: RenderResult?
    private(set) var clip: AudioClip?
    private(set) var isRendering = false
    private(set) var renderError: String?
    private(set) var notice: String?
    var autoPlay = true
    var admissibleOnly = true
    /// The last `random_recipe` seed (incremented by Random).
    private(set) var seed = 0
    let activity = Activity()

    @ObservationIgnored private var renderTask: Task<Void, Never>?
    @ObservationIgnored private var renderToken = 0
    /// The play that Play (or Space) asked for while the shown render was not current:
    /// the next render that lands plays, whatever "Play after every change" says, but only
    /// while this request is still the latest (`AppModel.isLatest`). Stop Playback, a
    /// click on another sound, a change of section or of profile cancel it, also for the
    /// renders of later edits.
    @ObservationIgnored private var playRequest: AppModel.PlayRequest?

    /// Whether the shown render belongs to the current recipe and profile.
    var isCurrent: Bool {
        guard let app, let result else { return false }
        return result.recipe == app.recipe && result.profile == app.profile
    }

    /// Renders the current recipe after a short debounce; a newer request replaces an
    /// older one, so only the last edit's render is shown. The render plays while the lab
    /// is visible when `autoPlay` and the "Play after every change" setting both allow it,
    /// or when Play asked for it (`play()`) and nothing cancelled that request since,
    /// unless another sound was asked for, Stop Playback was pressed, or the section or
    /// profile changed meanwhile (`AppModel.requestPlay()`). Only a render that plays by
    /// itself takes a request of its own, and only while the lab is visible: a render in
    /// the background never cancels another section's pending sound.
    func scheduleRender(autoPlay: Bool, debounce: Bool = true) {
        renderTask?.cancel()
        renderToken += 1
        guard let app, let client = app.client, app.isReady else {
            isRendering = false
            playRequest = nil
            return
        }
        if let asked = playRequest, !app.isLatest(asked) { playRequest = nil }  // cancelled since
        let token = renderToken
        let recipe = app.recipe
        let profile = app.profile
        let asked = playRequest
        let request = asked ?? (autoPlay && self.autoPlay && app.isLabVisible ? app.requestPlay() : nil)
        isRendering = true
        renderTask = Task { [weak self] in
            if debounce {
                do { try await Task.sleep(for: .milliseconds(150)) } catch { return }
            }
            do {
                let result = try await client.render(recipe, profile: profile)
                let clip: AudioClip? = result.audio.hasAudio ? try await Offload.clip(result.audio) : nil
                guard let self, token == self.renderToken else { return }
                self.result = result
                self.clip = clip
                self.renderError = nil
                self.isRendering = false
                self.playRequest = nil
                // Play only while the lab is still the visible section, and while no other
                // sound, stop, section or profile change came since the request.
                if let request, app.isLatest(request), app.isLabVisible, asked != nil || self.autoPlay {
                    if let clip { app.play(clip, for: request) } else { app.stopPlayback() }
                }
            } catch {
                guard let self, token == self.renderToken, !(error is CancellationError) else { return }
                self.renderError = userMessage(error)
                self.isRendering = false
                self.playRequest = nil
            }
        }
    }

    func randomRecipe() {
        guard let app, let client = app.client else { return }
        seed += 1
        let seed = seed
        let admissibleOnly = admissibleOnly
        activity.run("random") {
            app.recipe = try await client.randomRecipe(seed: seed, admissibleOnly: admissibleOnly)
        }
    }

    func reset() {
        app?.recipe = .example
    }

    /// Plays the current recipe: at once when its render is shown, otherwise as soon as
    /// it is rendered (also with "Play after every change" off), unless Stop Playback,
    /// another sound, or a change of section or profile cancels it first.
    func play() {
        guard let app else { return }
        if let clip, isCurrent {
            app.play(clip)
        } else {
            playRequest = app.requestPlay()
            scheduleRender(autoPlay: true, debounce: false)
        }
    }

    /// Writes the verified canonical WAV bytes to a file the user chooses.
    func exportWAV() {
        guard let clip, let result, isCurrent else { return }
        let name = "motif-\(result.profile.rawValue)-\(result.recipeSHA256.prefix(12)).wav"
        guard let url = Panels.saveWAV(suggestedName: name) else { return }
        do {
            try clip.audio.wavData.write(to: url, options: .atomic)
            notice = "Exported \(url.lastPathComponent) (\(Fmt.bytes(clip.audio.wavData.count)), file SHA-256 \(Fmt.shortHash(clip.audio.fileSHA256)))."
        } catch {
            activity.error = "Could not write \(url.lastPathComponent): \(error.localizedDescription)"
        }
    }

    func addToBook(_ atomID: String) {
        guard let app else { return }
        let replacing = app.book.atoms[atomID] != nil
        app.setAtom(atomID, recipe: app.recipe)
        notice = "\(replacing ? "Replaced" : "Added") \(atomID) in the \(app.profile.rawValue) scratch book."
    }

    func clearNotice() { notice = nil }
}

// MARK: - View

struct RecipeLabView: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        HStack(spacing: 0) {
            RecipeControls()
                .frame(width: 350)
            Divider()
            SectionPage(.recipeLab, summary: "Edit a 3-event motif and hear the real engine render it. Every change is rendered by av_sound through the bridge; the audio is played only after its file and waveform hashes are verified.") {
                RenderCard()
                MetadataCard()
                RecipeJSONCard()
            }
        }
    }
}

private struct RecipeControls: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        @Bindable var app = app
        @Bindable var lab = app.lab
        let domain = app.domain
        let totals = domain?.totalMs ?? RecipeDomain.totalMs
        let pitches = domain?.pitches ?? RecipeDomain.pitches
        let weights = domain?.rhythmWeights ?? RecipeDomain.rhythmWeights
        let gaps = domain?.gapsMs ?? RecipeDomain.gapsMs
        let amplitudes = domain?.amplitudes ?? RecipeDomain.amplitudes
        let pitchRange = (pitches.min() ?? -6)...(pitches.max() ?? 6)
        Form {
            Section {
                Picker("Profile", selection: $app.profile) {
                    ForEach(Profile.allCases) { profile in
                        Text(profile.rawValue).tag(profile)
                    }
                }
                .pickerStyle(.segmented)
                LabeledContent("Base frequency", value: "\(app.profile.f0Hz) Hz")
                Picker("total_ms", selection: $app.recipe.totalMs) {
                    ForEach(totals, id: \.self) { Text("\($0)").tag($0) }
                }
                .pickerStyle(.segmented)
            } header: {
                Text("Motif")
            }
            .requiresBridge()

            ForEach(0..<3, id: \.self) { j in
                Section {
                    Stepper(value: $app.recipe.pitches[j], in: pitchRange) {
                        LabeledContent("Pitch (semitones)") {
                            Text(Fmt.signed(app.recipe.pitches[j])).monospacedDigit()
                        }
                    }
                    Picker("Rhythm weight", selection: $app.recipe.rhythmWeights[j]) {
                        ForEach(weights, id: \.self) { Text("\($0)").tag($0) }
                    }
                    .pickerStyle(.segmented)
                    Picker("Amplitude", selection: $app.recipe.amplitudes[j]) {
                        ForEach(amplitudes, id: \.self) { Text(Fmt.amplitude($0)).tag($0) }
                    }
                    .pickerStyle(.segmented)
                } header: {
                    EventHeader(index: j)
                }
                .requiresBridge()
            }

            Section("Gaps") {
                ForEach(0..<2, id: \.self) { g in
                    Picker("Gap \(g + 1) (ms)", selection: $app.recipe.gapsMs[g]) {
                        ForEach(gaps, id: \.self) { Text("\($0)").tag($0) }
                    }
                    .pickerStyle(.segmented)
                }
            }
            .requiresBridge()

            Section {
                HStack {
                    ActionButton("Random", systemImage: "dice", isRunning: lab.activity.isRunning("random")) {
                        lab.randomRecipe()
                    }
                    .keyboardShortcut("r", modifiers: .command)
                    .help("random_recipe with the next seed (\u{2318}R)")
                    Button {
                        lab.reset()
                    } label: {
                        Label("Reset", systemImage: "arrow.counterclockwise")
                    }
                    .help("Back to the worked example of the renderer spec")
                    Spacer()
                    Text(lab.seed == 0 ? "no seed yet" : "seed \(lab.seed)")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
                Toggle("Admissible only (every event \u{2265} 2,880 samples)", isOn: $lab.admissibleOnly)
            } header: {
                Text("Generate")
            }
            .requiresBridge()

            Section("Playback") {
                Toggle("Play after every change", isOn: $lab.autoPlay)
                HStack {
                    Button {
                        if app.isPlaying(lab.clip) { app.stopPlayback() } else { lab.play() }
                    } label: {
                        Label(app.isPlaying(lab.clip) ? "Stop" : "Play",
                              systemImage: app.isPlaying(lab.clip) ? "stop.fill" : "play.fill")
                    }
                    .keyboardShortcut(.space, modifiers: [])
                    .buttonStyle(.borderedProminent)
                    Button {
                        lab.exportWAV()
                    } label: {
                        Label("Export WAV\u{2026}", systemImage: "square.and.arrow.down")
                    }
                    .disabled(lab.clip == nil || !lab.isCurrent)
                }
                Menu {
                    ForEach(AtomSlots.groups) { group in
                        Section(group.title) {
                            ForEach(group.ids, id: \.self) { atomID in
                                Button {
                                    lab.addToBook(atomID)
                                } label: {
                                    if app.book.atoms[atomID] != nil {
                                        Text("\(atomID)  (replace)")
                                    } else {
                                        Text(atomID)
                                    }
                                }
                            }
                        }
                    }
                } label: {
                    Label("Add to \(app.profile.rawValue) Book", systemImage: "text.badge.plus")
                }
                .help("Put the current recipe into an atom slot of the scratch book")
            }
            .requiresBridge()
        }
        .formStyle(.grouped)
    }
}

private struct EventHeader: View {
    @Environment(AppModel.self) private var app
    let index: Int

    var body: some View {
        HStack {
            Text("Event \(index + 1)")
            Spacer()
            if let result = app.lab.result, app.lab.isCurrent, index < result.eventSamples.count {
                let samples = result.eventSamples[index]
                Text("\(Fmt.int(samples)) samples \u{00B7} \(Fmt.ms(samples: samples))")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(samples < Fmt.minEventSamples ? Color.red : Color.secondary)
            }
        }
    }
}

private struct RenderCard: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        let lab = app.lab
        Card("Waveform", systemImage: "waveform.path", subtitle: subtitle) {
            HStack(spacing: 8) {
                if lab.isRendering { ProgressView().controlSize(.small) }
                PlayButton(clip: lab.isCurrent ? lab.clip : nil)
                    .buttonStyle(.bordered)
            }
        } content: {
            if let result = lab.result {
                ClipWaveform(clip: lab.clip, sampleCount: result.nSamples, spans: Self.spans(for: result))
                    .opacity(lab.isCurrent ? 1 : 0.45)
                if result.overflow {
                    Label("Overflow: |y| exceeds 32,767, so no canonical WAV exists and nothing can be played.",
                          systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.red)
                }
            } else {
                WaveformView(waveform: nil, sampleCount: app.recipe.totalMs * Fmt.samplesPerMs)
                    .opacity(0.4)
                    .overlay { Text(app.isReady ? "Rendering\u{2026}" : "Waiting for the bridge").foregroundStyle(.secondary) }
            }
            if let error = lab.renderError { ErrorBanner(message: error) }
            if let error = lab.activity.error { ErrorBanner(message: error) { lab.activity.error = nil } }
            if let notice = lab.notice {
                HStack {
                    Label(notice, systemImage: "checkmark.circle").foregroundStyle(.green)
                    Spacer()
                    Button("Dismiss") { lab.clearNotice() }.buttonStyle(.borderless)
                }
            }
        }
    }

    private var subtitle: String {
        guard let result = app.lab.result else { return "48 kHz mono, int16" }
        let state = app.lab.isCurrent ? "" : " (updating)"
        return "\(result.profile.rawValue) \u{00B7} \(Fmt.int(result.nSamples)) samples \u{00B7} \(Fmt.ms(samples: result.nSamples))\(state)"
    }

    static func spans(for result: RenderResult) -> [WaveSpan] {
        var spans: [WaveSpan] = []
        let events = min(result.eventOnsets.count, result.eventSamples.count)
        for j in 0..<events {
            let start = result.eventOnsets[j]
            let length = result.eventSamples[j]
            let pitch = j < result.recipe.pitches.count ? Fmt.signed(result.recipe.pitches[j]) : ""
            let amp = j < result.recipe.amplitudes.count ? Fmt.amplitude(result.recipe.amplitudes[j]) : ""
            spans.append(WaveSpan(
                id: "e\(j)", range: start..<(start + length), kind: .event,
                title: "E\(j + 1) \u{00B7} \(Fmt.ms(samples: length))",
                detail: "pitch \(pitch) \u{00B7} amp \(amp)",
                color: SpanPalette.events[j % SpanPalette.events.count],
                warning: length < Fmt.minEventSamples))
            if j + 1 < events {
                let end = start + length
                let next = result.eventOnsets[j + 1]
                if next > end {
                    spans.append(WaveSpan(
                        id: "g\(j)", range: end..<next, kind: .gap, title: "gap",
                        detail: Fmt.ms(samples: next - end)))
                }
            }
        }
        return spans
    }
}

private struct MetadataCard: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        Card("Render metadata", systemImage: "list.bullet.rectangle") {
            if let result = app.lab.result {
                Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 8) {
                    InfoRow("n_samples", Fmt.int(result.nSamples))
                    InfoRow("Duration", "\(Fmt.number(result.durationMs, digits: 0)) ms")
                    InfoRow("Event samples") { eventSamples(result) }
                    InfoRow("Event onsets", result.eventOnsets.map(Fmt.int).joined(separator: " \u{00B7} "))
                    InfoRow("Gap samples",
                            result.gapSamples.map { "\(Fmt.int($0)) (\(Fmt.ms(samples: $0)))" }.joined(separator: " \u{00B7} "))
                    InfoRow("Peak", "\(Fmt.int(result.peak)) \u{00B7} \(Fmt.dbfs(result.peakDbfs))")
                    InfoRow("RMS", "\(Fmt.number(result.rms, digits: 1)) LSB \u{00B7} \(Fmt.dbfs(Fmt.rmsDbfs(result.rms)))")
                    InfoRow("short_event") { shortEvent(result) }
                    InfoRow("overflow") {
                        Text(result.overflow ? "Yes: no canonical WAV (|y| > 32,767)" : "No")
                            .foregroundStyle(result.overflow ? Color.red : Color.primary)
                    }
                    InfoRow("nonfinite") {
                        Text(Fmt.yesNo(result.nonfinite)).foregroundStyle(result.nonfinite ? Color.red : Color.primary)
                    }
                    Divider().gridCellColumns(2)
                    InfoRow("pcm_sha256") { HashText(hash: result.audio.pcmSHA256) }
                    InfoRow("file_sha256") { HashText(hash: result.audio.fileSHA256) }
                    InfoRow("recipe_sha256") { HashText(hash: result.recipeSHA256) }
                    InfoRow("Verified") {
                        if let clip = app.lab.clip, app.lab.isCurrent {
                            PassFail(ok: true, text: "file and waveform hashes checked (\(Fmt.bytes(clip.audio.wavData.count)))")
                        } else {
                            Text("\u{2014}").foregroundStyle(.secondary)
                        }
                    }
                    InfoRow("Renderer", "\(result.rendererVersion) \u{00B7} profile \(result.profile.rawValue) (\(result.profile.f0Hz) Hz)")
                }
                .opacity(app.lab.isCurrent ? 1 : 0.55)
            } else {
                Text("No render yet.").foregroundStyle(.secondary)
            }
        }
    }

    private func eventSamples(_ result: RenderResult) -> some View {
        HStack(spacing: 10) {
            ForEach(Array(result.eventSamples.enumerated()), id: \.offset) { _, samples in
                Text("\(Fmt.int(samples)) (\(Fmt.ms(samples: samples)))")
                    .monospacedDigit()
                    .foregroundStyle(samples < Fmt.minEventSamples ? Color.red : Color.primary)
            }
        }
    }

    @ViewBuilder
    private func shortEvent(_ result: RenderResult) -> some View {
        if result.shortEvent {
            let short = result.eventSamples.enumerated().filter { $0.element < Fmt.minEventSamples }
                .map { "event \($0.offset + 1) has \(Fmt.int($0.element))" }
                .joined(separator: ", ")
            Text("Yes: \(short) < 2,880 samples (60 ms)")
                .foregroundStyle(.red)
                .fontWeight(.semibold)
        } else {
            Text("No: every event has at least 2,880 samples (60 ms)")
        }
    }
}

private struct RecipeJSONCard: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        Card("Canonical recipe JSON", systemImage: "curlybraces",
             subtitle: "Sorted keys, compact, amplitudes as 0.6/0.8/1.0: byte for byte the engine's canonical_json(). SHA-256 \(Fmt.shortHash(app.recipe.sha256, length: 16))") {
            CodeBlock(text: app.recipe.canonicalJSON, maxHeight: 80)
        }
    }
}
