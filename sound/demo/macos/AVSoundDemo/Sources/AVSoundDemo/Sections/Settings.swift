import AVSoundDemoCore
import AVSoundSpec
import SwiftUI

struct SettingsView: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        SectionPage(.settings, summary: "Where the engine lives, which versions are running, and what the bridge printed.") {
            BridgeStatusCard()
            LocationsCard()
            VersionsCard()
            BridgeLogCard()
            AboutCard()
        }
    }
}

private struct BridgeStatusCard: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        Card("Bridge", systemImage: "point.3.connected.trianglepath.dotted") {
            HStack {
                Button {
                    app.restartBridge()
                } label: {
                    Label("Restart Bridge", systemImage: "arrow.clockwise")
                }
                if app.status.isReady {
                    Button("Stop") { app.stopBridge() }
                } else if app.status == .stopped || isFailed {
                    Button("Start") { app.startBridge() }
                }
            }
        } content: {
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
                InfoRow("Status") { StatusLabel(status: app.status) }
                InfoRow("Protocol", "bridge_version \(BridgeClient.supportedBridgeVersion) (PROTOCOL.md)")
                if let configuration = app.configuration {
                    InfoRow("Command") {
                        Text(configuration.commandLine)
                            .font(.caption.monospaced())
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    private var isFailed: Bool {
        if case .failed = app.status { return true }
        return false
    }
}

struct StatusLabel: View {
    let status: BridgeStatus

    var body: some View {
        HStack(spacing: 6) {
            switch status {
            case .starting:
                ProgressView().controlSize(.small)
            default:
                Circle().fill(color).frame(width: 9, height: 9)
            }
            Text(text).lineLimit(2)
        }
    }

    private var color: Color {
        switch status {
        case .ready: .green
        case .starting: .orange
        case .stopped: .gray
        case .failed: .red
        }
    }

    private var text: String {
        switch status {
        case .ready(let hello): "Ready \u{00B7} renderer \(hello.rendererVersion)"
        case .starting: "Starting\u{2026}"
        case .stopped: "Stopped"
        case .failed(let message): "Failed: \(message)"
        }
    }
}

/// Repository and uv paths, shared with the setup sheet.
struct LocationFields: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        @Bindable var app = app
        Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 10) {
            GridRow {
                Text("Repository").gridColumnAlignment(.trailing)
                TextField("path to the acoustic-vocabularies checkout", text: $app.draftRepoPath)
                    .textFieldStyle(.roundedBorder)
                    .font(.body.monospaced())
                    .frame(minWidth: 300)
                Button("Choose\u{2026}") { app.chooseRepository() }
                validity(app.draftRepoIsValid, valid: "contains sound/pyproject.toml and the bridge",
                         invalid: "not the repository")
            }
            GridRow {
                Text("uv").gridColumnAlignment(.trailing)
                TextField("path to the uv executable", text: $app.draftUVPath)
                    .textFieldStyle(.roundedBorder)
                    .font(.body.monospaced())
                Button("Choose\u{2026}") { app.chooseUV() }
                validity(app.draftUVIsValid, valid: "executable", invalid: "not an executable file")
            }
        }
    }

    private func validity(_ ok: Bool, valid: String, invalid: String) -> some View {
        Image(systemName: ok ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
            .foregroundStyle(ok ? .green : .orange)
            .help(ok ? valid : invalid)
            .accessibilityLabel(ok ? "Valid: \(valid)" : "Invalid: \(invalid)")
    }
}

private struct LocationsCard: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        Card("Locations", systemImage: "folder",
             subtitle: "The bridge runs: uv run --frozen --project <repository>/sound python <repository>/sound/demo/macos/bridge/av_sound_bridge.py (--frozen: uv never rewrites sound/uv.lock). Applying saves both paths for the next launch.") {
            HStack {
                Button("Forget Saved Paths") { app.forgetSavedPaths() }
                    .help("The next launch searches AV_SOUND_REPO, the app's location, the current directory and PATH again")
                Button("Apply & Restart") { app.applySetup() }
                    .buttonStyle(.borderedProminent)
                    .disabled(!app.draftRepoIsValid || !app.draftUVIsValid)
            }
        } content: {
            LocationFields()
            if let message = app.setupMessage {
                ErrorBanner(message: message) { app.setupMessage = nil }
            }
            if let repo = app.repoURL, let uv = app.uvURL {
                Text("Running with \(repo.path) and \(uv.path).")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
        }
    }
}

private struct VersionsCard: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        Card("Engine", systemImage: "cpu", subtitle: "Reported by hello.") {
            if let hello = app.hello {
                Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
                    InfoRow("bridge_version", "\(hello.bridgeVersion)")
                    InfoRow("Renderer", hello.rendererVersion)
                    InfoRow("renderer_hash") { HashText(hash: hello.rendererHash, length: 32) }
                    InfoRow("renderer_recipe_schema_hash") { HashText(hash: hello.rendererRecipeSchemaHash, length: 32) }
                    InfoRow("Validator", hello.validatorVersion)
                    InfoRow("validator_hash") { HashText(hash: hello.validatorHash, length: 32) }
                    InfoRow("Python / numpy", "\(hello.python) / \(hello.numpy)")
                    InfoRow("Sample rate", "\(Fmt.int(hello.sampleRate)) Hz")
                    InfoRow("Message gap", "\(Fmt.int(hello.gapSamples)) samples")
                    InfoRow("Threshold", "\(hello.threshold) (\(hello.thresholdFloat))")
                    InfoRow("Profiles", hello.profiles.map { "\($0.id) \($0.f0Hz) Hz" }.joined(separator: " \u{00B7} "))
                    InfoRow("Domain") {
                        Text(verbatim: domainText(hello.domain))
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    InfoRow("Reason codes") {
                        FlowChips(items: hello.reasonCodes)
                    }
                    InfoRow("Features") {
                        Text(hello.featureNames.joined(separator: ", ")).font(.callout.monospaced())
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    InfoRow("Swift port", "AVSoundSpec renderer \(SpecRenderer.rendererVersion)")
                }
            } else {
                Text("Not connected.").foregroundStyle(.secondary)
            }
        }
    }
}

private func domainText(_ domain: DomainInfo) -> String {
    func list(_ values: [Int]) -> String { values.map(String.init).joined(separator: ", ") }
    let pitches = "\(Fmt.signed(domain.pitches.min() ?? 0))\u{2026}\(Fmt.signed(domain.pitches.max() ?? 0))"
    return "total_ms [\(list(domain.totalMs))] \u{00B7} pitches \(pitches) \u{00B7} rhythm [\(list(domain.rhythmWeights))] "
        + "\u{00B7} gaps [\(list(domain.gapsMs))] \u{00B7} amplitudes [\(domain.amplitudes.map(Fmt.amplitude).joined(separator: ", "))]"
}

private struct FlowChips: View {
    let items: [String]

    var body: some View {
        // At most nine short codes: two rows are enough.
        let half = (items.count + 1) / 2
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 4) { ForEach(items.prefix(half), id: \.self) { CodeChip(code: $0, tint: .secondary) } }
            HStack(spacing: 4) { ForEach(items.dropFirst(half), id: \.self) { CodeChip(code: $0, tint: .secondary) } }
        }
    }
}

private struct BridgeLogCard: View {
    @Environment(AppModel.self) private var app
    @State private var stderrOnly = false

    var body: some View {
        let lines = stderrOnly ? app.log.filter { $0.entry.source == .stderr } : app.log
        Card("Bridge log", systemImage: "terminal",
             subtitle: "The bridge's stderr (engine and uv diagnostics), stray stdout lines and the client's notes.") {
            HStack {
                Toggle("stderr only", isOn: $stderrOnly)
                CopyButton(text: app.logText, help: "Copy the log")
                Button("Clear") { app.clearLog() }
            }
        } content: {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 2) {
                        ForEach(lines) { line in
                            HStack(alignment: .firstTextBaseline, spacing: 8) {
                                Text(line.entry.date, format: .dateTime.hour().minute().second())
                                    .foregroundStyle(.tertiary)
                                Text(line.entry.source.rawValue)
                                    .foregroundStyle(color(line.entry.source))
                                    .frame(width: 44, alignment: .leading)
                                Text(line.entry.text)
                                    .textSelection(.enabled)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            .font(.caption.monospaced())
                            .id(line.id)
                        }
                    }
                    .padding(8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(height: 260)
                .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 6))
                .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(Color(nsColor: .separatorColor).opacity(0.6)))
                .onChange(of: lines.last?.id) {
                    if let last = lines.last?.id { proxy.scrollTo(last, anchor: .bottom) }
                }
                .onAppear {
                    if let last = lines.last?.id { proxy.scrollTo(last, anchor: .bottom) }
                }
            }
        }
    }

    private func color(_ source: BridgeLogEntry.Source) -> Color {
        switch source {
        case .stderr: .orange
        case .stdout: .red
        case .client: .blue
        }
    }
}

private struct AboutCard: View {
    var body: some View {
        Card("About", systemImage: "info.circle") {
            Text("AV Sound Demo drives the real Python sound engine (av_sound) through a JSON-lines bridge. Audio is played only after its canonical WAV bytes match both the file and the waveform SHA-256. Everything it shows is synthetic: DEMO books, the public demo fallback seed and a temp store that the bridge deletes when it exits.")
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
