// Locates repository fixtures (reference vectors) relative to this source file.

import Foundation
import Testing

@testable import AVSoundSpec

enum RepoFixtures {
    /// The repository root: the first ancestor of this file containing sound/pyproject.toml.
    static func repoRoot(from file: String = #filePath) throws -> URL {
        var dir = URL(fileURLWithPath: file).deletingLastPathComponent()
        while dir.path != "/" {
            if FileManager.default.fileExists(atPath: dir.appendingPathComponent("sound/pyproject.toml").path) {
                return dir
            }
            dir.deleteLastPathComponent()
        }
        throw SpecError("repository root (sound/pyproject.toml) not found above \(file)")
    }

    static func load<T: Decodable>(_ type: T.Type, _ relativePath: String) throws -> T {
        let url = try repoRoot().appendingPathComponent(relativePath)
        return try JSONDecoder().decode(T.self, from: Data(contentsOf: url))
    }
}

/// sound/testvectors/renderer/vectors.json
struct RendererVectorFile: Decodable {
    let rendererVersion: String
    let vectors: [Vector]

    struct Vector: Decodable {
        let name: String
        let profile: String
        let recipe: SpecRecipe
        let nSamples: Int
        let eventSamples: [Int]
        let eventOnsets: [Int]
        let peak: Int
        let overflow: Bool
        let shortEvent: Bool
        let pcmSha256: String?
        let fileSha256: String?

        enum CodingKeys: String, CodingKey {
            case name, profile, recipe, peak, overflow
            case nSamples = "n_samples"
            case eventSamples = "event_samples"
            case eventOnsets = "event_onsets"
            case shortEvent = "short_event"
            case pcmSha256 = "pcm_sha256"
            case fileSha256 = "file_sha256"
        }
    }

    enum CodingKeys: String, CodingKey {
        case vectors
        case rendererVersion = "renderer_version"
    }
}

/// sound/testvectors/composition/vectors.json
struct CompositionVectorFile: Decodable {
    let rendererVersion: String
    let gapSamples: Int
    let books: [Book]
    let patterns: [Pattern]

    struct Book: Decodable {
        let bookId: String
        let profile: String
        let atoms: [Atom]
        let messages: [Message]

        enum CodingKeys: String, CodingKey {
            case profile, atoms, messages
            case bookId = "book_id"
        }
    }

    struct Atom: Decodable {
        let atomId: String
        let recipe: SpecRecipe
        let nSamples: Int
        let pcmSha256: String
        let fileSha256: String

        enum CodingKeys: String, CodingKey {
            case recipe
            case atomId = "atom_id"
            case nSamples = "n_samples"
            case pcmSha256 = "pcm_sha256"
            case fileSha256 = "file_sha256"
        }
    }

    struct Message: Decodable {
        let messageId: String
        let actionId: String
        let referentId: String
        let nSamples: Int
        let durationMs: Int
        let compositeSha256: String

        enum CodingKeys: String, CodingKey {
            case messageId = "message_id"
            case actionId = "action_id"
            case referentId = "referent_id"
            case nSamples = "n_samples"
            case durationMs = "duration_ms"
            case compositeSha256 = "composite_sha256"
        }
    }

    struct Pattern: Decodable {
        let name: String
        let action: PatternPart
        let referent: PatternPart
        let actionPcmSha256: String
        let referentPcmSha256: String
        let compositeSha256: String
        let nSamples: Int

        enum CodingKeys: String, CodingKey {
            case name, action, referent
            case actionPcmSha256 = "action_pcm_sha256"
            case referentPcmSha256 = "referent_pcm_sha256"
            case compositeSha256 = "composite_sha256"
            case nSamples = "n_samples"
        }
    }

    struct PatternPart: Decodable {
        let mul: Int
        let add: Int
        let nSamples: Int

        enum CodingKeys: String, CodingKey {
            case mul, add
            case nSamples = "n_samples"
        }

        /// sample[i] = (mul * i + add) mod 65535 - 32767, as int16 little endian.
        var pcm: Data {
            var data = Data(count: 2 * nSamples)
            for i in 0..<nSamples {
                let sample = Int16((mul * i + add) % 65535 - 32767)
                let u = UInt16(bitPattern: sample)
                data[2 * i] = UInt8(truncatingIfNeeded: u)
                data[2 * i + 1] = UInt8(truncatingIfNeeded: u >> 8)
            }
            return data
        }
    }

    enum CodingKeys: String, CodingKey {
        case books, patterns
        case rendererVersion = "renderer_version"
        case gapSamples = "gap_samples"
    }
}
