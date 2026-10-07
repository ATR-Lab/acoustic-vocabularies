// Recipe-independent checks: normalizer overflow (D6/D7), isqrt, WAV header (D8),
// timing (D1), domain validation and render time.

import Foundation
import Testing

@testable import AVSoundSpec

@Suite("Normalizer, WAV and domain")
struct NormalizerAndFormatTests {
    @Test("A single impulse in a long signal overflows after normalization (D7)")
    func impulseOverflows() throws {
        var x = [Int64](repeating: 0, count: 43_200)
        x[100] = 1
        let out = try SpecRenderer.normalize(x)
        #expect(out.overflow)
        #expect(out.sumSquares == 1)
        // G = isqrt(7336^2 * 43200 * 2^64) = 6,548,789,897,199,997 (Python math.isqrt),
        // y = floor((G + 2^31) / 2^32) = 1,524,759 ~ 7336 * sqrt(43200).
        #expect(out.gainQ32 == 6_548_789_897_199_997)
        #expect(out.samples[100] == 1_524_759)
        #expect(out.peak == 1_524_759)
    }

    @Test("A full-duty constant signal does not overflow and lands on the target")
    func constantSignal() throws {
        let x = [Int64](repeating: -3, count: 48_000)
        let out = try SpecRenderer.normalize(x)
        #expect(!out.overflow)
        #expect(out.samples.allSatisfy { $0 == -7336 })
        #expect(out.peak == 7336)
    }

    @Test("-32768 counts as overflow; +/-32767 does not")
    func overflowBoundary() {
        #expect(SpecRenderer.overflows([0, -32768]))
        #expect(SpecRenderer.overflows([32768]))
        #expect(!SpecRenderer.overflows([32767, -32767, 0]))
    }

    @Test("All-zero and empty signals are rejected")
    func zeroSignal() {
        #expect(throws: SpecError.self) { try SpecRenderer.normalize([0, 0, 0]) }
        #expect(throws: SpecError.self) { try SpecRenderer.normalize([]) }
    }

    @Test("isqrt is the exact floor square root")
    func isqrtExact() {
        var rng = SplitMix64(seed: 0x5EED_A5A5)
        var cases: [UInt128] = [0, 1, 2, 3, 4, 15, 16, 17, UInt128(UInt64.max), UInt128.max, (UInt128(1) << 106) - 1]
        for _ in 0..<2000 {
            cases.append(UInt128(rng.next() as UInt64) << 64 | UInt128(rng.next() as UInt64))
            cases.append(UInt128(rng.next() as UInt64) >> (rng.next() as UInt64 % 64))
        }
        for n in cases {
            let r = isqrt(n)
            let square = r.multipliedFullWidth(by: r)
            #expect(square.high == 0 && square.low <= n, "\(n)")
            let next = (r + 1).multipliedFullWidth(by: r + 1)
            #expect(next.high > 0 || next.low > n, "\(n)")
        }
    }

    @Test("Canonical WAV header layout (D8)")
    func wavHeader() {
        let pcm = Data([1, 0, 0xFF, 0xFF, 0x00, 0x80])
        let wav = SpecWAV.canonicalBytes(pcm: pcm)
        let expected: [UInt8] = [
            0x52, 0x49, 0x46, 0x46, 42, 0, 0, 0, 0x57, 0x41, 0x56, 0x45,
            0x66, 0x6D, 0x74, 0x20, 16, 0, 0, 0, 1, 0, 1, 0,
            0x80, 0xBB, 0, 0, 0x00, 0x77, 0x01, 0, 2, 0, 16, 0,
            0x64, 0x61, 0x74, 0x61, 6, 0, 0, 0,
        ]
        #expect(Array(wav.prefix(44)) == expected)
        #expect(wav.suffix(6) == pcm)
        #expect(SpecHash.sha256Hex(Data()) == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
    }

    @Test("D1 timing over all 2,304 structures: exact length, 2,097 admissible")
    func timingSweep() {
        var admissible = 0
        var shortestAdmissible = Int.max
        var longestRejected = 0
        for t in [450, 600, 750, 900] {
            for w1 in 1...4 { for w2 in 1...4 { for w3 in 1...4 {
                for g1 in [20, 40, 60] { for g2 in [20, 40, 60] {
                    let recipe = SpecRecipe(
                        totalMs: t, pitches: [0, 0, 0], rhythmWeights: [w1, w2, w3], gapsMs: [g1, g2],
                        amplitudes: [1.0, 1.0, 1.0])
                    let timing = SpecRenderer.timing(recipe)
                    #expect(timing.eventSamples.reduce(0, +) + (g1 + g2) * 48 == t * 48)
                    let shortest = timing.eventSamples.min()!
                    if shortest >= 2880 {
                        admissible += 1
                        shortestAdmissible = min(shortestAdmissible, shortest)
                    } else {
                        longestRejected = max(longestRejected, shortest)
                    }
                }}
            }}}
        }
        #expect(admissible == 2097)
        #expect(shortestAdmissible == 2880)
        #expect(longestRejected == 2812)
    }

    @Test("Out-of-domain recipes are rejected, never repaired")
    func domainValidation() {
        let good = RendererVectorTests.workedExample
        var bad: [SpecRecipe] = []
        var r = good; r.totalMs = 500; bad.append(r)
        r = good; r.pitches = [0, 7, 0]; bad.append(r)
        r = good; r.pitches = [0, 0]; bad.append(r)
        r = good; r.rhythmWeights = [0, 1, 1]; bad.append(r)
        r = good; r.gapsMs = [30, 20]; bad.append(r)
        r = good; r.gapsMs = [20]; bad.append(r)
        r = good; r.amplitudes = [0.7, 0.6, 0.8]; bad.append(r)
        r = good; r.amplitudes = [1.0, 0.6]; bad.append(r)
        for recipe in bad {
            #expect(throws: SpecError.self) { try SpecRenderer.render(recipe, profile: .p1) }
        }
        #expect(throws: Never.self) { try SpecRenderer.render(good, profile: .p1) }
    }

    @Test("Recipe JSON uses the snake_case keys of spec section 4")
    func recipeCoding() throws {
        let json = #"{"amplitudes":[1.0,0.6,0.8],"gaps_ms":[40,20],"pitches":[-3,0,4],"rhythm_weights":[2,1,3],"total_ms":600}"#
        let decoded = try JSONDecoder().decode(SpecRecipe.self, from: Data(json.utf8))
        #expect(decoded == RendererVectorTests.workedExample)
        let encoder = JSONEncoder()
        encoder.outputFormatting = .sortedKeys
        let roundTrip = try JSONDecoder().decode(SpecRecipe.self, from: encoder.encode(decoded))
        #expect(roundTrip == decoded)
    }

    @Test("Rendering a 900 ms motif is fast once tables exist")
    func renderTime() throws {
        _ = SpecRenderer.tableDigests()  // build tables outside the timed region
        let recipe = SpecRecipe(
            totalMs: 900, pitches: [6, -6, 3], rhythmWeights: [4, 1, 3], gapsMs: [20, 60],
            amplitudes: [1.0, 0.6, 0.8])
        let clock = ContinuousClock()
        let runs = 20
        let elapsed = try clock.measure {
            for _ in 0..<runs { _ = try SpecRenderer.render(recipe, profile: .p3) }
        }
        let perRender = elapsed / runs
        print("AVSoundSpec render 900 ms motif: \(perRender) per render")
        // Lenient bound so debug builds pass; release target is well under 50 ms.
        #expect(perRender < .milliseconds(500))
    }
}

/// Deterministic generator so test inputs are reproducible.
struct SplitMix64: RandomNumberGenerator {
    var state: UInt64

    init(seed: UInt64) { state = seed }

    mutating func next() -> UInt64 {
        state &+= 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }
}
