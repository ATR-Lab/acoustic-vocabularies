import Foundation

// Request argument shapes of PROTOCOL.md ("Commands"). Optional arguments are left out
// of the request when `nil`, so the bridge applies its documented default.

/// `{}`.
public struct EmptyArgs: Encodable, Sendable, Hashable {
    public init() {}
}

/// An atom reference: `{"ref_id": "K-a1", "recipe": {...}}`. The bridge renders the
/// recipe itself; the client never sends audio.
public struct AtomReference: Codable, Sendable, Hashable, Identifiable {
    public var refID: String
    public var recipe: Recipe

    public var id: String { refID }

    public init(refID: String, recipe: Recipe) {
        self.refID = refID
        self.recipe = recipe
    }

    enum CodingKeys: String, CodingKey {
        case refID = "ref_id"
        case recipe
    }
}

/// The `candidate` of `validate`: a recipe object, raw JSON text (checked by the engine
/// for `E_JSON`), or any JSON value (to show `E_SCHEMA` and `E_DOMAIN`).
public enum ValidationCandidate: Encodable, Sendable, Hashable {
    case recipe(Recipe)
    case text(String)
    case json(JSONValue)

    public func encode(to encoder: any Encoder) throws {
        switch self {
        case .recipe(let recipe): try recipe.encode(to: encoder)
        case .text(let text):
            var container = encoder.singleValueContainer()
            try container.encode(text)
        case .json(let value): try value.encode(to: encoder)
        }
    }
}

public struct RenderArgs: Encodable, Sendable, Hashable {
    public var recipe: Recipe
    public var profile: Profile

    public init(recipe: Recipe, profile: Profile) {
        self.recipe = recipe
        self.profile = profile
    }
}

public struct RandomRecipeArgs: Encodable, Sendable, Hashable {
    public var seed: Int
    public var admissibleOnly: Bool

    public init(seed: Int, admissibleOnly: Bool = true) {
        self.seed = seed
        self.admissibleOnly = admissibleOnly
    }

    enum CodingKeys: String, CodingKey {
        case seed
        case admissibleOnly = "admissible_only"
    }
}

public struct RecipeArgs: Encodable, Sendable, Hashable {
    public var recipe: Recipe

    public init(recipe: Recipe) { self.recipe = recipe }
}

public struct DistanceArgs: Encodable, Sendable, Hashable {
    public var a: Recipe
    public var b: Recipe

    public init(a: Recipe, b: Recipe) {
        self.a = a
        self.b = b
    }
}

/// The text of a threshold argument (`validate`, `store_create`). PROTOCOL.md allows only
/// a plain non-negative decimal (`0.1`, `0.10`, `1`), the engine's `parse_threshold`
/// pattern `(0|[1-9][0-9]*)(\.[0-9]+)?`. Fractions such as `1/10` appear only in results.
public enum ThresholdText {
    public static let hint = "a non-negative decimal such as 0.1"

    public static func isValid(_ text: String) -> Bool {
        let parts = text.split(separator: ".", maxSplits: 1, omittingEmptySubsequences: false)
        func digits(_ part: Substring) -> Bool {
            !part.isEmpty && part.utf8.allSatisfy { $0 >= UInt8(ascii: "0") && $0 <= UInt8(ascii: "9") }
        }
        guard let whole = parts.first, digits(whole), whole == "0" || !whole.hasPrefix("0") else { return false }
        return parts.count == 1 || digits(parts[1])
    }
}

public struct ValidateArgs: Encodable, Sendable, Hashable {
    public var candidate: ValidationCandidate
    public var profile: Profile
    public var committed: [AtomReference]?
    /// Exact threshold text, a plain decimal such as `"0.10"` (`ThresholdText`); `nil`
    /// uses the configured threshold.
    public var threshold: String?
    public var useReserved: Bool

    public init(
        candidate: ValidationCandidate, profile: Profile, committed: [AtomReference]? = nil,
        threshold: String? = nil, useReserved: Bool = true
    ) {
        self.candidate = candidate
        self.profile = profile
        self.committed = committed
        self.threshold = threshold
        self.useReserved = useReserved
    }

    enum CodingKeys: String, CodingKey {
        case candidate, profile, committed, threshold
        case useReserved = "use_reserved"
    }
}

public struct NearestArgs: Encodable, Sendable, Hashable {
    public var candidate: Recipe
    public var committed: [AtomReference]
    public var profile: Profile

    public init(candidate: Recipe, committed: [AtomReference], profile: Profile) {
        self.candidate = candidate
        self.committed = committed
        self.profile = profile
    }
}

/// Arguments of `compose` and `composite_hash`.
public struct ComposeArgs: Encodable, Sendable, Hashable {
    public var action: AtomReference
    public var referent: AtomReference
    public var profile: Profile
    public var bookID: String?

    public init(action: AtomReference, referent: AtomReference, profile: Profile, bookID: String? = nil) {
        self.action = action
        self.referent = referent
        self.profile = profile
        self.bookID = bookID
    }

    enum CodingKeys: String, CodingKey {
        case action, referent, profile
        case bookID = "book_id"
    }
}

public struct ProfileArgs: Encodable, Sendable, Hashable {
    public var profile: Profile

    public init(profile: Profile) { self.profile = profile }
}

public struct IDArgs: Encodable, Sendable, Hashable {
    public var id: String

    public init(id: String) { self.id = id }
}

public struct StoreCreateArgs: Encodable, Sendable, Hashable {
    /// Must start with `DEMO-`.
    public var bookID: String
    public var profile: Profile
    public var threshold: String?

    public init(bookID: String, profile: Profile, threshold: String? = nil) {
        self.bookID = bookID
        self.profile = profile
        self.threshold = threshold
    }

    enum CodingKeys: String, CodingKey {
        case bookID = "book_id"
        case profile, threshold
    }
}

public struct StoreCommitArgs: Encodable, Sendable, Hashable {
    public var bookID: String
    public var atomID: String
    /// Sent as `null` when `nil`: the key is required (an absent key is `E_BAD_REQUEST`).
    public var semanticLabel: String?
    public var recipe: Recipe

    public init(bookID: String, atomID: String, semanticLabel: String?, recipe: Recipe) {
        self.bookID = bookID
        self.atomID = atomID
        self.semanticLabel = semanticLabel
        self.recipe = recipe
    }

    enum CodingKeys: String, CodingKey {
        case bookID = "book_id"
        case atomID = "atom_id"
        case semanticLabel = "semantic_label"
        case recipe
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(bookID, forKey: .bookID)
        try c.encode(atomID, forKey: .atomID)
        try c.encode(semanticLabel, forKey: .semanticLabel)
        try c.encode(recipe, forKey: .recipe)
    }
}

public struct BookArgs: Encodable, Sendable, Hashable {
    public var bookID: String

    public init(bookID: String) { self.bookID = bookID }

    enum CodingKeys: String, CodingKey { case bookID = "book_id" }
}

public struct StoreVerifyArgs: Encodable, Sendable, Hashable {
    public var bookID: String
    public var expectedHead: String?

    public init(bookID: String, expectedHead: String? = nil) {
        self.bookID = bookID
        self.expectedHead = expectedHead
    }

    enum CodingKeys: String, CodingKey {
        case bookID = "book_id"
        case expectedHead = "expected_head"
    }
}

public struct StoreTamperArgs: Encodable, Sendable, Hashable {
    public var bookID: String
    public var kind: StoreTamperKind

    public init(bookID: String, kind: StoreTamperKind) {
        self.bookID = bookID
        self.kind = kind
    }

    enum CodingKeys: String, CodingKey {
        case bookID = "book_id"
        case kind
    }
}

public struct FallbackScanArgs: Encodable, Sendable, Hashable {
    public var profile: Profile
    public var book: [AtomReference]
    public var used: [Int]?

    public init(profile: Profile, book: [AtomReference], used: [Int]? = nil) {
        self.profile = profile
        self.book = book
        self.used = used
    }
}
