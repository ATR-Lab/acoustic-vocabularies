import Foundation
import Testing

@testable import AVSoundDemoCore

/// Repository files used as reference data (`sound/testvectors/...`).
enum RepoFiles {
    /// `<repo>/sound`, found from this source file.
    static let soundDirectory: URL = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()  // AVSoundDemoCoreTests
        .deletingLastPathComponent()  // Tests
        .deletingLastPathComponent()  // AVSoundDemo
        .deletingLastPathComponent()  // macos
        .deletingLastPathComponent()  // demo
        .deletingLastPathComponent()  // sound

    static func json(_ relativePath: String) throws -> JSONValue {
        try JSONValue(jsonData: Data(contentsOf: soundDirectory.appendingPathComponent(relativePath)))
    }
}

@Suite("Recipe")
struct RecipeTests {
    @Test func canonicalJSONMatchesPython() {
        #expect(
            Recipe.example.canonicalJSON
                == #"{"amplitudes":[1.0,0.6,0.8],"gaps_ms":[40,20],"pitches":[-3,0,4],"rhythm_weights":[2,1,3],"total_ms":600}"#
        )
        // Recipe.sha256() of the engine for the same recipe.
        #expect(Recipe.example.sha256 == "224775bc696fa696ae2a43cc27d2919e8089ca43517022db902305274df7dd61")
        let short = Recipe(totalMs: 450, pitches: [0, 0, 0], rhythmWeights: [1, 4, 4], gapsMs: [60, 60],
                           amplitudes: [1, 0.8, 0.6])
        #expect(short.canonicalJSON.hasPrefix(#"{"amplitudes":[1.0,0.8,0.6],"#))
        #expect(short.sha256 == "ae7fda76a7f93c88ec5764b6cfd1a6dda547150656dfe282b49c7ceb37977bee")
    }

    @Test func recipeHashesMatchTheCompositionVectors() throws {
        let vectors = try RepoFiles.json("testvectors/composition/vectors.json")
        let books = try #require(vectors["books"]?.arrayValue)
        var checked = 0
        for book in books {
            for atom in book["atoms"]?.arrayValue ?? [] {
                let recipe = try #require(try atom["recipe"]?.decode(Recipe.self))
                #expect(recipe.sha256 == atom["recipe_sha256"]?.stringValue, "atom \(atom["atom_id"]?.stringValue ?? "?")")
                #expect(recipe.isInDomain)
                checked += 1
            }
        }
        #expect(checked == 48)
    }

    @Test func codableUsesSnakeCase() throws {
        let data = try BridgeCoding.makeEncoder().encode(Recipe.example)
        let value = try JSONValue(jsonData: data)
        #expect(value["total_ms"] == 600)
        #expect(value["rhythm_weights"] == [2, 1, 3])
        #expect(value["gaps_ms"] == [40, 20])
        let decoded = try BridgeCoding.makeDecoder().decode(Recipe.self, from: Data(Fixtures.recipeJSON.utf8))
        #expect(decoded == .example)
    }

    @Test func domainViolations() {
        #expect(Recipe.example.domainViolations.isEmpty)
        let bad = Recipe(totalMs: 500, pitches: [7, 0], rhythmWeights: [1, 2, 5], gapsMs: [20, 40],
                         amplitudes: [0.7, 0.8, 1.0])
        let problems = bad.domainViolations
        #expect(problems.contains { $0.hasPrefix("total_ms") })
        #expect(problems.contains("pitches: expected 3 values, got 2"))
        #expect(problems.contains("pitches[0]: 7 is not allowed"))
        #expect(problems.contains("rhythm_weights[2]: 5 is not allowed"))
        #expect(problems.contains("amplitudes[0]: 0.7 is not allowed"))
        #expect(!bad.isInDomain)
    }

    @Test func profiles() {
        #expect(Profile.allCases.map(\.rawValue) == ["P1", "P2", "P3"])
        #expect(Profile.allCases.map(\.f0Hz) == [300, 450, 675])
    }
}

@Suite("JSONValue")
struct JSONValueTests {
    @Test func decodesEveryKind() throws {
        let value = try JSONValue(
            jsonString: #"{"a":null,"b":true,"c":3,"d":0.5,"e":"x","f":[1,"y"],"g":{"h":-2},"i":1.0}"#)
        #expect(value["a"] == .null)
        #expect(value["b"] == true)
        #expect(value["c"] == 3)
        #expect(value["d"] == 0.5)
        #expect(value["e"] == "x")
        #expect(value["f"] == [1, "y"])
        #expect(value["g"]?["h"]?.intValue == -2)
        #expect(value["i"]?.doubleValue == 1.0)
        #expect(value["missing"] == nil)
        #expect(value["f"]?[5] == nil)
    }

    @Test func nonFiniteNumbers() throws {
        let value = try JSONValue(jsonString: "[NaN, -Infinity, Infinity]")
        #expect(value[0]?.doubleValue?.isNaN == true)
        #expect(value[1]?.doubleValue == -.infinity)
        #expect(value.compactString == "[NaN,-Infinity,Infinity]")
    }

    @Test func compactAndPrettyText() throws {
        let value: JSONValue = ["z": 1, "a": [0.6, 1.0, "q\"\n"], "m": .null, "e": [:]]
        #expect(value.compactString == #"{"a":[0.6,1.0,"q\"\n"],"e":{},"m":null,"z":1}"#)
        #expect(value.prettyString.contains("\n  \"a\": [\n    0.6,"))
        // Whole-number floats stay floats (1.0) when read back.
        #expect(try JSONValue(jsonString: value.prettyString) == value)
        let other: JSONValue = ["a": [0.25, -3, "x"], "b": ["c": false]]
        #expect(try JSONValue(jsonString: other.prettyString) == other)
    }

    @Test func codableRoundTrip() throws {
        let value: JSONValue = ["list": [1, 2.5, false, .null], "text": "é"]
        let data = try BridgeCoding.makeEncoder().encode(value)
        #expect(try JSONValue(jsonData: data) == value)
        struct Pair: Decodable, Equatable { let list: [JSONValue]; let text: String }
        #expect(try value.decode(Pair.self) == Pair(list: [1, 2.5, false, .null], text: "é"))
    }
}

@Suite("Bounded log buffer")
struct BoundedBufferTests {
    @Test func keepsTheNewestElements() {
        var buffer = BoundedBuffer<Int>(capacity: 3)
        for i in 1...7 { buffer.append(i) }
        #expect(buffer.elements == [5, 6, 7])
        #expect(buffer.count == 3)
        #expect(buffer.suffix(2) == [6, 7])
        buffer.removeAll()
        #expect(buffer.isEmpty)
        buffer.append(9)
        #expect(buffer.elements == [9])
    }
}
