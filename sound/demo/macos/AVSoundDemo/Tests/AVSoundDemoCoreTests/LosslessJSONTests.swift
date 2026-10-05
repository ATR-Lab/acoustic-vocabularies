import Foundation
import Testing

@testable import AVSoundDemoCore

/// Raw payloads keep the numbers of the bridge's JSON text (`1.0` is not `1`), so what
/// the app shows of a store record is the record the engine hashed.
@Suite("Lossless JSON")
struct LosslessJSONTests {
    /// A `store_records` response of the real bridge (a DEMO-P1 book with one commit).
    static let storeRecordsLine = #"{"id":4,"ok":true,"result":{"records":[{"book_id":"DEMO-P1","event":"create_book","kind":"synthetic","prev_sha256":"0000000000000000000000000000000000000000000000000000000000000000","profile":"P1","record_sha256":"ea252bab734ebfed25a7f4fe9b18c0d3e90eaf816551a127fc26ebe0d9026df4","record_version":1,"renderer_hash":"03a99200e8546fc113d320d499a071159afa219d914b54e98c88449791fd151d","renderer_version":"0.1.0","seq":0,"threshold":"0.1","timestamp":"2026-10-05T04:21:09.215Z","validator_hash":"fd2d6acfb5aa9f673b46e9e8baab3abda816142af6ddd5e32a5bcecef928a650","validator_version":"0.1.0"},{"atom_id":"K-a1","book_id":"DEMO-P1","commit_index":0,"event":"commit","extra_references_sha256":null,"family":"K","file_sha256":"f4c67dd5e7fcd94dd8d810b6afa54f036b37478c3eb56d690784b585701f978d","matrix_index":1,"n_samples":28800,"pcm_sha256":"3ba20f610c6afc928151b959fa9a3e2fcd4a1ddcc981d2e4d9607a687b4d2483","prev_sha256":"d8a68fdd740a6b2e4f8fb530664d76153a6120cca2ebe72d33cd6729a68253b0","profile":"P1","recipe":{"amplitudes":[1.0,0.6,0.8],"gaps_ms":[40,20],"pitches":[-3,0,4],"rhythm_weights":[2,1,3],"total_ms":600},"recipe_sha256":"224775bc696fa696ae2a43cc27d2919e8089ca43517022db902305274df7dd61","record_sha256":"62044e76a4c42a54ed5a7eea52fead3524c3f543ad4b1e56629d934cc18ad81a","record_version":1,"renderer_version":"0.1.0","reserved_sha256":"d9869d6af3fb467bce77ea1330761b0544f820a66cdd999766ae94e55dd76932","role":"action","semantic_label":"ADD_ONE","seq":1,"source":"demo-app","threshold":"0.1","timestamp":"2026-10-05T04:21:09.220Z","validator_version":"0.1.0"}]}}"#

    @Test func numbersKeepIntegerAndFloatApart() throws {
        let value = try JSONValue(jsonString: #"[1, 1.0, 0.0, -0, -0.5, 1e2, 2E-3, 12345678901234567890, 0.6]"#)
        #expect(value == [.int(1), .double(1.0), .double(0.0), .int(0), .double(-0.5), .double(100), .double(0.002),
                          .double(Double("12345678901234567890")!), .double(0.6)])
        #expect(value.compactString == "[1,1.0,0.0,0,-0.5,100.0,0.002,1.2345678901234567e+19,0.6]")
        let floats: JSONValue = ["a": [1.0, 0.0, 2], "b": ["c": 3.0]]
        #expect(try JSONValue(jsonString: floats.prettyString) == floats)
        #expect(try JSONValue(jsonString: floats.compactString).compactString == #"{"a":[1.0,0.0,2],"b":{"c":3.0}}"#)
    }

    @Test func stringsAndEscapes() throws {
        let value = try JSONValue(jsonString: #"["a\"b\\c\/\n\té😀", "\ud800x", "plain é"]"#)
        #expect(value[0] == .string("a\"b\\c/\n\t\u{e9}\u{1F600}"))
        #expect(value[1] == .string("\u{FFFD}x"))
        #expect(value[2] == .string("plain é"))
    }

    @Test func strictParserRefusesWhatJSONForbids() {
        for text in ["", "[1,]", "{\"a\" 1}", "01", "1.", "[1] x", "\"\u{01}\"", "-", "tru", "{1:2}"] {
            #expect(throws: JSONValueParser.ParseError.self, "\(text)") {
                try JSONValueParser.parse(Data(text.utf8))
            }
        }
        func nested(_ depth: Int) -> Data {
            Data((String(repeating: "[", count: depth) + String(repeating: "]", count: depth)).utf8)
        }
        #expect(throws: Never.self) { try JSONValueParser.parse(nested(JSONValueParser.maxDepth)) }
        #expect(throws: JSONValueParser.ParseError.self) { try JSONValueParser.parse(nested(JSONValueParser.maxDepth + 1)) }
        #expect(throws: JSONValueParser.ParseError.self) { try JSONValueParser.parse(nested(100_000)) }
        // Python's non-finite tokens are accepted, as before.
        #expect(throws: Never.self) { try JSONValueParser.parse(Data("[NaN,-Infinity,Infinity]".utf8)) }
    }

    /// The finding: `amplitudes: [1.0, 0.6, 0.8]` was shown as `[1, 0.6, 0.8]`, and the
    /// shown record no longer hashed to its `record_sha256`.
    @Test func storeRecordsKeepTheLoggedBytes() throws {
        let result = try BridgeCoding.decodeResult(
            StoreRecordsResult.self, from: Data(Self.storeRecordsLine.utf8), cmd: "store_records")
        let records = result.records
        #expect(records.count == 2)
        let commit = records[1]
        #expect(commit["recipe"]?["amplitudes"] == [.double(1.0), .double(0.6), .double(0.8)])
        #expect(commit.prettyString.contains("1.0,"))
        #expect(!commit.compactString.contains("[1,0.6"))

        // record_sha256 is the SHA-256 of the canonical JSON without that field, and each
        // prev_sha256 the SHA-256 of the previous log line: both recompute from the
        // decoded values (all ASCII, so compactString is the engine's canonical_json).
        for record in records {
            guard case .object(var fields) = record else {
                Issue.record("record is not an object")
                continue
            }
            let expected = fields.removeValue(forKey: "record_sha256")?.stringValue
            #expect(Hashing.sha256Hex(Data(JSONValue.object(fields).compactString.utf8)) == expected)
        }
        #expect(Hashing.sha256Hex(Data(records[0].compactString.utf8)) == records[1]["prev_sha256"]?.stringValue)
    }

    @Test func validationResultRawKeepsFloats() throws {
        let line = BridgeCoding.successLine(id: 3, result: [
            "ok": false, "codes": ["E_DUPLICATE"], "messages": ["m"], "nearest_id": "K-a1",
            "nearest_index": 0, "nearest_distance": 0.0, "recipe": ["amplitudes": [1.0, 0.6, 0.8]],
        ])
        #expect(line.contains(#""nearest_distance":0.0"#))
        let result = try BridgeCoding.decodeResult(ValidationResult.self, from: Data(line.utf8), cmd: "validate")
        #expect(result.nearestDistance == 0)
        #expect(result.raw["nearest_distance"] == .double(0.0))
        #expect(result.raw.compactString.contains(#""nearest_distance":0.0"#))
        #expect(result.raw.compactString.contains(#""amplitudes":[1.0,0.6,0.8]"#))
    }

    @Test func errorDetailsKeepFloats() throws {
        let line = BridgeCoding.errorLine(
            id: 5, type: "CommitRejected", code: "E_REJECTED", message: "refused",
            details: ["ok": false, "codes": ["E_SEPARATION"], "messages": ["m"], "nearest_distance": 0.0])
        #expect(throws: BridgeError.self) {
            try BridgeCoding.decodeResult(StoreCommitResult.self, from: Data(line.utf8), cmd: "store_commit")
        }
        do {
            _ = try BridgeCoding.decodeResult(StoreCommitResult.self, from: Data(line.utf8), cmd: "store_commit")
        } catch let BridgeError.engine(_, _, _, details) {
            #expect(details?["nearest_distance"] == .double(0.0))
        } catch {
            Issue.record("unexpected error \(error)")
        }
    }

    @Test func aPlainDecoderStillWorks() throws {
        // Without BridgeCoding's document source, JSONDecoder cannot tell 1.0 from 1.
        let value = try JSONDecoder().decode(JSONValue.self, from: Data("[1.0, 2.5]".utf8))
        #expect(value == [.int(1), .double(2.5)])
    }
}

@Suite("Threshold text")
struct ThresholdTextTests {
    @Test func onlyPlainNonNegativeDecimals() {
        for text in ["0", "0.1", "0.10", "1", "12.5", "0.000"] {
            #expect(ThresholdText.isValid(text), "\(text)")
        }
        for text in ["", "1/10", ".1", "1.", "-1", "+1", "01", "0.1e0", "1e-1", " 0.1", "0.1.2", "\u{0661}"] {
            #expect(!ThresholdText.isValid(text), "\(text)")
        }
    }
}
