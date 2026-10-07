// Spec section 5 worked example and sound/testvectors/renderer/vectors.json.

import Foundation
import Testing

@testable import AVSoundSpec

@Suite("Renderer reference vectors (spec D1-D9)")
struct RendererVectorTests {
    static let workedExample = SpecRecipe(
        totalMs: 600, pitches: [-3, 0, 4], rhythmWeights: [2, 1, 3], gapsMs: [40, 20],
        amplitudes: [1.0, 0.6, 0.8])

    @Test("Spec section 5 worked example (P2)")
    func workedExample() throws {
        let r = try SpecRenderer.render(Self.workedExample, profile: .p2)
        #expect(r.nSamples == 28_800)
        #expect(r.eventSamples == [8640, 4320, 12960])
        #expect(r.eventOnsets == [0, 10_560, 15_840])
        #expect(r.peak == 13_865)
        #expect(!r.overflow)
        #expect(!r.shortEvent)
        #expect(r.pcm.count == 2 * 28_800)
        #expect(r.samples.count == 28_800)
        #expect(r.pcmSHA256 == "4c0467de354c076c0b30bc9af794e31384afdf161af24605fb621cc455c35d87")
        #expect(r.fileSHA256 == "32ade67b9c996e11deb3e5c1ee4b51381c0d49c6e22b4bbce9095980e119fca8")
        #expect(SpecRenderer.timing(Self.workedExample).gapSamples == [1920, 960])
        #expect(try SpecRenderer.amplitudeSteps([1.0, 0.6, 0.8]) == [5, 3, 4])
        // RMS is 7,336.0 LSB: sum(y^2) / N rounds to 7336^2.
        let sumSquares = r.samples.reduce(Int64(0)) { $0 + $1 * $1 }
        #expect(((sumSquares + 14_400) / 28_800 - 7336 * 7336).magnitude < 7336)
    }

    @Test("All renderer vectors reproduce")
    func allVectors() throws {
        let file = try RepoFixtures.load(RendererVectorFile.self, "sound/testvectors/renderer/vectors.json")
        #expect(file.rendererVersion == SpecRenderer.rendererVersion)
        #expect(file.vectors.count == 21)
        for v in file.vectors {
            let label = "\(v.name) \(v.profile)"
            let profile = try #require(SpecProfile(rawValue: v.profile), "\(label)")
            let r = try SpecRenderer.render(v.recipe, profile: profile)
            #expect(r.nSamples == v.nSamples, "\(label)")
            #expect(r.eventSamples == v.eventSamples, "\(label)")
            #expect(r.eventOnsets == v.eventOnsets, "\(label)")
            #expect(r.peak == v.peak, "\(label)")
            #expect(r.overflow == v.overflow, "\(label)")
            #expect(r.shortEvent == v.shortEvent, "\(label)")
            if let pcm = v.pcmSha256 { #expect(r.pcmSHA256 == pcm, "\(label)") }
            if let file = v.fileSha256 { #expect(r.fileSHA256 == file, "\(label)") }
            #expect(v.pcmSha256 != nil || v.overflow, "\(label): hash missing")
        }
    }

    @Test("Uniform amplitude triples render to the same bytes (spec D5)")
    func uniformAmplitudes() throws {
        var hashes: Set<String> = []
        for a in [0.6, 0.8, 1.0] {
            var recipe = Self.workedExample
            recipe.amplitudes = [a, a, a]
            hashes.insert(try SpecRenderer.render(recipe, profile: .p1).pcmSHA256)
        }
        #expect(hashes.count == 1)
    }
}
