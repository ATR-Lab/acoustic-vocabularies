import AVSoundDemoCore
import AVSoundSpec
import Foundation

/// Verified audio plus its waveform summary, ready to draw and play.
struct AudioClip: Sendable, Hashable, Identifiable {
    let audio: VerifiedAudio
    let waveform: WaveformSummary

    var id: String { audio.id }
}

/// CPU work that must never run on the main actor: audio verification (base64, two
/// SHA-256 digests, sample decoding), waveform summaries and the Swift renderer port.
///
/// These are nonisolated `async` functions, so in the Swift 6 language mode they run on
/// the global concurrent executor, not on the caller's actor (the same holds for
/// `Conformance`). Do not enable the upcoming feature `NonisolatedNonsendingByDefault`
/// without marking them `@concurrent` (Swift 6.2+).
enum Offload {
    /// Buckets of the precomputed waveform summary (the canvas scales it to its width).
    static let waveformBuckets = 1_600

    /// Verifies `payload` (file hash, canonical header, waveform hash) and summarizes it.
    static func clip(_ payload: AudioPayload) async throws -> AudioClip {
        let audio = try payload.verified()
        return AudioClip(audio: audio, waveform: WaveformSummary(audio: audio, buckets: waveformBuckets))
    }

    /// Renders with the independent Swift port (AVSoundSpec).
    static func specRender(_ recipe: Recipe, profile: Profile) async throws -> SpecRendered {
        try SpecRenderer.render(SpecRecipe(recipe), profile: SpecProfile(profile))
    }

    /// The four AVSoundSpec table digests (the first call builds the tables).
    static func tableDigests() async -> [String: String] {
        SpecRenderer.tableDigests()
    }
}

extension SpecRecipe {
    init(_ recipe: Recipe) {
        self.init(
            totalMs: recipe.totalMs, pitches: recipe.pitches, rhythmWeights: recipe.rhythmWeights,
            gapsMs: recipe.gapsMs, amplitudes: recipe.amplitudes)
    }
}

extension SpecProfile {
    init(_ profile: Profile) {
        switch profile {
        case .p1: self = .p1
        case .p2: self = .p2
        case .p3: self = .p3
        }
    }
}
