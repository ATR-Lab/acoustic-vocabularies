// Spec D2/D4 tables: digests, exactness bookkeeping and spot values.

import Foundation
import Testing

@testable import AVSoundSpec

@Suite("Renderer tables (spec D2, D4)")
struct TableTests {
    @Test("Table digests equal the four spec D4 digests")
    func digestsMatchSpec() {
        #expect(SpecRenderer.tableDigests() == [
            "sine_int32le": "7507c6a534ec1b3bb8bd7e2650f4dad6e0d04adf3d0000cbb2472985b49d2b21",
            "attack_int32le": "7fbc9783264314af81e078b6759a67170904a55f935f207f0ced7c50b3a3af34",
            "release_int32le": "2b2b6c3486da35d3a73cacb83ae1d45bdc5ce9a2d7b7eaeb6cbfe4b36858d3ec",
            "increment_uint32le": "14864688cdbb9cacbd6f633485b15c902a7416f0e38a2b8c2616c5b809bdd5f0",
        ])
    }

    @Test("Every entry's rounding is settled by the error bound; no increment needed correction")
    func everyEntryProven() {
        let t = SpecTables.shared
        #expect(t.unprovenEntries == 0)
        #expect(t.incrementCorrections == 0)
        #expect(t.sine.count == 65536)
        #expect(t.attack.count == 481)
        #expect(t.release.count == 1441)
        #expect(t.increments.count == 117)
    }

    @Test("Machin pi and series ln 2 agree with their published hex expansions")
    func constants() {
        // pi = 3.243F6A8885A308D313198A2E0370734|4A409... (hex); ln 2 = 0.B17217F7D1CF79ABC9E3B39803F2F6AF|40F3...
        let piQ124: UInt128 = 0x3243F6A8_885A308D_313198A2_E0370734
        let ln2Q124: UInt128 = 0x0B17217F_7D1CF79A_BC9E3B39_803F2F6A
        // Series truncation leaves at most a few dozen Q124 units of error; the tie
        // margin used for the tables is 2^20 units.
        let piError = Q124.pi > piQ124 ? Q124.pi - piQ124 : piQ124 - Q124.pi
        let ln2Error = Q124.ln2 > ln2Q124 ? Q124.ln2 - ln2Q124 : ln2Q124 - Q124.ln2
        print("AVSoundSpec constants: |pi error| = \(piError), |ln 2 error| = \(ln2Error) Q124 units")
        #expect(piError < 256)
        #expect(ln2Error < 256)
    }

    @Test("Spot values: sine symmetry, envelope end points, worked-example increments")
    func spotValues() {
        let t = SpecTables.shared
        #expect(t.sine[0] == 0)
        #expect(t.sine[16384] == 1 << 24)
        #expect(t.sine[32768] == 0)
        #expect(t.sine[49152] == -(1 << 24))
        for i in 1..<65536 { #expect(t.sine[i] == -t.sine[65536 - i]) }
        #expect(t.attack[0] == 0 && t.attack[480] == 1 << 30 && t.attack[479] < 1 << 30)
        #expect(t.release[0] == 0 && t.release[1] > 0 && t.release[1440] == 1 << 30)
        #expect(t.attack[240] == 1 << 29)  // sin^2(pi/4) = 1/2, exact
        #expect(t.release[480] == 1 << 28)  // sin^2(pi/6) = 1/4, exact
        // Spec section 5, P2, h = 1, pitches -3, 0, +4.
        #expect(t.increment(.p2, pitch: -3, harmonic: 1) == 33_858_962)
        #expect(t.increment(.p2, pitch: 0, harmonic: 1) == 40_265_318)
        #expect(t.increment(.p2, pitch: 4, harmonic: 1) == 50_731_122)
    }

    @Test("Exact 12th-power increment predicate brackets each table entry")
    func incrementPredicate() {
        let t = SpecTables.shared
        for profile in SpecProfile.allCases {
            for pitch in -6...6 {
                for h in 1...3 {
                    let n = UInt64(t.increment(profile, pitch: pitch, harmonic: h))
                    let hf0 = UInt64(h * profile.f0Hz)
                    #expect(SpecTables.incrementAtLeast(n, hf0: hf0, pitch: pitch))
                    #expect(!SpecTables.incrementAtLeast(n + 1, hf0: hf0, pitch: pitch))
                }
            }
        }
    }
}
