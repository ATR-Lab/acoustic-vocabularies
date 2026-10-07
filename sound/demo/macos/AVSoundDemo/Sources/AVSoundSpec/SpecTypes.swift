// Public value types of the independent Swift port of the renderer spec
// (sound/docs/renderer-spec.md, section 4 and D1-D9).

import Foundation

/// Render profile (renderer spec section 1). The profile is not part of the recipe.
public enum SpecProfile: String, CaseIterable, Sendable {
    case p1 = "P1"
    case p2 = "P2"
    case p3 = "P3"

    /// Base frequency in Hz (300, 450 or 675).
    public var f0Hz: Int {
        switch self {
        case .p1: 300
        case .p2: 450
        case .p3: 675
        }
    }
}

/// One 3-event motif recipe (renderer spec section 4). JSON keys are `total_ms`,
/// `pitches`, `rhythm_weights`, `gaps_ms` and `amplitudes`.
public struct SpecRecipe: Hashable, Sendable, Codable {
    public var totalMs: Int
    public var pitches: [Int]
    public var rhythmWeights: [Int]
    public var gapsMs: [Int]
    public var amplitudes: [Double]

    public init(totalMs: Int, pitches: [Int], rhythmWeights: [Int], gapsMs: [Int], amplitudes: [Double]) {
        self.totalMs = totalMs
        self.pitches = pitches
        self.rhythmWeights = rhythmWeights
        self.gapsMs = gapsMs
        self.amplitudes = amplitudes
    }

    enum CodingKeys: String, CodingKey {
        case totalMs = "total_ms"
        case pitches
        case rhythmWeights = "rhythm_weights"
        case gapsMs = "gaps_ms"
        case amplitudes
    }
}

/// A recipe the port refuses to render (wrong shape or outside the recipe domain).
public struct SpecError: Error, Sendable, CustomStringConvertible {
    public let message: String

    public init(_ message: String) {
        self.message = message
    }

    public var description: String { message }
}

/// Result of `SpecRenderer.render` (renderer spec D1-D9).
public struct SpecRendered: Sendable {
    /// int16 little-endian samples. Empty when `overflow` is true (spec D7: no PCM bytes).
    public let pcm: Data
    /// Normalized output samples `y` (spec D6) before conversion to int16.
    public let samples: [Int64]
    /// Always `total_ms * 48`.
    public let nSamples: Int
    /// Per-event sample counts (spec D1).
    public let eventSamples: [Int]
    /// Per-event onset sample indices (spec D1).
    public let eventOnsets: [Int]
    /// Largest `|y|` over the motif.
    public let peak: Int
    /// Any `|y| > 32767` (spec D7; -32768 also counts).
    public let overflow: Bool
    /// Any event shorter than 2,880 samples (spec D1, 60 ms rule).
    public let shortEvent: Bool

    /// SHA-256 of `pcm`, lowercase hex (spec D9). For an overflowed motif this is the
    /// digest of the empty byte string, because no PCM exists.
    public var pcmSHA256: String { SpecHash.sha256Hex(pcm) }

    /// SHA-256 of the canonical WAV file (spec D9).
    public var fileSHA256: String { SpecHash.sha256Hex(wavBytes) }

    /// Canonical 44-byte header plus `pcm` (spec D8).
    public var wavBytes: Data { SpecWAV.canonicalBytes(pcm: pcm) }
}
