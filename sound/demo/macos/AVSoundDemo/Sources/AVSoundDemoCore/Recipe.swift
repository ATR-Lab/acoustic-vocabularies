import CryptoKit
import Foundation

/// A render profile. The profile is passed to the renderer separately from the recipe.
public enum Profile: String, Codable, Sendable, Hashable, CaseIterable, Identifiable {
    case p1 = "P1"
    case p2 = "P2"
    case p3 = "P3"

    public var id: String { rawValue }

    /// Base frequency in Hz (300, 450 or 675).
    public var f0Hz: Int {
        switch self {
        case .p1: 300
        case .p2: 450
        case .p3: 675
        }
    }
}

/// The value domain of `sound/schema/recipe.schema.json` (renderer spec 0.1.0).
public enum RecipeDomain {
    public static let totalMs: [Int] = [450, 600, 750, 900]
    public static let pitches: [Int] = Array(-6...6)
    public static let rhythmWeights: [Int] = [1, 2, 3, 4]
    public static let gapsMs: [Int] = [20, 40, 60]
    public static let amplitudes: [Double] = [0.6, 0.8, 1.0]
}

/// One 3-event motif recipe, the canonical object of `sound/schema/recipe.schema.json`.
///
/// This is a plain value: it can hold out-of-domain values (the engine reports those as
/// `E_DOMAIN`). Use `domainViolations` to check it locally.
public struct Recipe: Codable, Sendable, Hashable {
    public var totalMs: Int
    public var pitches: [Int]
    public var rhythmWeights: [Int]
    public var gapsMs: [Int]
    public var amplitudes: [Double]

    public init(totalMs: Int, pitches: [Int], rhythmWeights: [Int], gapsMs: [Int], amplitudes: [Double]) {
        self.totalMs = totalMs
        self.pitches = pitches
        self.rhythmWeights = rhythmWeights
        self.gapsMs = gapsMs
        self.amplitudes = amplitudes
    }

    enum CodingKeys: String, CodingKey {
        case totalMs = "total_ms"
        case pitches
        case rhythmWeights = "rhythm_weights"
        case gapsMs = "gaps_ms"
        case amplitudes
    }

    /// The worked example of the renderer spec and the engine README.
    public static let example = Recipe(
        totalMs: 600, pitches: [-3, 0, 4], rhythmWeights: [2, 1, 3], gapsMs: [40, 20],
        amplitudes: [1.0, 0.6, 0.8])

    /// Compact JSON with sorted keys and amplitudes written as `0.6`, `0.8` or `1.0`:
    /// byte for byte the engine's `Recipe.canonical_json()`.
    public var canonicalJSON: String {
        func ints(_ values: [Int]) -> String { "[" + values.map(String.init).joined(separator: ",") + "]" }
        let amps = "[" + amplitudes.map(JSONValue.formatDouble).joined(separator: ",") + "]"
        return "{\"amplitudes\":\(amps),\"gaps_ms\":\(ints(gapsMs)),\"pitches\":\(ints(pitches)),"
            + "\"rhythm_weights\":\(ints(rhythmWeights)),\"total_ms\":\(totalMs)}"
    }

    /// SHA-256 of `canonicalJSON` (UTF-8), lowercase hex: the engine's `Recipe.sha256()`.
    public var sha256: String { Hashing.sha256Hex(Data(canonicalJSON.utf8)) }

    /// Human-readable schema and domain problems; empty when the recipe is in domain.
    public var domainViolations: [String] {
        var problems: [String] = []
        if !RecipeDomain.totalMs.contains(totalMs) {
            problems.append("total_ms: \(totalMs) is not one of \(RecipeDomain.totalMs)")
        }
        func check(_ name: String, _ values: [Int], count: Int, allowed: [Int]) {
            if values.count != count {
                problems.append("\(name): expected \(count) values, got \(values.count)")
            }
            for (i, v) in values.enumerated() where !allowed.contains(v) {
                problems.append("\(name)[\(i)]: \(v) is not allowed")
            }
        }
        check("pitches", pitches, count: 3, allowed: RecipeDomain.pitches)
        check("rhythm_weights", rhythmWeights, count: 3, allowed: RecipeDomain.rhythmWeights)
        check("gaps_ms", gapsMs, count: 2, allowed: RecipeDomain.gapsMs)
        if amplitudes.count != 3 {
            problems.append("amplitudes: expected 3 values, got \(amplitudes.count)")
        }
        for (i, a) in amplitudes.enumerated() where !RecipeDomain.amplitudes.contains(a) {
            problems.append("amplitudes[\(i)]: \(JSONValue.formatDouble(a)) is not allowed")
        }
        return problems
    }

    public var isInDomain: Bool { domainViolations.isEmpty }
}

/// SHA-256 helpers (CryptoKit).
public enum Hashing {
    /// Lowercase hex SHA-256 of `data`.
    public static func sha256Hex<D: DataProtocol>(_ data: D) -> String {
        let digest = SHA256.hash(data: data)
        var out = ""
        out.reserveCapacity(64)
        for byte in digest {
            out += String(byte, radix: 16).leftPadded(to: 2)
        }
        return out
    }

    /// True for a 64-character lowercase hex string.
    public static func isSHA256Hex(_ value: String) -> Bool {
        value.utf8.count == 64
            && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }
}

extension String {
    fileprivate func leftPadded(to width: Int) -> String {
        count >= width ? self : String(repeating: "0", count: width - count) + self
    }
}
