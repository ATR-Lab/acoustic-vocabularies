import AVSoundDemoCore
import AVSoundSpec
import Foundation

/// Text formatting shared by the views and the headless self-check.
enum Fmt {
    /// Samples per millisecond at the canonical 48 kHz.
    static let samplesPerMs = 48
    /// The 60 ms minimum event length of the renderer spec (D1).
    static let minEventSamples = 2_880

    /// `28,800`.
    static func int(_ value: Int) -> String { value.formatted(.number) }

    /// `+4`, `0`, `−3` (with a true minus sign).
    static func signed(_ value: Int) -> String {
        value > 0 ? "+\(value)" : value < 0 ? "\u{2212}\(-value)" : "0"
    }

    /// Milliseconds for a sample count: `180 ms`, `36.5 ms`.
    static func ms(samples: Int) -> String {
        let value = Double(samples) / Double(samplesPerMs)
        return value == value.rounded() ? "\(Int(value)) ms" : String(format: "%.1f ms", value)
    }

    static func number(_ value: Double, digits: Int) -> String {
        value.formatted(.number.precision(.fractionLength(digits)))
    }

    /// `−7.47 dBFS`, or a dash for silence.
    static func dbfs(_ value: Double?) -> String {
        guard let value, value.isFinite else { return "\u{2014}" }
        return String(format: "%.2f dBFS", value).replacingOccurrences(of: "-", with: "\u{2212}")
    }

    /// RMS (in int16 LSB) as dBFS.
    static func rmsDbfs(_ rms: Double) -> Double? {
        rms > 0 ? 20 * log10(rms / 32_768) : nil
    }

    /// The first `length` hex digits of a hash, with an ellipsis.
    static func shortHash(_ hash: String?, length: Int = 12) -> String {
        guard let hash else { return "\u{2014}" }
        return hash.count > length ? String(hash.prefix(length)) + "\u{2026}" : hash
    }

    /// `1.0`, `0.6`, `0.8`: the amplitude as the canonical JSON writes it.
    static func amplitude(_ value: Double) -> String { JSONValue.double(value).compactString }

    static func bytes(_ count: Int) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(count), countStyle: .file)
    }

    static func yesNo(_ value: Bool) -> String { value ? "Yes" : "No" }

    static func seconds(_ value: Double) -> String { String(format: "%.3f s", value) }
}

extension Recipe {
    /// `600 ms · −3 0 +4 · 2:1:3 · 40/20 · 1.0 0.6 0.8`.
    var summary: String {
        let pitchText = pitches.map(Fmt.signed).joined(separator: " ")
        let weights = rhythmWeights.map(String.init).joined(separator: ":")
        let gaps = gapsMs.map(String.init).joined(separator: "/")
        let amps = amplitudes.map(Fmt.amplitude).joined(separator: " ")
        return "\(totalMs) ms \u{00B7} \(pitchText) \u{00B7} \(weights) \u{00B7} \(gaps) \u{00B7} \(amps)"
    }
}

/// Exact engine numbers (`"5/6"`, `"0.1"`, `"0"`) as floats for display.
enum ExactNumber {
    static func double(_ text: String) -> Double? {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        if let slash = trimmed.firstIndex(of: "/") {
            guard let p = Double(trimmed[..<slash]), let q = Double(trimmed[trimmed.index(after: slash)...]), q != 0
            else { return nil }
            return p / q
        }
        return Double(trimmed)
    }
}

/// One readable line for any error the app shows.
func userMessage(_ error: any Error) -> String {
    switch error {
    case let bridge as BridgeError: bridge.message
    case let audio as AudioVerificationError: "Audio refused: \(audio.message)"
    case let spec as SpecError: "AVSoundSpec: \(spec.message)"
    case let setup as BridgeSetupError: setup.message
    case let app as AppError: app.message
    default: error.localizedDescription
    }
}

/// Failures detected by the app itself.
struct AppError: Error, Sendable, LocalizedError {
    let message: String

    init(_ message: String) { self.message = message }

    var errorDescription: String? { message }
}

extension String {
    var trimmed: String { trimmingCharacters(in: .whitespacesAndNewlines) }

    /// `nil` when empty after trimming.
    var nonEmpty: String? {
        let value = trimmed
        return value.isEmpty ? nil : value
    }

    /// `~` and `~/...` expanded with the home directory.
    var expandingTilde: String { (self as NSString).expandingTildeInPath }
}

extension BridgeError {
    /// The engine's own message of an `.engine` error (without type and code).
    var engineMessage: String {
        if case .engine(_, _, let message, _) = self { return message }
        return message
    }

    /// `OverwriteRejected`, `StoreError E_LABEL`, ...
    var engineTitle: String? {
        guard let type = engineType else { return nil }
        return engineCode.map { "\(type) \($0)" } ?? type
    }
}
