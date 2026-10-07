import Foundation

/// Entry point: `--self-check` runs the headless check (no window, no AppKit); anything
/// else starts the SwiftUI app.
@main
enum AVSoundDemoMain {
    @MainActor
    static func main() {
        let arguments = Array(CommandLine.arguments.dropFirst())
        if arguments.contains(SelfCheck.flag) {
            let options = SelfCheck.Options(arguments: arguments)
            Task.detached {
                let status = await SelfCheck.run(options)
                exit(status)
            }
            dispatchMain()
        }
        AVSoundDemoApp.main()
    }
}
