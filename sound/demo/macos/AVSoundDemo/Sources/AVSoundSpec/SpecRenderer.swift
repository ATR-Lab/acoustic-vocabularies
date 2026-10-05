// Integer-only motif renderer, ported from sound/docs/renderer-spec.md (D1-D7).
// Bit-exact with the Python reference `av_sound.renderer.render`.

import Foundation

public enum SpecRenderer {
    /// Renderer version this port implements (spec D10).
    public static let rendererVersion: String = "0.1.0"

    static let samplesPerMs = 48
    static let rmsTarget: Int64 = 7336
    static let fullScale: Int64 = 32767
    static let minEventSamples = 60 * samplesPerMs
    static let workShift: Int64 = 40
    static let gainFractionBits = 32

    static let totalMsDomain: Set<Int> = [450, 600, 750, 900]
    static let pitchDomain = -6...6
    static let rhythmWeightDomain = 1...4
    static let gapMsDomain: Set<Int> = [20, 40, 60]

    /// Render one recipe for one profile. Pure function: same input, same bytes.
    /// Throws `SpecError` for a recipe outside the domain of renderer spec section 1.
    public static func render(_ recipe: SpecRecipe, profile: SpecProfile) throws -> SpecRendered {
        try validate(recipe)
        let layout = timing(recipe)
        let steps = try amplitudeSteps(recipe.amplitudes)
        var x = [Int64](repeating: 0, count: layout.nSamples)
        let tables = SpecTables.shared
        for j in 0..<3 {
            synthesizeEvent(
                into: &x, at: layout.eventOnsets[j], count: layout.eventSamples[j],
                increments: (1...3).map { tables.increment(profile, pitch: recipe.pitches[j], harmonic: $0) },
                amplitudeStep: Int64(steps[j]), tables: tables)
        }
        let out = try normalize(x)
        return SpecRendered(
            pcm: out.overflow ? Data() : pcmBytes(out.samples),
            samples: out.samples,
            nSamples: layout.nSamples,
            eventSamples: layout.eventSamples,
            eventOnsets: layout.eventOnsets,
            peak: Int(out.peak),
            overflow: out.overflow,
            shortEvent: layout.eventSamples.min()! < minEventSamples)
    }

    /// SHA-256 of each generated table (keys `sine_int32le`, `attack_int32le`,
    /// `release_int32le`, `increment_uint32le`), to compare with the spec D4 digests.
    public static func tableDigests() -> [String: String] {
        SpecTables.shared.digests()
    }

    // MARK: - Domain (renderer spec section 1)

    static func validate(_ r: SpecRecipe) throws {
        guard totalMsDomain.contains(r.totalMs) else {
            throw SpecError("total_ms: \(r.totalMs) is not one of [450, 600, 750, 900]")
        }
        guard r.pitches.count == 3 else { throw SpecError("pitches: expected 3 values, got \(r.pitches.count)") }
        for (i, p) in r.pitches.enumerated() where !pitchDomain.contains(p) {
            throw SpecError("pitches[\(i)]: \(p) is not in -6...6")
        }
        guard r.rhythmWeights.count == 3 else {
            throw SpecError("rhythm_weights: expected 3 values, got \(r.rhythmWeights.count)")
        }
        for (i, w) in r.rhythmWeights.enumerated() where !rhythmWeightDomain.contains(w) {
            throw SpecError("rhythm_weights[\(i)]: \(w) is not in 1...4")
        }
        guard r.gapsMs.count == 2 else { throw SpecError("gaps_ms: expected 2 values, got \(r.gapsMs.count)") }
        for (i, g) in r.gapsMs.enumerated() where !gapMsDomain.contains(g) {
            throw SpecError("gaps_ms[\(i)]: \(g) is not one of [20, 40, 60]")
        }
        guard r.amplitudes.count == 3 else {
            throw SpecError("amplitudes: expected 3 values, got \(r.amplitudes.count)")
        }
    }

    // MARK: - D1 timing

    struct Timing: Equatable {
        var eventSamples: [Int]
        var eventOnsets: [Int]
        var gapSamples: [Int]
        var nSamples: Int
    }

    /// Events 1 and 2 rounded half up, event 3 takes the remainder (spec D1).
    static func timing(_ r: SpecRecipe) -> Timing {
        let d = (r.totalMs - r.gapsMs[0] - r.gapsMs[1]) * samplesPerMs
        let w = r.rhythmWeights[0] + r.rhythmWeights[1] + r.rhythmWeights[2]
        let n1 = (2 * d * r.rhythmWeights[0] + w) / (2 * w)
        let n2 = (2 * d * r.rhythmWeights[1] + w) / (2 * w)
        let n3 = d - n1 - n2
        let g1 = r.gapsMs[0] * samplesPerMs
        let g2 = r.gapsMs[1] * samplesPerMs
        return Timing(
            eventSamples: [n1, n2, n3],
            eventOnsets: [0, n1 + g1, n1 + g1 + n2 + g2],
            gapSamples: [g1, g2],
            nSamples: r.totalMs * samplesPerMs)
    }

    // MARK: - D5 amplitudes

    /// k = 5a by lookup (0.6 -> 3, 0.8 -> 4, 1.0 -> 5), divided by gcd(k1, k2, k3).
    static func amplitudeSteps(_ amplitudes: [Double]) throws -> [Int] {
        let k = try amplitudes.enumerated().map { i, a -> Int in
            switch a {
            case 0.6: return 3
            case 0.8: return 4
            case 1.0: return 5
            default: throw SpecError("amplitudes[\(i)]: \(a) is not one of [0.6, 0.8, 1.0]")
            }
        }
        func gcd(_ a: Int, _ b: Int) -> Int { b == 0 ? a : gcd(b, a % b) }
        let common = gcd(gcd(k[0], k[1]), k[2])
        return k.map { $0 / common }
    }

    // MARK: - D2-D4 synthesis

    /// One enveloped event at working scale, written at `onset` (spec D2, D3, D4).
    /// Partials start at phase 0; `phase += INC` with wrap-around equals
    /// `(INC * i) mod 2^32`.
    static func synthesizeEvent(
        into x: inout [Int64], at onset: Int, count n: Int, increments inc: [UInt32],
        amplitudeStep k: Int64, tables: SpecTables
    ) {
        let attackLast = SpecTables.attackSamples
        let releaseLast = SpecTables.releaseSamples
        let indexShift = UInt32(32 - SpecTables.sineBits)
        let round: Int64 = 1 << (workShift - 1)
        let (inc1, inc2, inc3) = (inc[0], inc[1], inc[2])
        tables.sine.withUnsafeBufferPointer { s in
            tables.attack.withUnsafeBufferPointer { ea in
                tables.release.withUnsafeBufferPointer { er in
                    x.withUnsafeMutableBufferPointer { out in
                        var p1: UInt32 = 0
                        var p2: UInt32 = 0
                        var p3: UInt32 = 0
                        for i in 0..<n {
                            let s1 = Int64(s[Int(p1 >> indexShift)])
                            let s2 = Int64(s[Int(p2 >> indexShift)])
                            let s3 = Int64(s[Int(p3 >> indexShift)])
                            let mix = 20 * s1 + 3 * s2 + s3
                            let env = Int64(min(ea[min(i, attackLast)], er[min(n - 1 - i, releaseLast)]))
                            let e = mix * k * env
                            out[onset + i] = (e + round) >> workShift
                            p1 &+= inc1
                            p2 &+= inc2
                            p3 &+= inc3
                        }
                    }
                }
            }
        }
    }

    // MARK: - D6/D7 normalization

    struct Normalized {
        var samples: [Int64]
        var gainQ32: Int64
        var sumSquares: UInt128
        var peak: Int64
        var overflow: Bool
    }

    /// RMS normalization with an integer Q32 gain (spec D6). Never limits (spec D7).
    static func normalize(_ x: [Int64], targetRMS: Int64 = rmsTarget) throws -> Normalized {
        guard !x.isEmpty else { throw SpecError("cannot normalize an empty signal") }
        var sumSquares: UInt128 = 0
        for v in x {
            let m = UInt128(v.magnitude)
            sumSquares += m * m
        }
        guard sumSquares > 0 else { throw SpecError("cannot normalize an all-zero signal") }
        let target = UInt128(targetRMS.magnitude)
        // T^2 N < 2^60 keeps the numerator below 2^124 and, because |x| <= sqrt(S) and
        // G <= T sqrt(N / S) 2^32, every |x * G| <= T sqrt(N) 2^32 < 2^62 (fits Int64).
        // The recipe domain has T^2 N < 2^42.
        let energy = target * target * UInt128(x.count)
        guard energy < UInt128(1) << 60 else { throw SpecError("normalization target too large") }
        let gain = isqrt((energy << (2 * gainFractionBits)) / sumSquares)
        let g = Int64(gain)
        let round: Int64 = 1 << (gainFractionBits - 1)
        var y = [Int64](repeating: 0, count: x.count)
        var peak: Int64 = 0
        for i in x.indices {
            let v = (x[i] * g + round) >> Int64(gainFractionBits)
            y[i] = v
            peak = max(peak, Swift.abs(v))
        }
        return Normalized(
            samples: y, gainQ32: g, sumSquares: sumSquares, peak: peak, overflow: overflows(y))
    }

    /// True if any sample is outside +/-32,767 (-32,768 counts as overflow; spec D7).
    static func overflows(_ y: [Int64]) -> Bool {
        y.contains { $0 > fullScale || $0 < -fullScale }
    }

    /// int16 little-endian bytes. Callers guarantee no overflow.
    static func pcmBytes(_ y: [Int64]) -> Data {
        var data = Data(count: 2 * y.count)
        data.withUnsafeMutableBytes { raw in
            for (i, v) in y.enumerated() {
                let u = UInt16(bitPattern: Int16(v))
                raw[2 * i] = UInt8(truncatingIfNeeded: u)
                raw[2 * i + 1] = UInt8(truncatingIfNeeded: u >> 8)
            }
        }
        return data
    }
}
