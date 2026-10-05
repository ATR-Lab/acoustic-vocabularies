// sound/testvectors/composition/vectors.json: atoms rendered per book profile, and
// the composite hash of every message and synthetic pattern.

import Foundation
import Testing

@testable import AVSoundSpec

@Suite("Composition reference vectors (spec D9)")
struct CompositionVectorTests {
    static func file() throws -> CompositionVectorFile {
        try RepoFixtures.load(CompositionVectorFile.self, "sound/testvectors/composition/vectors.json")
    }

    @Test("All atoms and message composite hashes reproduce")
    func booksReproduce() throws {
        let file = try Self.file()
        #expect(file.rendererVersion == SpecRenderer.rendererVersion)
        #expect(file.gapSamples == SpecComposer.gapSamples)
        #expect(!file.books.isEmpty)
        var atomCount = 0
        var messageCount = 0
        for book in file.books {
            let profile = try #require(SpecProfile(rawValue: book.profile), "\(book.bookId)")
            var pcm: [String: Data] = [:]
            for atom in book.atoms {
                let label = "\(book.bookId)/\(atom.atomId)"
                let r = try SpecRenderer.render(atom.recipe, profile: profile)
                #expect(!r.overflow, "\(label)")
                #expect(r.nSamples == atom.nSamples, "\(label)")
                #expect(r.pcmSHA256 == atom.pcmSha256, "\(label)")
                #expect(r.fileSHA256 == atom.fileSha256, "\(label)")
                pcm[atom.atomId] = r.pcm
                atomCount += 1
            }
            for message in book.messages {
                let label = "\(book.bookId)/\(message.messageId)"
                let action = try #require(pcm[message.actionId], "\(label): unknown action")
                let referent = try #require(pcm[message.referentId], "\(label): unknown referent")
                let n = (action.count + referent.count) / 2 + SpecComposer.gapSamples
                #expect(n == message.nSamples, "\(label)")
                #expect(n == message.durationMs * 48, "\(label)")
                #expect(
                    SpecComposer.compositeHash(actionPCM: action, referentPCM: referent)
                        == message.compositeSha256, "\(label)")
                messageCount += 1
            }
        }
        #expect(atomCount == 48)
        #expect(messageCount == 96)
    }

    @Test("Synthetic PCM patterns reproduce the composite hash")
    func patternsReproduce() throws {
        let file = try Self.file()
        #expect(file.patterns.count == 4)
        for p in file.patterns {
            let action = p.action.pcm
            let referent = p.referent.pcm
            #expect(SpecHash.sha256Hex(action) == p.actionPcmSha256, "\(p.name)")
            #expect(SpecHash.sha256Hex(referent) == p.referentPcmSha256, "\(p.name)")
            #expect(p.action.nSamples + SpecComposer.gapSamples + p.referent.nSamples == p.nSamples)
            #expect(
                SpecComposer.compositeHash(actionPCM: action, referentPCM: referent) == p.compositeSha256,
                "\(p.name)")
            var joined = action
            joined.append(Data(count: 2 * SpecComposer.gapSamples))
            joined.append(referent)
            #expect(SpecHash.sha256Hex(joined) == p.compositeSha256, "\(p.name)")
        }
    }
}
