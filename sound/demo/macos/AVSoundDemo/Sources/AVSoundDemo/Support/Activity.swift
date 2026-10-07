import Foundation
import Observation

/// Tracks the running operations of one section and its last error.
///
/// `run` starts a task on the main actor; the bridge calls inside it suspend, so the UI
/// never waits. Keys name operations so each button can show its own spinner.
@MainActor
@Observable
final class Activity {
    private(set) var running: [String: Int] = [:]
    var error: String?

    var isBusy: Bool { !running.isEmpty }

    func isRunning(_ key: String) -> Bool { running[key, default: 0] > 0 }

    @discardableResult
    func run(_ key: String, _ body: @escaping @MainActor () async throws -> Void) -> Task<Void, Never> {
        running[key, default: 0] += 1
        error = nil
        return Task {
            defer { finish(key) }
            do {
                try await body()
            } catch is CancellationError {
                // Cancelled on purpose: nothing to report.
            } catch {
                self.error = userMessage(error)
            }
        }
    }

    private func finish(_ key: String) {
        let count = running[key, default: 0] - 1
        running[key] = count > 0 ? count : nil
    }
}
