import Foundation
import Testing

@testable import AVSoundDemoCore

@Suite("Protocol result shapes decode")
struct ModelDecodingTests {
    @Test func hello() throws {
        let hello = try Fixtures.decode(Hello.self, Fixtures.hello)
        #expect(hello.bridgeVersion == 1)
        #expect(hello.rendererVersion == "0.1.0")
        #expect(hello.sampleRate == 48_000)
        #expect(hello.gapSamples == 9_600)
        #expect(hello.threshold == "0.1")
        #expect(hello.thresholdFloat == 0.1)
        #expect(hello.profiles.map(\.profile) == [.p1, .p2, .p3])
        #expect(hello.profiles.map(\.f0Hz) == Profile.allCases.map(\.f0Hz))
        #expect(hello.domain.totalMs == RecipeDomain.totalMs)
        #expect(hello.domain.pitches == RecipeDomain.pitches)
        #expect(hello.domain.amplitudes == RecipeDomain.amplitudes)
        #expect(hello.reasonCodes.count == 9)
        #expect(hello.featureNames.count == 12)
    }

    @Test func helloMissingFieldIsADecodingError() throws {
        let broken = Fixtures.hello.replacingOccurrences(of: #""numpy":"2.4.6","#, with: "")
        #expect(throws: BridgeError.decoding("hello: missing key numpy")) {
            try Fixtures.decode(Hello.self, broken, cmd: "hello")
        }
    }

    @Test func selfTestAndEmpty() throws {
        let result = try Fixtures.decode(SelfTestResult.self, Fixtures.selfTest)
        #expect(result.ok)
        _ = try Fixtures.decode(EmptyResult.self, Fixtures.empty)
    }

    @Test func render() throws {
        let result = try Fixtures.decode(RenderResult.self, Fixtures.render)
        #expect(result.recipe == .example)
        #expect(result.recipeSHA256 == Recipe.example.sha256)
        #expect(result.profile == .p2)
        #expect(result.nSamples == 28_800)
        #expect(result.eventSamples == [8640, 4320, 12960])
        #expect(result.eventOnsets == [0, 10560, 15840])
        #expect(result.gapSamples == [1920, 960])
        #expect(result.peak == 13_865)
        #expect(result.peakDbfs == -7.47033614455637)
        #expect(!result.overflow && !result.shortEvent && !result.nonfinite)
        #expect(result.audio.hasAudio)
        let audio = try result.audio.verified()
        #expect(audio.samples == Fixtures.smallWAVSamples)
    }

    @Test func renderOverflowHasNoAudioAndToleratesNonFiniteNumbers() throws {
        let result = try Fixtures.decode(RenderResult.self, Fixtures.renderOverflow)
        #expect(result.overflow)
        #expect(result.durationMs == 600)
        #expect(result.peakDbfs == -Double.infinity)
        #expect(!result.audio.hasAudio)
        #expect(throws: AudioVerificationError.missingAudio) { try result.audio.verified() }
    }

    @Test func randomRecipeFeaturesDistance() throws {
        #expect(try Fixtures.decode(RandomRecipeResult.self, Fixtures.randomRecipe).recipe == .example)
        let features = try Fixtures.decode(FeaturesResult.self, Fixtures.features)
        #expect(features.names.count == 12)
        #expect(features.exact[2] == "5/6")
        #expect(features.values[2] == 0.8333333333333334)
        let distance = try Fixtures.decode(DistanceResult.self, Fixtures.distance)
        #expect(distance.sumSq == "989/450")
        #expect(distance.separated)
    }

    @Test func validationRejected() throws {
        let result = try Fixtures.decode(ValidationResult.self, Fixtures.validationRejected)
        #expect(!result.ok)
        #expect(result.codes == ["E_EVENT_SHORT"])
        #expect(result.primaryCode == "E_EVENT_SHORT")
        #expect(result.reasons.first?.message.hasPrefix("event 1 is 1760 samples") == true)
        #expect(result.features?.count == 12)
        #expect(result.nearestID == "K-a1")
        #expect(result.nearestIndex == 0)
        #expect(result.nearestDistance == 0.4522833019084224)
        #expect(result.pcmSHA256 == "a88b842cf40b930e0f3859470f8911a746d24895941eb4111dd17b3964dfbe51")
        #expect(result.profile == .p2)
        #expect(result.threshold == "0.1")
        #expect(result.eventSamples == [1760, 7040, 7040])
        #expect(result.recipe?.sha256 == result.recipeSHA256)
        #expect(result.raw["result_version"] == 1)
        #expect(result.raw["codes"]?[0] == "E_EVENT_SHORT")
    }

    @Test func validationBadJSONHasNullFields() throws {
        let result = try Fixtures.decode(ValidationResult.self, Fixtures.validationBadJSON)
        #expect(result.codes == ["E_JSON"])
        #expect(result.features == nil)
        #expect(result.recipe == nil)
        #expect(result.pcmSHA256 == nil)
        #expect(result.nearestID == nil)
        #expect(result.nearestDistance == nil)
        #expect(result.raw["recipe"] == .null)
    }

    @Test func nearestAndNull() throws {
        let nearest = try Fixtures.decode(NearestResult?.self, Fixtures.nearest)
        #expect(nearest?.refID == "K-a1")
        #expect(nearest?.sumSq == "491/200")
        let none = try Fixtures.decode(NearestResult?.self, "null")
        #expect(none == nil)
    }

    @Test func grammar() throws {
        let grammar = try Fixtures.decode(Grammar.self, Fixtures.grammar)
        #expect(grammar.familyIDs == ["K", "Q"])
        #expect(grammar.atomIDs.count == 16)
        #expect(grammar.messages.count == 3)
        #expect(grammar.heldoutMessages.map(\.id) == ["K-a1-r2"])
        let first = grammar.messages[0]
        #expect(first.action == "K-a1")
        #expect(first.referent == "K-r1")
        #expect(first.trainingWave == 1)
        #expect(first.heldoutSet == nil)
        #expect(grammar.messages[1].heldoutSet == "H-V1")
    }

    @Test func grammarFamiliesAsObjects() throws {
        let variant = Fixtures.grammar.replacingOccurrences(
            of: #""families":["K","Q"]"#,
            with: #""families":[{"id":"K","actions":["K-a1"]},{"id":"Q","actions":["Q-a1"]}]"#)
        #expect(try Fixtures.decode(Grammar.self, variant).familyIDs == ["K", "Q"])
    }

    @Test func composeAndCompositeHash() throws {
        let compose = try Fixtures.decode(ComposeResult.self, Fixtures.compose)
        #expect(compose.messageID == "K-a1-r1")
        #expect(compose.referentOnset == compose.actionSamples + 9_600)
        #expect(try compose.audio.verified().nSamples == 5)
        let hash = try Fixtures.decode(CompositeHashResult.self, Fixtures.compositeHash)
        #expect(hash.isHeldout)
        #expect(hash.compositeSHA256 == Fixtures.hash64)
    }

    @Test func syntheticBook() throws {
        let book = try Fixtures.decode(SyntheticBook.self, Fixtures.syntheticBook)
        #expect(book.bookID == "DEMO-P1")
        let atom = try #require(book.atom("K-a1"))
        #expect(atom.nSamples == 21_600)
        #expect(atom.recipe.sha256 == "4a6603dd7921ab3af1fc4e17275b64395430f7f95461c6ffac69c185b357650b")
        #expect(atom.reference == AtomReference(refID: "K-a1", recipe: atom.recipe))
    }

    @Test func nonlexical() throws {
        let list = try Fixtures.decode(NonlexicalList.self, Fixtures.nonlexicalList)
        #expect(list.assets.count == 2)
        #expect(list.assets[0].profile == .p1)
        #expect(list.assets[0].nSamples == 96_000)
        #expect(list.assets[1].profile == nil)
        let get = try Fixtures.decode(NonlexicalAssetAudio.self, Fixtures.nonlexicalGet)
        #expect(get.asset.id == "calibration-P1")
        // The audio fields share the object with the asset fields; the fixture's hashes are
        // the asset's, not the small WAV's, so verification must refuse it.
        #expect(get.audio.hasAudio)
        #expect(throws: AudioVerificationError.self) { try get.audio.verified() }
    }

    @Test func vectorsAndGolden() throws {
        let vectors = try Fixtures.decode(VectorsCheck.self, Fixtures.vectorsCheck)
        #expect(!vectors.ok)
        #expect(vectors.renderer.checked == 21)
        #expect(vectors.composition.mismatches.first?["name"] == "K-a1")
        let golden = try Fixtures.decode(GoldenCheck.self, Fixtures.goldenCheck)
        #expect(golden.ok && golden.items == 117 && golden.digest == Fixtures.hash64)
        let variant = try Fixtures.decode(GoldenCheck.self, Fixtures.goldenCheckVariant)
        #expect(variant.items == 2)
        #expect(variant.digest == Fixtures.hash64)
    }

    @Test func store() throws {
        #expect(try Fixtures.decode(StoreResetResult.self, Fixtures.storeReset).root.hasPrefix("/tmp/"))
        #expect(try Fixtures.decode(StoreCreateResult.self, Fixtures.storeCreate).bookID == "DEMO-STORE-1")
        let commit = try Fixtures.decode(StoreCommitResult.self, Fixtures.storeCommit)
        #expect(commit.isNoop)
        #expect(commit.entry.recipe == .example)
        #expect(commit.entry.commitIndex == 0)
        let list = try Fixtures.decode(StoreListResult.self, Fixtures.storeList)
        #expect(list.isFrozen && !list.isVoid)
        #expect(list.entries.map(\.atomID) == ["K-a1"])
        let records = try Fixtures.decode(StoreRecordsResult.self, Fixtures.storeRecords)
        #expect(records.records.first?["event"] == "create_book")
        #expect(try Fixtures.decode(StoreFreezeResult.self, Fixtures.storeFreeze).chainHead == Fixtures.hash64)
        let verify = try Fixtures.decode(StoreVerifyResult.self, Fixtures.storeVerify)
        #expect(!verify.ok)
        #expect(verify.issues.map(\.line) == [3, nil, 7])
        #expect(try Fixtures.decode(StoreTamperResult.self, Fixtures.storeTamper).done.isEmpty == false)
    }

    @Test func fallback() throws {
        let demo = try Fixtures.decode(FallbackDemo.self, Fixtures.fallbackDemo)
        #expect(demo.seedLabel.hasPrefix("DEMO-"))
        #expect(demo.bank.first?.index == 0)
        #expect(demo.book.first?.reference.refID == "K-a1")
        let scan = try Fixtures.decode(ScanResult.self, Fixtures.fallbackScan)
        #expect(scan.outcome == "selected")
        #expect(!scan.isExhausted)
        #expect(scan.selectedIndex == 2)
        #expect(scan.selectedSource == "fallback-bank-P1-02")
        #expect(scan.used == [0])
        #expect(scan.referenceIDs == ["K-a1", "K-a2"])
        #expect(scan.log.map(\.outcome) == ["used", "rejected", "selected"])
        #expect(scan.log[1].codes == ["E_SEPARATION"])
        #expect(scan.raw["scan_version"] == 1)
    }

    @Test func scanResultToleratesAnyObject() throws {
        let scan = try Fixtures.decode(ScanResult.self, #"{"outcome":"exhausted","selected_index":null}"#)
        #expect(scan.isExhausted)
        #expect(scan.selectedIndex == nil)
        #expect(scan.log.isEmpty)
    }

    @Test func packageDemo() throws {
        let package = try Fixtures.decode(PackageDemo.self, Fixtures.packageDemo)
        #expect(package.loaderOK)
        #expect(package.leakReportOK == true)
        #expect(package.counts.atomWavs == 16)
        #expect(package.counts.messageWavs == 18)
        #expect(package.counts.heldoutIDs == 14)
        #expect(package.files.count == 2)
        #expect(package.answersPreview.first?["message_id"] == "K-a1-r1")
    }

    @Test func engineErrorWithDetails() throws {
        let line = BridgeCoding.errorLine(
            id: 4, type: "CommitRejected", code: "E_EVENT_SHORT", message: "not admissible",
            details: try JSONValue(jsonString: Fixtures.validationRejected))
        do {
            _ = try BridgeCoding.decodeResult(StoreCommitResult.self, from: Data(line.utf8), cmd: "store_commit")
            Issue.record("expected an engine error")
        } catch let error as BridgeError {
            #expect(error.engineType == "CommitRejected")
            #expect(error.engineCode == "E_EVENT_SHORT")
            #expect(error.validationDetails?.codes == ["E_EVENT_SHORT"])
        }
    }

    @Test func errorWithNullCode() throws {
        let line = #"{"id":2,"ok":false,"error":{"type":"ValueError","code":null,"message":"bad"}}"#
        #expect(throws: BridgeError.engine(type: "ValueError", code: nil, message: "bad", details: nil)) {
            try BridgeCoding.decodeResult(EmptyResult.self, from: Data(line.utf8), cmd: "x")
        }
    }

    @Test func malformedEnvelopes() throws {
        #expect(throws: BridgeError.protocolViolation("x: response has no boolean \"ok\"")) {
            try BridgeCoding.decodeResult(EmptyResult.self, from: Data(#"{"id":1}"#.utf8), cmd: "x")
        }
        #expect(throws: BridgeError.protocolViolation("x: success response has no \"result\"")) {
            try BridgeCoding.decodeResult(EmptyResult.self, from: Data(#"{"id":1,"ok":true}"#.utf8), cmd: "x")
        }
        #expect(throws: BridgeError.self) {
            try BridgeCoding.decodeResult(EmptyResult.self, from: Data(#"{"id":1,"ok":false}"#.utf8), cmd: "x")
        }
    }

    @Test func argumentEncoding() throws {
        let encoder = BridgeCoding.makeEncoder()
        func text(_ value: some Encodable) throws -> String {
            String(decoding: try encoder.encode(value), as: UTF8.self)
        }
        #expect(try text(EmptyArgs()) == "{}")
        #expect(
            try text(StoreCommitArgs(bookID: "DEMO-1", atomID: "K-a1", semanticLabel: nil, recipe: .example))
                .contains(#""semantic_label":null"#))
        #expect(try text(StoreVerifyArgs(bookID: "DEMO-1")) == #"{"book_id":"DEMO-1"}"#)
        #expect(try text(RandomRecipeArgs(seed: 3)) == #"{"admissible_only":true,"seed":3}"#)
        #expect(try text(ValidationCandidate.text("{\"total_ms\":450")) == #""{\"total_ms\":450""#)
        let validate = try JSONValue(
            jsonString: try text(
                ValidateArgs(
                    candidate: .recipe(.example), profile: .p2,
                    committed: [AtomReference(refID: "K-a1", recipe: .example)])))
        #expect(validate["candidate"]?["total_ms"] == 600)
        #expect(validate["committed"]?[0]?["ref_id"] == "K-a1")
        #expect(validate["use_reserved"] == true)
        #expect(validate["threshold"] == nil)
        #expect(try text(StoreTamperArgs(bookID: "DEMO-1", kind: .truncateLog)).contains(#""kind":"truncate_log""#))
        let line = BridgeCoding.requestLine(id: 7, cmd: "render", argsJSON: Data("{}".utf8))
        #expect(String(decoding: line, as: UTF8.self) == #"{"id":7,"cmd":"render","args":{}}"#)
    }
}
