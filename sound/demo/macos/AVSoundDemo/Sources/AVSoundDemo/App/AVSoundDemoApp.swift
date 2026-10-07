import AVSoundDemoCore
import AppKit
import SwiftUI

/// The SwiftUI app (started by `AVSoundDemoMain` unless `--self-check` is given).
struct AVSoundDemoApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        Window("AV Sound Demo", id: "main") {
            ContentView()
                .environment(delegate.model)
                .frame(minWidth: 1_080, minHeight: 700)
        }
        .defaultSize(width: 1_320, height: 860)
        .commands {
            AppCommands(model: delegate.model)
        }
    }
}

/// Owns the model, starts the bridge at launch and shuts it down before quitting.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let model = AppModel()
    private var terminationReplied = false
    private var signalSources: [any DispatchSourceSignal] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        // A SwiftPM executable has no bundle: make it a regular, frontmost app.
        NSApplication.shared.setActivationPolicy(.regular)
        NSApplication.shared.activate()
        quitOnTerminationSignals()
        model.launch()
    }

    /// SIGTERM and SIGINT (`kill`, Ctrl-C after `swift run`) quit like the Quit menu item,
    /// so the bridge gets `shutdown` and removes its temp directory.
    ///
    /// `terminate` is deferred to a run-loop timer: called directly from this main-queue
    /// handler, `.terminateLater` would wait in a nested run loop that cannot drain the
    /// main queue, so the shutdown task on the main actor would never run.
    private func quitOnTerminationSignals() {
        for signalNumber in [SIGTERM, SIGINT] {
            signal(signalNumber, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: signalNumber, queue: .main)
            source.setEventHandler {
                MainActor.assumeIsolated {
                    NSApplication.shared.perform(
                        #selector(NSApplication.terminate(_:)), with: nil, afterDelay: 0)
                }
            }
            source.resume()
            signalSources.append(source)
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard model.client != nil else { return .terminateNow }
        terminationReplied = false
        // Send `shutdown` and wait for the bridge to exit (at most a few seconds).
        Task {
            await model.shutdown()
            replyToTermination(sender)
        }
        // The deadline is a run-loop timer, not a main-actor task: it also fires when the
        // main queue cannot be drained (`terminate` called from a main-queue callout). The
        // bridge then exits by itself when its stdin closes.
        let deadline = Timer(timeInterval: 6, repeats: false) { [weak self] _ in
            MainActor.assumeIsolated { self?.replyToTermination(sender) }
        }
        RunLoop.main.add(deadline, forMode: .common)
        return .terminateLater
    }

    private func replyToTermination(_ sender: NSApplication) {
        guard !terminationReplied else { return }
        terminationReplied = true
        sender.reply(toApplicationShouldTerminate: true)
    }
}

struct AppCommands: Commands {
    let model: AppModel

    var body: some Commands {
        CommandMenu("Bridge") {
            Button("Restart Bridge") { model.restartBridge() }
                .keyboardShortcut("r", modifiers: [.command, .shift])
            Button("Stop Bridge") { model.stopBridge() }
            Divider()
            Button("Settings & About") { model.selection = .settings }
                .keyboardShortcut(",", modifiers: .command)
        }
        CommandMenu("Sound") {
            Button("Stop Playback") { model.stopPlayback() }
                .keyboardShortcut(".", modifiers: .command)
            Divider()
            ForEach(Profile.allCases) { profile in
                Button("Profile \(profile.rawValue) (\(profile.f0Hz) Hz)") { model.profile = profile }
                    .keyboardShortcut(KeyEquivalent(Character("\(Profile.allCases.firstIndex(of: profile).map { $0 + 1 } ?? 1)")), modifiers: .command)
            }
        }
        CommandGroup(after: .sidebar) {
            ForEach(Array(AppModel.Section.allCases.enumerated()), id: \.element) { index, section in
                Button(section.title) { model.selection = section }
                    .keyboardShortcut(KeyEquivalent(Character("\(index + 1)")), modifiers: [.command, .option])
            }
        }
    }
}
