import Foundation
import Testing

@testable import AVSoundDemoCore

/// End-to-end checks against the real Python bridge. Opt-in, because they need uv and a
/// prepared engine environment: run with `AV_SOUND_BRIDGE_TESTS=1 swift test ...`.
enum LiveBridge {
    static let repoRoot = RepoFiles.soundDirectory.deletingLastPathComponent()

    static var configuration: ProcessBridgeTransport.Configuration? {
        guard ProcessInfo.processInfo.environment["AV_SOUND_BRIDGE_TESTS"] == "1",
            RepoLocator.isValidRepo(repoRoot),
            let uv = UVLocator.locate()
        else { return nil }
        return .bridge(uv: uv, repoRoot: repoRoot)
    }
}

@Suite("Live bridge (AV_SOUND_BRIDGE_TESTS=1)", .enabled(if: LiveBridge.configuration != nil), .serialized)
struct LiveBridgeTests {
    @Test func endToEnd() async throws {
        let configuration = try #require(LiveBridge.configuration)
        let client = BridgeClient(configuration: configuration)
        let hello = try await client.start()
        #expect(hello.bridgeVersion == BridgeClient.supportedBridgeVersion)
        #expect(hello.sampleRate == 48_000)
        #expect(hello.gapSamples == 9_600)
        #expect(hello.featureNames.count == 12)
        #expect(try await client.selfTest().ok)

        // The worked example at P2 (engine pcm_sha256 for renderer 0.1.0).
        let rendered = try await client.render(.example, profile: .p2)
        #expect(rendered.recipeSHA256 == Recipe.example.sha256)
        let audio = try rendered.audio.verified()
        #expect(audio.nSamples == 28_800)
        #expect(audio.pcmSHA256 == "4c0467de354c076c0b30bc9af794e31384afdf161af24605fb621cc455c35d87")

        let rejected = try await client.validate(
            .text(#"{"total_ms":450,"pitches":[0,0,0],"rhythm_weights":[1,4,4],"gaps_ms":[60,60],"amplitudes":[1.0,0.8,0.6]}"#),
            profile: .p2,
            committed: [AtomReference(refID: "K-a1", recipe: .example)])
        #expect(rejected.codes == ["E_EVENT_SHORT"])
        #expect(rejected.nearestID == "K-a1")
        let badJSON = try await client.validate(.text(#"{"total_ms":450"#), profile: .p2)
        #expect(badJSON.codes == ["E_JSON"])

        let grammar = try await client.grammar()
        #expect(grammar.messages.count == 32)
        #expect(grammar.heldoutMessages.count == 14)

        let book = try await client.syntheticBook(profile: .p1)
        #expect(book.atoms.count == 16)
        let trained = try #require(grammar.trainedMessages.first)
        let action = try #require(book.atom(trained.action)).reference
        let referent = try #require(book.atom(trained.referent)).reference
        let message = try await client.compose(action: action, referent: referent, profile: .p1, bookID: book.bookID)
        let messageAudio = try message.audio.verified()
        #expect(messageAudio.nSamples == message.nSamples)
        #expect(message.referentOnset == message.actionSamples + 9_600)
        #expect(messageAudio.samples[message.actionSamples..<message.referentOnset].allSatisfy { $0 == 0 })

        let heldout = try #require(grammar.heldoutMessages.first)
        do {
            _ = try await client.compose(
                action: try #require(book.atom(heldout.action)).reference,
                referent: try #require(book.atom(heldout.referent)).reference, profile: .p1, bookID: book.bookID)
            Issue.record("a held-out message was composed")
        } catch let error as BridgeError {
            #expect(error.engineCode == "E_HELDOUT")
        }

        let assets = try await client.nonlexicalList()
        #expect(assets.count == 7)
        let asset = try await client.nonlexicalGet(id: try #require(assets.first).id)
        #expect(try asset.audio.verified().pcmSHA256 == asset.asset.pcmSHA256)

        let vectors = try await client.vectorsCheck()
        #expect(vectors.ok)

        await client.stop()
        #expect(await client.status == .stopped)
    }

    @Test func storeFallbackGoldenAndPackage() async throws {
        let configuration = try #require(LiveBridge.configuration)
        let client = BridgeClient(configuration: configuration)
        try await client.start()
        let book = try await client.syntheticBook(profile: .p1)
        let ka1 = try #require(book.atom("K-a1"))
        let ka2 = try #require(book.atom("K-a2"))

        _ = try await client.storeReset()
        let bookID = "DEMO-LIVE-1"
        _ = try await client.storeCreate(bookID: bookID, profile: .p1)
        let first = try await client.storeCommit(bookID: bookID, atomID: "K-a1", semanticLabel: "ADD_ONE", recipe: ka1.recipe)
        #expect(first.outcome == "commit")
        #expect(first.entry.pcmSHA256 == ka1.pcmSHA256)
        let again = try await client.storeCommit(bookID: bookID, atomID: "K-a1", semanticLabel: "ADD_ONE", recipe: ka1.recipe)
        #expect(again.isNoop)
        do {
            _ = try await client.storeCommit(bookID: bookID, atomID: "K-a1", semanticLabel: "ADD_ONE", recipe: ka2.recipe)
            Issue.record("an overwrite was accepted")
        } catch let error as BridgeError {
            #expect(error.engineType == "OverwriteRejected")
        }
        let short = Recipe(totalMs: 450, pitches: [0, 0, 0], rhythmWeights: [1, 4, 4], gapsMs: [60, 60],
                           amplitudes: [1.0, 0.8, 0.6])
        do {
            _ = try await client.storeCommit(bookID: bookID, atomID: "K-a2", semanticLabel: "REMOVE_ONE", recipe: short)
            Issue.record("an inadmissible recipe was committed")
        } catch let error as BridgeError {
            #expect(error.engineType == "CommitRejected")
            #expect(error.validationDetails?.codes.contains("E_EVENT_SHORT") == true)
        }
        let list = try await client.storeList(bookID: bookID)
        #expect(list.entries.map(\.atomID) == ["K-a1"])
        #expect(try await client.storeRecords(bookID: bookID).count >= 3)
        #expect(try await client.storeVerify(bookID: bookID).ok)
        let frozen = try await client.storeFreeze(bookID: bookID)
        #expect(try await client.storeList(bookID: bookID).isFrozen)
        _ = try await client.storeTamper(bookID: bookID, kind: .flipBlobByte)
        let damaged = try await client.storeVerify(bookID: bookID, expectedHead: frozen.chainHead)
        #expect(!damaged.ok)
        #expect(!damaged.issues.isEmpty)
        do {
            _ = try await client.storeCreate(bookID: "STUDY-1", profile: .p1)
            Issue.record("a non-DEMO book was created")
        } catch is BridgeError {}

        let fallback = try await client.fallbackDemo(profile: .p1)
        #expect(fallback.seedLabel.hasPrefix("DEMO-"))
        #expect(fallback.bank.count == 64)
        #expect(fallback.book.count == 16)
        let scan = try await client.fallbackScan(
            profile: .p1, book: fallback.book.prefix(8).map(\.reference), used: [0])
        #expect(scan.outcome == "selected" || scan.outcome == "exhausted")
        #expect(scan.used == [0])
        #expect(scan.log.first?.outcome == "used")

        let golden = try await client.goldenCheck()
        #expect(golden.ok)
        #expect(golden.items > 0)

        let package = try await client.packageDemo()
        #expect(package.loaderOK)
        #expect(package.leakReportOK == true)
        #expect(package.counts.atomWavs == 16)
        #expect(package.counts.messageWavs == 18)
        #expect(package.counts.heldoutIDs == 14)

        await client.stop()
    }
}
