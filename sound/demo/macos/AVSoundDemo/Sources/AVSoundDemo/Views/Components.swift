import AVSoundDemoCore
import AppKit
import SwiftUI

// MARK: - Layout

/// A titled card.
struct Card<Content: View, Accessory: View>: View {
    let title: String
    var systemImage: String?
    var subtitle: String?
    @ViewBuilder var accessory: Accessory
    @ViewBuilder var content: Content

    init(
        _ title: String, systemImage: String? = nil, subtitle: String? = nil,
        @ViewBuilder accessory: () -> Accessory = { EmptyView() },
        @ViewBuilder content: () -> Content
    ) {
        self.title = title
        self.systemImage = systemImage
        self.subtitle = subtitle
        self.accessory = accessory()
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                VStack(alignment: .leading, spacing: 2) {
                    if let systemImage {
                        Label(title, systemImage: systemImage).font(.headline)
                    } else {
                        Text(title).font(.headline)
                    }
                    if let subtitle {
                        Text(subtitle).font(.callout).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                Spacer(minLength: 8)
                accessory
            }
            content
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(Color(nsColor: .separatorColor).opacity(0.6)))
    }
}

/// The scrolling page of a section: a header, the bridge notice and the content.
struct SectionPage<Content: View>: View {
    let section: AppModel.Section
    let summary: String
    @ViewBuilder var content: Content

    init(_ section: AppModel.Section, summary: String, @ViewBuilder content: () -> Content) {
        self.section = section
        self.summary = summary
        self.content = content()
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                SectionHeader(section: section, summary: summary)
                BridgeNotice()
                content
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

struct SectionHeader: View {
    let section: AppModel.Section
    let summary: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Label(section.title, systemImage: section.systemImage)
                .font(.title2.weight(.semibold))
            Text(summary)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// Shown while the bridge is not ready, with what to do about it.
struct BridgeNotice: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            switch app.status {
            case .ready:
                EmptyView()
            case .starting:
                HStack(spacing: 10) {
                    ProgressView().controlSize(.small)
                    Text("Starting the sound engine bridge\u{2026} The first start can take a while while uv prepares the environment.")
                        .foregroundStyle(.secondary)
                }
                .noticeStyle(.blue)
            case .stopped:
                HStack(spacing: 10) {
                    Image(systemName: "pause.circle").foregroundStyle(.secondary)
                    Text("The bridge is stopped. Controls are disabled until it is ready.")
                    Spacer()
                    Button("Start Bridge") { app.startBridge() }
                }
                .noticeStyle(.gray)
            case .failed(let message):
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.red)
                    VStack(alignment: .leading, spacing: 4) {
                        Text("The bridge is not running").font(.headline)
                        Text(message).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer()
                    Button("Restart") { app.restartBridge() }
                    Button("Settings\u{2026}") { app.selection = .settings }
                }
                .noticeStyle(.red)
            }
            if let error = app.player.lastError {
                // Shown on every section until dismissed or until a later sound plays.
                HStack(alignment: .top, spacing: 8) {
                    Label(error, systemImage: "speaker.slash").foregroundStyle(.orange)
                    Spacer(minLength: 0)
                    Button {
                        app.player.clearLastError()
                    } label: {
                        Image(systemName: "xmark")
                    }
                    .buttonStyle(.borderless)
                    .help("Dismiss")
                    .accessibilityLabel("Dismiss the audio message")
                }
                .noticeStyle(.orange)
            }
            if let error = app.activity.error {
                ErrorBanner(message: error) { app.activity.error = nil }
            }
        }
    }
}

extension View {
    func noticeStyle(_ tint: Color) -> some View {
        padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(tint.opacity(0.10), in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(tint.opacity(0.25)))
    }

    /// Disabled until the bridge is ready.
    func requiresBridge() -> some View { modifier(RequiresBridge()) }
}

struct RequiresBridge: ViewModifier {
    @Environment(AppModel.self) private var app

    func body(content: Content) -> some View {
        content.disabled(!app.isReady)
    }
}

// MARK: - Messages and badges

struct ErrorBanner: View {
    let message: String
    var onDismiss: (() -> Void)?

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "xmark.octagon.fill").foregroundStyle(.red)
            Text(message)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
            if let onDismiss {
                Button {
                    onDismiss()
                } label: {
                    Image(systemName: "xmark")
                }
                .buttonStyle(.borderless)
                .help("Dismiss")
                .accessibilityLabel("Dismiss")
            }
        }
        .noticeStyle(.red)
    }
}

/// A green check or a red cross with a text.
struct PassFail: View {
    let ok: Bool
    let text: String

    var body: some View {
        Label {
            Text(text)
        } icon: {
            Image(systemName: ok ? "checkmark.circle.fill" : "xmark.octagon.fill")
                .foregroundStyle(ok ? .green : .red)
        }
    }
}

/// A capsule with a short code (`E_DOMAIN`, `OK`).
struct CodeChip: View {
    let code: String
    var tint: Color = .red

    var body: some View {
        Text(code)
            .font(.caption.monospaced().weight(.semibold))
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .foregroundStyle(tint)
            .background(tint.opacity(0.14), in: Capsule())
            .overlay(Capsule().strokeBorder(tint.opacity(0.35)))
    }
}

struct Pill: View {
    let text: String
    var tint: Color = .secondary

    var body: some View {
        Text(text)
            .font(.caption.weight(.medium))
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .foregroundStyle(tint)
            .background(tint.opacity(0.12), in: Capsule())
    }
}

// MARK: - Hashes and copying

/// Copies `text`; shows a check mark for a moment.
struct CopyButton: View {
    let text: String
    var help = "Copy"
    @State private var copied = false

    var body: some View {
        Button {
            Panels.copy(text)
            copied = true
            Task {
                try? await Task.sleep(for: .seconds(1.2))
                copied = false
            }
        } label: {
            Image(systemName: copied ? "checkmark" : "doc.on.doc")
                .foregroundStyle(copied ? Color.green : Color.secondary)
                .frame(width: 14)
        }
        .buttonStyle(.borderless)
        .help(help)
        .accessibilityLabel(copied ? "Copied" : help)
    }
}

/// A truncated hash with the full value in the tooltip and a copy button.
struct HashText: View {
    let hash: String?
    var length = 16
    var tint: Color?

    var body: some View {
        HStack(spacing: 6) {
            Text(Fmt.shortHash(hash, length: length))
                .font(.body.monospaced())
                .foregroundStyle(tint ?? (hash == nil ? Color.secondary : Color.primary))
                .textSelection(.enabled)
                .help(hash ?? "none")
            if let hash { CopyButton(text: hash, help: "Copy the full SHA-256") }
        }
    }
}

/// Monospaced, selectable, wrapping text in a scrolling box with a copy button.
struct CodeBlock: View {
    let text: String
    var maxHeight: CGFloat = 220

    var body: some View {
        ZStack(alignment: .topTrailing) {
            ScrollView(.vertical) {
                Text(verbatim: text)
                    .font(.callout.monospaced())
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(10)
                    .padding(.trailing, 26)
            }
            .frame(maxHeight: maxHeight)
            .fixedSize(horizontal: false, vertical: true)
            CopyButton(text: text).padding(8)
        }
        .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 6))
        .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(Color(nsColor: .separatorColor).opacity(0.6)))
    }
}

/// One label/value row of a `Grid`.
struct InfoRow<Value: View>: View {
    let label: String
    @ViewBuilder var value: Value

    init(_ label: String, @ViewBuilder value: () -> Value) {
        self.label = label
        self.value = value()
    }

    var body: some View {
        GridRow(alignment: .firstTextBaseline) {
            Text(label)
                .foregroundStyle(.secondary)
                .gridColumnAlignment(.trailing)
            value
                .gridColumnAlignment(.leading)
        }
    }
}

extension InfoRow where Value == Text {
    init(_ label: String, _ text: String) {
        self.init(label) { Text(text) }
    }
}

/// A button with a spinner while its operation runs.
struct ActionButton: View {
    let title: String
    let systemImage: String
    var isRunning = false
    var prominent = false
    let action: () -> Void

    init(_ title: String, systemImage: String, isRunning: Bool = false, prominent: Bool = false, action: @escaping () -> Void) {
        self.title = title
        self.systemImage = systemImage
        self.isRunning = isRunning
        self.prominent = prominent
        self.action = action
    }

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if isRunning {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: systemImage)
                }
                Text(title)
            }
        }
        .modifier(ProminentIf(prominent: prominent))
        .disabled(isRunning)
    }
}

private struct ProminentIf: ViewModifier {
    let prominent: Bool

    func body(content: Content) -> some View {
        if prominent {
            content.buttonStyle(.borderedProminent)
        } else {
            content.buttonStyle(.bordered)
        }
    }
}

/// Play/stop for one clip.
struct PlayButton: View {
    @Environment(AppModel.self) private var app
    let clip: AudioClip?
    var title: String? = "Play"

    var body: some View {
        let playing = app.isPlaying(clip)
        Button {
            if playing {
                app.stopPlayback()
            } else if let clip {
                app.play(clip)
            }
        } label: {
            if let title {
                Label(playing ? "Stop" : title, systemImage: playing ? "stop.fill" : "play.fill")
            } else {
                Image(systemName: playing ? "stop.fill" : "play.fill")
            }
        }
        .disabled(clip == nil)
        .help(playing ? "Stop" : "Play the verified audio")
        .accessibilityLabel(playing ? "Stop" : title ?? "Play")
    }
}
