import AVSoundDemoCore
import SwiftUI

struct ContentView: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        @Bindable var app = app
        NavigationSplitView {
            Sidebar()
                .navigationSplitViewColumnWidth(min: 190, ideal: 210, max: 260)
        } detail: {
            DetailView(section: app.selection)
                .navigationTitle(app.selection.title)
                .navigationSubtitle(app.status.label)
                .toolbar { MainToolbar() }
        }
        .sheet(isPresented: $app.isSetupPresented) {
            SetupSheet()
                .environment(app)
        }
    }
}

/// The section list. Internal for the test of its required selection.
struct Sidebar: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        @Bindable var app = app
        // A required selection (`Binding<Section>`, not `Section?`): the list never
        // empties it, so the detail and the model's visible section always agree.
        List(selection: $app.selection) {
            ForEach(AppModel.Section.groups, id: \.title) { group in
                Section(group.title) {
                    ForEach(group.sections) { section in
                        Label(section.title, systemImage: section.systemImage)
                            .tag(section)
                    }
                }
            }
        }
        .listStyle(.sidebar)
        .safeAreaInset(edge: .bottom) {
            Button {
                app.selection = .settings
            } label: {
                StatusLabel(status: app.status)
                    .font(.caption)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 10)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help("Bridge status (open Settings)")
            .background(.bar)
        }
    }
}

private struct DetailView: View {
    let section: AppModel.Section

    var body: some View {
        switch section {
        case .recipeLab: RecipeLabView()
        case .validator: ValidatorBookView()
        case .messages: MessagesView()
        case .nonlexical: NonlexicalView()
        case .store: StoreView()
        case .fallback: FallbackView()
        case .packages: PackagesView()
        case .determinism: DeterminismView()
        case .settings: SettingsView()
        }
    }
}

private struct MainToolbar: ToolbarContent {
    @Environment(AppModel.self) private var app

    var body: some ToolbarContent {
        @Bindable var app = app
        let section = app.selection
        ToolbarItemGroup(placement: .primaryAction) {
            if section.usesProfile, section != .recipeLab {
                Picker("Profile", selection: $app.profile) {
                    ForEach(Profile.allCases) { profile in
                        Text("\(profile.rawValue) \u{00B7} \(profile.f0Hz) Hz").tag(profile)
                    }
                }
                .pickerStyle(.segmented)
                .help("Render profile used by every section")
            }
            if app.player.isPlaying {
                Button {
                    app.stopPlayback()
                } label: {
                    Label("Stop Playback", systemImage: "stop.fill")
                }
                .help("Stop playback")
            }
        }
    }
}

/// First-run sheet: choose the repository and uv when they cannot be found.
struct SetupSheet: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label("Connect the sound engine", systemImage: "wrench.and.screwdriver")
                .font(.title2.weight(.semibold))
            Text("The demo runs the Python engine through uv:\n`uv run --frozen --project <repository>/sound python <repository>/sound/demo/macos/bridge/av_sound_bridge.py`.\nChoose the repository checkout (the folder that contains sound/pyproject.toml) and the uv executable. Both choices are saved: later launches use the saved uv, and the saved repository when AV_SOUND_REPO is not set and the app is not inside a checkout.")
                .fixedSize(horizontal: false, vertical: true)
            if let message = app.setupMessage {
                Label {
                    Text(message).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                } icon: {
                    Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                }
                .noticeStyle(.orange)
            }
            LocationFields()
            Text("No uv? Install it with Homebrew (`brew install uv`) or from astral.sh, then run `uv sync --project sound --locked` once in the repository.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Button("Quit") { NSApplication.shared.terminate(nil) }
                Spacer()
                Button("Not Now") { app.isSetupPresented = false }
                Button("Start Bridge") { app.applySetup() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(!app.draftRepoIsValid || !app.draftUVIsValid)
            }
        }
        .padding(24)
        .frame(width: 640)
    }
}
