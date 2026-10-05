import AVSoundDemoCore
import AVSoundSpec
import Foundation

/// One recipe rendered by both the Python engine (through the bridge) and AVSoundSpec.
struct ConformanceRow: Identifiable, Sendable, Hashable {
    let seed: Int
    let profile: Profile
    let recipe: Recipe
    /// Verified hashes of the bridge's audio; `nil` when the motif overflowed.
    let bridgePCM: String?
    let bridgeFile: String?
    let bridgeOverflow: Bool
    let bridgePeak: Int
    let bridgeSamples: Int
    let bridgeEvents: [Int]
    let swiftPCM: String
    let swiftFile: String
    let swiftOverflow: Bool
    let swiftPeak: Int
    let swiftSamples: Int
    let swiftEvents: [Int]
    let swiftMilliseconds: Double

    var id: Int { seed }

    var pcmMatches: Bool { bridgeOverflow ? swiftOverflow : bridgePCM == swiftPCM }
    var fileMatches: Bool { bridgeOverflow ? swiftOverflow : bridgeFile == swiftFile }
    var layoutMatches: Bool {
        bridgeSamples == swiftSamples && bridgeEvents == swiftEvents && bridgePeak == swiftPeak
            && bridgeOverflow == swiftOverflow
    }
    var matches: Bool { pcmMatches && fileMatches && layoutMatches }

    /// What differs, for the self-check report.
    var differences: [String] {
        var out: [String] = []
        if !pcmMatches { out.append("pcm_sha256 \(bridgePCM ?? "null") != \(swiftPCM)") }
        if !fileMatches { out.append("file_sha256 \(bridgeFile ?? "null") != \(swiftFile)") }
        if bridgeSamples != swiftSamples { out.append("n_samples \(bridgeSamples) != \(swiftSamples)") }
        if bridgeEvents != swiftEvents { out.append("event_samples \(bridgeEvents) != \(swiftEvents)") }
        if bridgePeak != swiftPeak { out.append("peak \(bridgePeak) != \(swiftPeak)") }
        if bridgeOverflow != swiftOverflow { out.append("overflow \(bridgeOverflow) != \(swiftOverflow)") }
        return out
    }
}

/// One message of the synthetic DEMO book: the bridge's `composite_hash` against
/// `SpecComposer.compositeHash` of the AVSoundSpec atom renders.
struct CompositeRow: Identifiable, Sendable, Hashable {
    let messageID: String
    let status: String
    let isHeldout: Bool
    let bridgeHash: String
    let swiftHash: String
    let bridgeSamples: Int
    let swiftSamples: Int

    var id: String { messageID }
    var matches: Bool { bridgeHash == swiftHash && bridgeSamples == swiftSamples }
}

/// One atom of the synthetic DEMO book: the engine's waveform hash against AVSoundSpec.
struct AtomHashRow: Identifiable, Sendable, Hashable {
    let atomID: String
    let enginePCM: String
    let swiftPCM: String

    var id: String { atomID }
    var matches: Bool { enginePCM == swiftPCM }
}

enum Conformance {
    /// The profile used for the `index`-th recipe: P1, P2, P3, P1, ... when cycling.
    static func profile(index: Int, cycling: Bool, fixed: Profile) -> Profile {
        cycling ? Profile.allCases[index % Profile.allCases.count] : fixed
    }

    /// `random_recipe(seed)`, then `render` on the bridge and in AVSoundSpec.
    static func check(
        seed: Int, profile: Profile, admissibleOnly: Bool, client: BridgeClient
    ) async throws -> ConformanceRow {
        let recipe = try await client.randomRecipe(seed: seed, admissibleOnly: admissibleOnly)
        let bridge = try await client.render(recipe, profile: profile)
        var bridgePCM: String?
        var bridgeFile: String?
        if bridge.audio.hasAudio {
            let audio = try bridge.audio.verified()
            bridgePCM = audio.pcmSHA256
            bridgeFile = audio.fileSHA256
        }
        let clock = ContinuousClock()
        let start = clock.now
        let spec = try SpecRenderer.render(SpecRecipe(recipe), profile: SpecProfile(profile))
        let elapsed = start.duration(to: clock.now)
        return ConformanceRow(
            seed: seed, profile: profile, recipe: recipe,
            bridgePCM: bridgePCM, bridgeFile: bridgeFile, bridgeOverflow: bridge.overflow,
            bridgePeak: bridge.peak, bridgeSamples: bridge.nSamples, bridgeEvents: bridge.eventSamples,
            swiftPCM: spec.pcmSHA256, swiftFile: spec.fileSHA256, swiftOverflow: spec.overflow,
            swiftPeak: spec.peak, swiftSamples: spec.nSamples, swiftEvents: spec.eventSamples,
            swiftMilliseconds: Double(elapsed.components.attoseconds) / 1e15 + Double(elapsed.components.seconds) * 1e3)
    }

    /// AVSoundSpec renders of every atom of a synthetic book (int16 LE PCM by atom ID).
    static func swiftAtoms(_ book: SyntheticBook, profile: Profile) async throws -> [String: Data] {
        var out: [String: Data] = [:]
        for atom in book.atoms {
            out[atom.atomID] = try SpecRenderer.render(SpecRecipe(atom.recipe), profile: SpecProfile(profile)).pcm
        }
        return out
    }

    static func swiftComposite(action: Data, referent: Data) async -> String {
        SpecComposer.compositeHash(actionPCM: action, referentPCM: referent)
    }

    /// Samples of `action + gap + referent`.
    static func compositeSamples(action: Data, referent: Data) -> Int {
        (action.count + referent.count) / 2 + SpecComposer.gapSamples
    }
}

/// Published reference values of the renderer spec.
enum SpecReference {
    /// The table digests of renderer spec D4.
    static let tableDigests: [String: String] = [
        "sine_int32le": "7507c6a534ec1b3bb8bd7e2650f4dad6e0d04adf3d0000cbb2472985b49d2b21",
        "attack_int32le": "7fbc9783264314af81e078b6759a67170904a55f935f207f0ced7c50b3a3af34",
        "release_int32le": "2b2b6c3486da35d3a73cacb83ae1d45bdc5ce9a2d7b7eaeb6cbfe4b36858d3ec",
        "increment_uint32le": "14864688cdbb9cacbd6f633485b15c902a7416f0e38a2b8c2616c5b809bdd5f0",
    ]
    static let tableOrder = ["sine_int32le", "attack_int32le", "release_int32le", "increment_uint32le"]
}
