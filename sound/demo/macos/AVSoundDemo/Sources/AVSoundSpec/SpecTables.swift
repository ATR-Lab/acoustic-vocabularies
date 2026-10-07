// Lookup tables of renderer spec D2 and D4, generated with exact integer arithmetic.
//
// Every table entry is a round-half-up of a transcendental value. The port computes
// each value in Q124 fixed point (error well below 2^10 Q124 units) and rounds it.
// An entry is *proven* correctly rounded when the discarded fraction is farther than
// `tieMargin` (2^20 Q124 units) from the halfway point; entries that are not are
// counted in `unprovenEntries` (expected: 0). Phase increments are additionally
// checked with exact 12th-power comparisons, so they are correctly rounded by
// construction. The spec's SHA-256 digests of the tables are the final arbiter
// (see `SpecRenderer.tableDigests()` and the tests).

import Foundation

struct SpecTables: Sendable {
    static let sineBits = 16
    static let sineSize = 1 << sineBits
    static let sineQ = 24
    static let envelopeQ = 30
    static let attackSamples = 480
    static let releaseSamples = 1440
    static let sampleRate = 48_000
    static let pitchRange = -6...6
    static let harmonics = 1...3

    /// Margin, in Q124 units, within which a rounding is treated as unproven.
    static let tieMargin: UInt128 = 1 << 20

    /// S[i] = round_half_up(2^24 sin(2 pi i / 65536)), i = 0..65535.
    let sine: [Int32]
    /// EA[k] = round_half_up(2^30 (1 - cos(pi k / 480)) / 2), k = 0..480.
    let attack: [Int32]
    /// ER[k] = round_half_up(2^30 (1 - cos(pi k / 1440)) / 2), k = 0..1440.
    let release: [Int32]
    /// INC in the spec digest order: profile P1..P3, pitch -6..+6, harmonic 1..3.
    let increments: [UInt32]
    /// Entries whose rounding the fixed-point error bound does not settle (expected 0).
    let unprovenEntries: Int
    /// Increments whose fixed-point estimate needed an exact correction (expected 0).
    let incrementCorrections: Int

    /// Process-wide tables, built once on first use (Swift globals are initialized
    /// lazily and atomically).
    static let shared = SpecTables()

    init() {
        var unproven = 0

        // Sine: quarter wave from the series, mirrored (exactly odd-symmetric).
        let quarter = Self.sineSize / 4
        var sine = [Int32](repeating: 0, count: Self.sineSize)
        for i in 0...quarter {
            let (value, nearTie) = Q124.roundShift(
                Q124.sinQuarter(i, quarter), Q124.fractionBits - Self.sineQ, margin: Self.tieMargin)
            if nearTie { unproven += 1 }
            let v = Int32(value)
            sine[i] = v
            sine[2 * quarter - i] = v
            if i > 0 {
                sine[2 * quarter + i] = -v
                sine[Self.sineSize - i] = -v
            }
        }
        sine[2 * quarter] = 0

        // Raised cosine: (1 - cos(pi k / L)) / 2 = sin^2(pi/2 * k / L).
        func raisedCosine(_ length: Int) -> [Int32] {
            (0...length).map { k in
                let s = Q124.sinQuarter(k, length)
                let (value, nearTie) = Q124.roundShift(
                    Q124.mul(s, s), Q124.fractionBits - Self.envelopeQ, margin: Self.tieMargin)
                if nearTie { unproven += 1 }
                return Int32(value)
            }
        }
        let attack = raisedCosine(Self.attackSamples)
        let release = raisedCosine(Self.releaseSamples)

        // Phase increments: round_half_up(h f0 2^(p/12) 2^32 / 48000).
        var increments: [UInt32] = []
        var corrections = 0
        for profile in SpecProfile.allCases {
            for pitch in Self.pitchRange {
                let ratio = Q124.pow2Twelfth(pitch)
                for h in Self.harmonics {
                    let hf0 = UInt64(h * profile.f0Hz)
                    let estimate = Self.incrementEstimate(hf0: hf0, ratio: ratio)
                    var n = estimate
                    while n > 1 && !Self.incrementAtLeast(n, hf0: hf0, pitch: pitch) { n -= 1 }
                    while Self.incrementAtLeast(n + 1, hf0: hf0, pitch: pitch) { n += 1 }
                    if n != estimate { corrections += 1 }
                    increments.append(UInt32(n))
                }
            }
        }

        self.sine = sine
        self.attack = attack
        self.release = release
        self.increments = increments
        self.unprovenEntries = unproven
        self.incrementCorrections = corrections
    }

    /// Fixed-point estimate of round_half_up(hf0 * ratio * 2^32 / 48000), ratio in Q124.
    private static func incrementEstimate(hf0: UInt64, ratio: UInt128) -> UInt64 {
        // P = hf0 * ratio < 2^11 * 2^125; V = P >> 8 fits 128 bits (Q116).
        let (high, low) = UInt128(hf0).multipliedFullWidth(by: ratio)
        let v = (high << 120) | (low >> 8)
        // value = V * 2^32 / (48000 * 2^116) = (V / 48000) / 2^84.
        let (rounded, _) = Q124.roundShift(v / UInt128(sampleRate), 84, margin: 0)
        return UInt64(rounded)
    }

    /// Exact test of `n - 1/2 <= hf0 * 2^(p/12) * 2^32 / 48000`, i.e. whether the
    /// correctly rounded (half up) increment is at least `n`. Both sides are raised to
    /// the 12th power so the irrational factor 2^(p/12) becomes the integer 2^p.
    static func incrementAtLeast(_ n: UInt64, hf0: UInt64, pitch: Int) -> Bool {
        precondition(n >= 1)
        let lhs = WideUInt((2 * n - 1) * UInt64(sampleRate)).power(12)
        let rhs = WideUInt(hf0 << 33).power(12)
        if pitch >= 0 {
            return lhs <= rhs.shiftedLeft(pitch)
        }
        return lhs.shiftedLeft(-pitch) <= rhs
    }

    /// INC for one partial.
    @inline(__always)
    func increment(_ profile: SpecProfile, pitch: Int, harmonic: Int) -> UInt32 {
        let profileIndex = SpecProfile.allCases.firstIndex(of: profile)!
        return increments[(profileIndex * 13 + (pitch + 6)) * 3 + (harmonic - 1)]
    }

    /// SHA-256 of each table's little-endian bytes (spec D4 digest table).
    func digests() -> [String: String] {
        func le<T: FixedWidthInteger>(_ values: [T]) -> Data {
            var data = Data(capacity: values.count * MemoryLayout<T>.size)
            for value in values {
                withUnsafeBytes(of: value.littleEndian) { data.append(contentsOf: $0) }
            }
            return data
        }
        return [
            "sine_int32le": SpecHash.sha256Hex(le(sine)),
            "attack_int32le": SpecHash.sha256Hex(le(attack)),
            "release_int32le": SpecHash.sha256Hex(le(release)),
            "increment_uint32le": SpecHash.sha256Hex(le(increments)),
        ]
    }
}
