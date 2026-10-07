import Foundation

// Result shapes of PROTOCOL.md ("Commands"). Field names follow the protocol in camel
// case; the coding keys are the protocol's snake-case names.

// MARK: - hello, self_test

public struct ProfileInfo: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    public let f0Hz: Int

    public init(id: String, f0Hz: Int) {
        self.id = id
        self.f0Hz = f0Hz
    }

    enum CodingKeys: String, CodingKey {
        case id
        case f0Hz = "f0_hz"
    }

    /// The matching `Profile`, when the id is `P1`, `P2` or `P3`.
    public var profile: Profile? { Profile(rawValue: id) }
}

/// The recipe value domain as the engine reports it.
public struct DomainInfo: Codable, Sendable, Hashable {
    public let totalMs: [Int]
    public let pitches: [Int]
    public let rhythmWeights: [Int]
    public let gapsMs: [Int]
    public let amplitudes: [Double]

    enum CodingKeys: String, CodingKey {
        case totalMs = "total_ms"
        case pitches
        case rhythmWeights = "rhythm_weights"
        case gapsMs = "gaps_ms"
        case amplitudes
    }
}

/// `hello`: versions, hashes and constants of the running engine.
public struct Hello: Decodable, Sendable, Hashable {
    public let bridgeVersion: Int
    public let rendererVersion: String
    public let rendererHash: String
    public let rendererRecipeSchemaHash: String
    public let validatorVersion: String
    public let validatorHash: String
    public let python: String
    public let numpy: String
    public let sampleRate: Int
    public let gapSamples: Int
    /// Exact separation threshold text (`"0.1"`).
    public let threshold: String
    public let thresholdFloat: Double
    public let profiles: [ProfileInfo]
    public let domain: DomainInfo
    public let reasonCodes: [String]
    public let featureNames: [String]

    enum CodingKeys: String, CodingKey {
        case bridgeVersion = "bridge_version"
        case rendererVersion = "renderer_version"
        case rendererHash = "renderer_hash"
        case rendererRecipeSchemaHash = "renderer_recipe_schema_hash"
        case validatorVersion = "validator_version"
        case validatorHash = "validator_hash"
        case python, numpy
        case sampleRate = "sample_rate"
        case gapSamples = "gap_samples"
        case threshold
        case thresholdFloat = "threshold_float"
        case profiles, domain
        case reasonCodes = "reason_codes"
        case featureNames = "feature_names"
    }
}

/// `self_test`.
public struct SelfTestResult: Decodable, Sendable, Hashable {
    public let ok: Bool
    public let message: String
}

/// An empty result (`shutdown`) or any result whose content is not needed.
public struct EmptyResult: Decodable, Sendable, Hashable {
    public init() {}
    public init(from decoder: any Decoder) throws {}
}

// MARK: - render, random_recipe, features, distance

/// `render`.
public struct RenderResult: Decodable, Sendable, Hashable {
    public let recipe: Recipe
    public let recipeSHA256: String
    public let profile: Profile
    public let nSamples: Int
    public let durationMs: Double
    public let eventSamples: [Int]
    public let eventOnsets: [Int]
    public let gapSamples: [Int]
    public let peak: Int
    /// `nil` when the bridge sends `null` (silence).
    public let peakDbfs: Double?
    public let rms: Double
    public let shortEvent: Bool
    public let overflow: Bool
    public let nonfinite: Bool
    public let rendererVersion: String
    /// `wav_b64` is `nil` when the motif overflowed (no canonical WAV exists).
    public let audio: AudioPayload

    enum CodingKeys: String, CodingKey {
        case recipe
        case recipeSHA256 = "recipe_sha256"
        case profile
        case nSamples = "n_samples"
        case durationMs = "duration_ms"
        case eventSamples = "event_samples"
        case eventOnsets = "event_onsets"
        case gapSamples = "gap_samples"
        case peak
        case peakDbfs = "peak_dbfs"
        case rms
        case shortEvent = "short_event"
        case overflow, nonfinite
        case rendererVersion = "renderer_version"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        recipe = try c.decode(Recipe.self, forKey: .recipe)
        recipeSHA256 = try c.decode(String.self, forKey: .recipeSHA256)
        profile = try c.decode(Profile.self, forKey: .profile)
        nSamples = try c.decode(Int.self, forKey: .nSamples)
        durationMs = try c.decode(Double.self, forKey: .durationMs)
        eventSamples = try c.decode([Int].self, forKey: .eventSamples)
        eventOnsets = try c.decode([Int].self, forKey: .eventOnsets)
        gapSamples = try c.decode([Int].self, forKey: .gapSamples)
        peak = try c.decode(Int.self, forKey: .peak)
        peakDbfs = c.decodeLossy(Double.self, forKey: .peakDbfs)
        rms = try c.decode(Double.self, forKey: .rms)
        shortEvent = try c.decode(Bool.self, forKey: .shortEvent)
        overflow = try c.decode(Bool.self, forKey: .overflow)
        nonfinite = try c.decode(Bool.self, forKey: .nonfinite)
        rendererVersion = try c.decode(String.self, forKey: .rendererVersion)
        audio = try AudioPayload(from: decoder)
    }
}

/// `random_recipe`.
public struct RandomRecipeResult: Decodable, Sendable, Hashable {
    public let recipe: Recipe
}

/// `features`: the 12 normalized features, exact (`"0.25"`, `"5/6"`) and as floats.
public struct FeaturesResult: Decodable, Sendable, Hashable {
    public let names: [String]
    public let exact: [String]
    public let values: [Double]
}

/// `distance`.
public struct DistanceResult: Decodable, Sendable, Hashable {
    public let distance: Double
    /// Exact `sum((x_j - y_j)^2)` as text.
    public let sumSq: String
    /// Whether the distance is at least the configured threshold (decided exactly).
    public let separated: Bool

    enum CodingKeys: String, CodingKey {
        case distance
        case sumSq = "sum_sq"
        case separated
    }
}

// MARK: - validate, nearest

/// `validate`: the engine's `ValidationResult.to_dict()`
/// (`sound/schema/validation-result.schema.json`). Rejection is a normal result.
public struct ValidationResult: Decodable, Sendable, Hashable {
    public let ok: Bool
    /// Failing reason codes in the engine's fixed order.
    public let codes: [String]
    /// `messages[i]` explains `codes[i]`.
    public let messages: [String]
    /// The 12 exact features; `nil` when the recipe did not parse.
    public let features: [String]?
    public let nearestID: String?
    public let nearestIndex: Int?
    public let nearestDistance: Double?
    /// `nil` when the recipe did not parse or the waveform is unusable.
    public let pcmSHA256: String?
    public let profile: Profile?
    public let threshold: String?
    public let recipe: Recipe?
    public let recipeSHA256: String?
    public let eventSamples: [Int]?
    public let validatorVersion: String?
    public let rendererVersion: String?
    /// The full result object as sent.
    public let raw: JSONValue

    enum CodingKeys: String, CodingKey {
        case ok, codes, messages, features
        case nearestID = "nearest_id"
        case nearestIndex = "nearest_index"
        case nearestDistance = "nearest_distance"
        case pcmSHA256 = "pcm_sha256"
        case profile, threshold, recipe
        case recipeSHA256 = "recipe_sha256"
        case eventSamples = "event_samples"
        case validatorVersion = "validator_version"
        case rendererVersion = "renderer_version"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = try c.decode(Bool.self, forKey: .ok)
        codes = try c.decode([String].self, forKey: .codes)
        messages = try c.decode([String].self, forKey: .messages)
        features = try c.decodeIfPresent([String].self, forKey: .features)
        nearestID = try c.decodeIfPresent(String.self, forKey: .nearestID)
        nearestIndex = c.decodeLossy(Int.self, forKey: .nearestIndex)
        nearestDistance = try c.decodeIfPresent(Double.self, forKey: .nearestDistance)
        pcmSHA256 = try c.decodeIfPresent(String.self, forKey: .pcmSHA256)
        profile = c.decodeLossy(Profile.self, forKey: .profile)
        threshold = c.decodeLossy(String.self, forKey: .threshold)
        recipe = c.decodeLossy(Recipe.self, forKey: .recipe)
        recipeSHA256 = c.decodeLossy(String.self, forKey: .recipeSHA256)
        eventSamples = c.decodeLossy([Int].self, forKey: .eventSamples)
        validatorVersion = c.decodeLossy(String.self, forKey: .validatorVersion)
        rendererVersion = c.decodeLossy(String.self, forKey: .rendererVersion)
        raw = try JSONValue(from: decoder)
    }

    /// The first failing code, or `nil` when `ok`.
    public var primaryCode: String? { codes.first }

    /// `(code, message)` pairs for display.
    public var reasons: [(code: String, message: String)] {
        codes.enumerated().map { i, code in (code, i < messages.count ? messages[i] : "") }
    }
}

/// `nearest` (the call returns `nil` when nothing is committed).
public struct NearestResult: Decodable, Sendable, Hashable {
    public let refID: String
    public let index: Int
    public let distance: Double
    public let sumSq: String

    enum CodingKeys: String, CodingKey {
        case refID = "ref_id"
        case index, distance
        case sumSq = "sum_sq"
    }
}

// MARK: - grammar, compose, composite_hash, synthetic_book

/// `grammar`: atoms and the fixed 32-message matrix (18 trained, 14 held out).
public struct Grammar: Decodable, Sendable, Hashable {
    /// `families` exactly as sent (the protocol does not fix its shape).
    public let families: JSONValue
    public let atomIDs: [String]
    public let messages: [MessageRef]

    enum CodingKeys: String, CodingKey {
        case families
        case atomIDs = "atom_ids"
        case messages
    }

    /// Family IDs (`["K", "Q"]`) read from `families`: a list of strings, a list of
    /// objects with `id`/`family`, or an object keyed by family.
    public var familyIDs: [String] {
        switch families {
        case .array(let items):
            return items.compactMap { item in
                item.stringValue ?? item["id"]?.stringValue ?? item["family"]?.stringValue
            }
        case .object(let object):
            return object.keys.sorted()
        default:
            return []
        }
    }

    public var trainedMessages: [MessageRef] { messages.filter { !$0.isHeldout } }
    public var heldoutMessages: [MessageRef] { messages.filter(\.isHeldout) }
}

/// One legal message of the grammar.
public struct MessageRef: Decodable, Sendable, Hashable, Identifiable {
    public let messageID: String
    public let family: String
    /// The action atom (an atom ID such as `K-a1`).
    public let action: String
    /// The referent atom (an atom ID such as `K-r2`).
    public let referent: String
    /// Matrix cell: `Train V1`..`Train V3` or `H-V1`..`H-W4`.
    public let status: String
    public let trainingWave: Int?
    public let heldoutSet: String?
    public let isHeldout: Bool

    public var id: String { messageID }

    enum CodingKeys: String, CodingKey {
        case messageID = "message_id"
        case family, action, referent, status
        case trainingWave = "training_wave"
        case heldoutSet = "heldout_set"
        case isHeldout = "is_heldout"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        messageID = try c.decode(String.self, forKey: .messageID)
        family = try c.decode(String.self, forKey: .family)
        action = try c.decodeLenientString(forKey: .action)
        referent = try c.decodeLenientString(forKey: .referent)
        status = try c.decode(String.self, forKey: .status)
        trainingWave = c.decodeLossy(Int.self, forKey: .trainingWave)
        heldoutSet = c.decodeLossy(String.self, forKey: .heldoutSet)
        isHeldout = try c.decode(Bool.self, forKey: .isHeldout)
    }
}

/// `compose`: action + 9,600 zero samples + referent.
public struct ComposeResult: Decodable, Sendable, Hashable {
    public let messageID: String
    public let nSamples: Int
    public let durationS: Double
    public let actionSamples: Int
    public let referentSamples: Int
    public let referentOnset: Int
    public let audio: AudioPayload

    enum CodingKeys: String, CodingKey {
        case messageID = "message_id"
        case nSamples = "n_samples"
        case durationS = "duration_s"
        case actionSamples = "action_samples"
        case referentSamples = "referent_samples"
        case referentOnset = "referent_onset"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        messageID = try c.decode(String.self, forKey: .messageID)
        nSamples = try c.decode(Int.self, forKey: .nSamples)
        durationS = try c.decode(Double.self, forKey: .durationS)
        actionSamples = try c.decode(Int.self, forKey: .actionSamples)
        referentSamples = try c.decode(Int.self, forKey: .referentSamples)
        referentOnset = try c.decode(Int.self, forKey: .referentOnset)
        audio = try AudioPayload(from: decoder)
    }
}

/// `composite_hash` (held-out messages allowed; never audio).
public struct CompositeHashResult: Decodable, Sendable, Hashable {
    public let messageID: String
    public let compositeSHA256: String
    public let nSamples: Int
    public let durationS: Double
    public let isHeldout: Bool

    enum CodingKeys: String, CodingKey {
        case messageID = "message_id"
        case compositeSHA256 = "composite_sha256"
        case nSamples = "n_samples"
        case durationS = "duration_s"
        case isHeldout = "is_heldout"
    }
}

/// `synthetic_book`: the synthetic `DEMO-Pn` book.
public struct SyntheticBook: Decodable, Sendable, Hashable {
    public let bookID: String
    public let atoms: [SyntheticAtom]

    enum CodingKeys: String, CodingKey {
        case bookID = "book_id"
        case atoms
    }

    /// The atom with this ID (`K-a1`).
    public func atom(_ atomID: String) -> SyntheticAtom? { atoms.first { $0.atomID == atomID } }
}

public struct SyntheticAtom: Decodable, Sendable, Hashable, Identifiable {
    public let atomID: String
    public let recipe: Recipe
    public let pcmSHA256: String
    public let nSamples: Int

    public var id: String { atomID }

    /// This atom as an atom reference for `compose`, `validate` or `nearest`.
    public var reference: AtomReference { AtomReference(refID: atomID, recipe: recipe) }

    enum CodingKeys: String, CodingKey {
        case atomID = "atom_id"
        case recipe
        case pcmSHA256 = "pcm_sha256"
        case nSamples = "n_samples"
    }
}

// MARK: - nonlexical

/// One reserved nonlexical asset (calibration examples, READY cue, grammar clicks).
public struct NonlexicalAsset: Decodable, Sendable, Hashable, Identifiable {
    public let id: String
    public let kind: String
    /// `nil` for profile-independent assets.
    public let profile: Profile?
    public let nSamples: Int
    public let durationMs: Double
    public let peakDbfs: Double?
    public let rmsDbfs: Double?
    public let activeRmsDbfs: Double?
    public let pcmSHA256: String
    public let fileSHA256: String
    public let description: String

    enum CodingKeys: String, CodingKey {
        case id, kind, profile
        case nSamples = "n_samples"
        case durationMs = "duration_ms"
        case peakDbfs = "peak_dbfs"
        case rmsDbfs = "rms_dbfs"
        case activeRmsDbfs = "active_rms_dbfs"
        case pcmSHA256 = "pcm_sha256"
        case fileSHA256 = "file_sha256"
        case description
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        kind = try c.decode(String.self, forKey: .kind)
        profile = try c.decodeIfPresent(Profile.self, forKey: .profile)
        nSamples = try c.decode(Int.self, forKey: .nSamples)
        durationMs = try c.decode(Double.self, forKey: .durationMs)
        peakDbfs = c.decodeLossy(Double.self, forKey: .peakDbfs)
        rmsDbfs = c.decodeLossy(Double.self, forKey: .rmsDbfs)
        activeRmsDbfs = c.decodeLossy(Double.self, forKey: .activeRmsDbfs)
        pcmSHA256 = try c.decode(String.self, forKey: .pcmSHA256)
        fileSHA256 = try c.decode(String.self, forKey: .fileSHA256)
        description = try c.decode(String.self, forKey: .description)
    }
}

/// `nonlexical_list`.
public struct NonlexicalList: Decodable, Sendable, Hashable {
    public let assets: [NonlexicalAsset]
}

/// `nonlexical_get`: the asset fields plus the audio fields.
public struct NonlexicalAssetAudio: Decodable, Sendable, Hashable {
    public let asset: NonlexicalAsset
    public let audio: AudioPayload

    public init(from decoder: any Decoder) throws {
        asset = try NonlexicalAsset(from: decoder)
        audio = try AudioPayload(from: decoder)
    }
}

// MARK: - vectors_check, golden_check

public struct VectorsSection: Decodable, Sendable, Hashable {
    public let checked: Int
    public let mismatches: [JSONValue]
}

/// `vectors_check`: the reference vectors re-rendered on this machine.
public struct VectorsCheck: Decodable, Sendable, Hashable {
    public let renderer: VectorsSection
    public let composition: VectorsSection
    public let ok: Bool
}

/// `golden_check`: the golden manifest recomputed on this machine.
public struct GoldenCheck: Decodable, Sendable, Hashable {
    /// Number of golden items (the count, or the length when a list is sent).
    public let items: Int
    public let mismatches: [JSONValue]
    public let ok: Bool
    /// The digest (when an object of digests is sent: its `all` entry).
    public let digest: String
    public let raw: JSONValue

    enum CodingKeys: String, CodingKey { case items, mismatches, ok, digest }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let itemsValue = try c.decode(JSONValue.self, forKey: .items)
        if let count = itemsValue.intValue {
            items = count
        } else if let list = itemsValue.arrayValue {
            items = list.count
        } else {
            throw DecodingError.typeMismatch(
                Int.self,
                .init(codingPath: c.codingPath + [CodingKeys.items], debugDescription: "expected a count or a list"))
        }
        mismatches = try c.decode([JSONValue].self, forKey: .mismatches)
        ok = try c.decode(Bool.self, forKey: .ok)
        let digestValue = try c.decode(JSONValue.self, forKey: .digest)
        digest = digestValue.stringValue ?? digestValue["all"]?.stringValue ?? digestValue.compactString
        raw = try JSONValue(from: decoder)
    }
}

// MARK: - store

/// `store_reset`.
public struct StoreResetResult: Decodable, Sendable, Hashable {
    /// The bridge's fresh temp directory (outside any git work tree).
    public let root: String
}

/// `store_create`.
public struct StoreCreateResult: Decodable, Sendable, Hashable {
    public let bookID: String
    public let chainHead: String

    enum CodingKeys: String, CodingKey {
        case bookID = "book_id"
        case chainHead = "chain_head"
    }
}

/// One committed atom of a store book.
public struct StoreEntry: Decodable, Sendable, Hashable, Identifiable {
    public let atomID: String
    public let commitIndex: Int
    public let pcmSHA256: String
    public let fileSHA256: String
    public let recipe: Recipe

    public var id: String { atomID }

    /// This entry as an atom reference.
    public var reference: AtomReference { AtomReference(refID: atomID, recipe: recipe) }

    enum CodingKeys: String, CodingKey {
        case atomID = "atom_id"
        case commitIndex = "commit_index"
        case pcmSHA256 = "pcm_sha256"
        case fileSHA256 = "file_sha256"
        case recipe
    }
}

/// `store_commit`.
public struct StoreCommitResult: Decodable, Sendable, Hashable {
    public let entry: StoreEntry
    public let chainHead: String
    /// `"commit"` or `"recommit_noop"`.
    public let outcome: String

    public var isNoop: Bool { outcome == "recommit_noop" }

    enum CodingKeys: String, CodingKey {
        case entry
        case chainHead = "chain_head"
        case outcome
    }
}

/// `store_list`.
public struct StoreListResult: Decodable, Sendable, Hashable {
    public let entries: [StoreEntry]
    public let chainHead: String
    public let isFrozen: Bool
    public let isVoid: Bool

    enum CodingKeys: String, CodingKey {
        case entries
        case chainHead = "chain_head"
        case isFrozen = "frozen"
        case isVoid = "void"
    }
}

/// `store_records`: the parsed log records, in order, of a book that passes the integrity
/// checks (a damaged book is refused with `StoreIntegrityError` / `E_INTEGRITY`).
public struct StoreRecordsResult: Decodable, Sendable, Hashable {
    public let records: [JSONValue]
}

/// `store_freeze`.
public struct StoreFreezeResult: Decodable, Sendable, Hashable {
    public let chainHead: String

    enum CodingKeys: String, CodingKey { case chainHead = "chain_head" }
}

/// One integrity problem found by `store_verify`.
public struct StoreIssue: Decodable, Sendable, Hashable {
    public let code: String
    /// The log line, when the problem has one (`line`, or the engine's `seq`).
    public let line: Int?
    public let message: String

    enum CodingKeys: String, CodingKey { case code, line, seq, message }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        code = try c.decode(String.self, forKey: .code)
        line = c.decodeLossy(Int.self, forKey: .line) ?? c.decodeLossy(Int.self, forKey: .seq)
        message = (try? c.decode(String.self, forKey: .message)) ?? ""
    }
}

/// `store_verify`.
public struct StoreVerifyResult: Decodable, Sendable, Hashable {
    public let ok: Bool
    public let issues: [StoreIssue]
}

/// `store_tamper` kinds (demo only).
public enum StoreTamperKind: String, Codable, Sendable, Hashable, CaseIterable, Identifiable {
    case flipBlobByte = "flip_blob_byte"
    case editLogLine = "edit_log_line"
    case truncateLog = "truncate_log"

    public var id: String { rawValue }
}

/// `store_tamper`.
public struct StoreTamperResult: Decodable, Sendable, Hashable {
    public let done: String
}

// MARK: - fallback

public struct FallbackBankEntry: Decodable, Sendable, Hashable, Identifiable {
    public let index: Int
    public let recipe: Recipe
    public let pcmSHA256: String

    public var id: Int { index }

    enum CodingKeys: String, CodingKey {
        case index, recipe
        case pcmSHA256 = "pcm_sha256"
    }
}

public struct FallbackBookAtom: Decodable, Sendable, Hashable, Identifiable {
    public let atomID: String
    public let recipe: Recipe
    public let pcmSHA256: String

    public var id: String { atomID }

    public var reference: AtomReference { AtomReference(refID: atomID, recipe: recipe) }

    enum CodingKeys: String, CodingKey {
        case atomID = "atom_id"
        case recipe
        case pcmSHA256 = "pcm_sha256"
    }
}

/// `fallback_demo`: the bank (64) and book (16) of the public demo seed.
public struct FallbackDemo: Decodable, Sendable, Hashable {
    public let seedLabel: String
    public let fallbackBankHash: String
    public let bank: [FallbackBankEntry]
    public let book: [FallbackBookAtom]

    enum CodingKeys: String, CodingKey {
        case seedLabel = "seed_label"
        case fallbackBankHash = "fallback_bank_hash"
        case bank, book
    }
}

/// One step of a fallback scan log.
public struct ScanStep: Decodable, Sendable, Hashable {
    public let index: Int
    /// `used`, `rejected` or `selected`.
    public let outcome: String
    public let codes: [String]
    public let messages: [String]
    public let recipeSHA256: String?
    public let pcmSHA256: String?

    enum CodingKeys: String, CodingKey {
        case index, outcome, codes, messages
        case recipeSHA256 = "recipe_sha256"
        case pcmSHA256 = "pcm_sha256"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        index = try c.decode(Int.self, forKey: .index)
        outcome = try c.decode(String.self, forKey: .outcome)
        codes = c.decodeLossy([String].self, forKey: .codes) ?? []
        messages = c.decodeLossy([String].self, forKey: .messages) ?? []
        recipeSHA256 = c.decodeLossy(String.self, forKey: .recipeSHA256)
        pcmSHA256 = c.decodeLossy(String.self, forKey: .pcmSHA256)
    }
}

/// `fallback_scan`: the engine's `ScanResult.to_dict()` (`fallback-scan.schema.json`).
/// The documented fields are read leniently; `raw` holds the full record.
public struct ScanResult: Decodable, Sendable, Hashable {
    public let raw: JSONValue
    /// `selected` or `exhausted`.
    public let outcome: String?
    public let selectedIndex: Int?
    public let selectedSource: String?
    public let selectedRecipeSHA256: String?
    public let selectedPcmSHA256: String?
    public let used: [Int]
    public let referenceIDs: [String]
    public let log: [ScanStep]

    /// True when no bank recipe passed (the whole-book fallback applies).
    public var isExhausted: Bool { outcome == "exhausted" }

    enum CodingKeys: String, CodingKey {
        case outcome
        case selectedIndex = "selected_index"
        case selectedSource = "selected_source"
        case selectedRecipeSHA256 = "selected_recipe_sha256"
        case selectedPcmSHA256 = "selected_pcm_sha256"
        case used
        case referenceIDs = "reference_ids"
        case log
    }

    public init(from decoder: any Decoder) throws {
        raw = try JSONValue(from: decoder)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        outcome = c.decodeLossy(String.self, forKey: .outcome)
        selectedIndex = c.decodeLossy(Int.self, forKey: .selectedIndex)
        selectedSource = c.decodeLossy(String.self, forKey: .selectedSource)
        selectedRecipeSHA256 = c.decodeLossy(String.self, forKey: .selectedRecipeSHA256)
        selectedPcmSHA256 = c.decodeLossy(String.self, forKey: .selectedPcmSHA256)
        used = c.decodeLossy([Int].self, forKey: .used) ?? []
        referenceIDs = c.decodeLossy([String].self, forKey: .referenceIDs) ?? []
        log = c.decodeLossy([ScanStep].self, forKey: .log) ?? []
    }
}

// MARK: - package_demo

public struct PackageFile: Decodable, Sendable, Hashable, Identifiable {
    public let path: String
    public let sha256: String
    public let bytes: Int

    public var id: String { path }
}

public struct PackageCounts: Decodable, Sendable, Hashable {
    public let atomWavs: Int
    public let messageWavs: Int
    public let heldoutIDs: Int

    enum CodingKeys: String, CodingKey {
        case atomWavs = "atom_wavs"
        case messageWavs = "message_wavs"
        case heldoutIDs = "heldout_ids"
    }
}

/// `package_demo`: a synthetic package built, loaded and leak-scanned in a temp directory.
public struct PackageDemo: Decodable, Sendable, Hashable {
    public let packageSHA256: String
    public let files: [PackageFile]
    public let counts: PackageCounts
    public let loaderOK: Bool
    public let leakReport: JSONValue
    public let answersPreview: [JSONValue]
    /// Temp directory of the package (outside any git work tree).
    public let dir: String

    /// `leak_report.ok`, when present.
    public var leakReportOK: Bool? { leakReport["ok"]?.boolValue }

    enum CodingKeys: String, CodingKey {
        case packageSHA256 = "package_sha256"
        case files, counts
        case loaderOK = "loader_ok"
        case leakReport = "leak_report"
        case answersPreview = "answers_preview"
        case dir
    }
}
