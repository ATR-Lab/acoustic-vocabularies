import Foundation

/// Typed calls for every command of PROTOCOL.md. Each takes an optional timeout; the
/// defaults allow for the slower checks (golden set, fallback banks, package build).
extension BridgeClient {
    public static let slowTimeout: Duration = .seconds(600)

    public nonisolated func hello(timeout: Duration = BridgeClient.defaultTimeout) async throws -> Hello {
        try await call("hello", EmptyArgs(), timeout: timeout)
    }

    public nonisolated func selfTest(timeout: Duration = .seconds(60)) async throws -> SelfTestResult {
        try await call("self_test", EmptyArgs(), timeout: timeout)
    }

    public nonisolated func render(
        _ recipe: Recipe, profile: Profile, timeout: Duration = BridgeClient.defaultTimeout
    ) async throws -> RenderResult {
        try await call("render", RenderArgs(recipe: recipe, profile: profile), timeout: timeout)
    }

    /// A deterministic recipe for `seed` (with `admissibleOnly`, every event is at least
    /// 2,880 samples).
    public nonisolated func randomRecipe(
        seed: Int, admissibleOnly: Bool = true, timeout: Duration = BridgeClient.defaultTimeout
    ) async throws -> Recipe {
        let result: RandomRecipeResult = try await call(
            "random_recipe", RandomRecipeArgs(seed: seed, admissibleOnly: admissibleOnly), timeout: timeout)
        return result.recipe
    }

    public nonisolated func features(
        _ recipe: Recipe, timeout: Duration = BridgeClient.defaultTimeout
    ) async throws -> FeaturesResult {
        try await call("features", RecipeArgs(recipe: recipe), timeout: timeout)
    }

    public nonisolated func distance(
        _ a: Recipe, _ b: Recipe, timeout: Duration = BridgeClient.defaultTimeout
    ) async throws -> DistanceResult {
        try await call("distance", DistanceArgs(a: a, b: b), timeout: timeout)
    }

    /// The engine's admissibility check. A rejection is a normal result (`ok == false`).
    public nonisolated func validate(
        _ candidate: ValidationCandidate, profile: Profile, committed: [AtomReference]? = nil,
        threshold: String? = nil, useReserved: Bool = true, timeout: Duration = .seconds(60)
    ) async throws -> ValidationResult {
        try await call(
            "validate",
            ValidateArgs(
                candidate: candidate, profile: profile, committed: committed, threshold: threshold,
                useReserved: useReserved),
            timeout: timeout)
    }

    /// The nearest committed reference, or `nil` when `committed` is empty.
    public nonisolated func nearest(
        _ candidate: Recipe, committed: [AtomReference], profile: Profile,
        timeout: Duration = .seconds(60)
    ) async throws -> NearestResult? {
        try await call(
            "nearest", NearestArgs(candidate: candidate, committed: committed, profile: profile), timeout: timeout)
    }

    public nonisolated func grammar(timeout: Duration = BridgeClient.defaultTimeout) async throws -> Grammar {
        try await call("grammar", EmptyArgs(), timeout: timeout)
    }

    /// Composes a trained message. A held-out message throws
    /// `BridgeError.engine(type: "HeldOutMessageError", code: "E_HELDOUT", ...)`.
    public nonisolated func compose(
        action: AtomReference, referent: AtomReference, profile: Profile, bookID: String? = nil,
        timeout: Duration = .seconds(60)
    ) async throws -> ComposeResult {
        try await call(
            "compose", ComposeArgs(action: action, referent: referent, profile: profile, bookID: bookID),
            timeout: timeout)
    }

    /// The expected message hash (held-out messages included); never audio.
    public nonisolated func compositeHash(
        action: AtomReference, referent: AtomReference, profile: Profile, bookID: String? = nil,
        timeout: Duration = .seconds(60)
    ) async throws -> CompositeHashResult {
        try await call(
            "composite_hash", ComposeArgs(action: action, referent: referent, profile: profile, bookID: bookID),
            timeout: timeout)
    }

    public nonisolated func syntheticBook(
        profile: Profile, timeout: Duration = .seconds(120)
    ) async throws -> SyntheticBook {
        try await call("synthetic_book", ProfileArgs(profile: profile), timeout: timeout)
    }

    public nonisolated func nonlexicalList(timeout: Duration = .seconds(60)) async throws -> [NonlexicalAsset] {
        let result: NonlexicalList = try await call("nonlexical_list", EmptyArgs(), timeout: timeout)
        return result.assets
    }

    public nonisolated func nonlexicalGet(
        id: String, timeout: Duration = .seconds(60)
    ) async throws -> NonlexicalAssetAudio {
        try await call("nonlexical_get", IDArgs(id: id), timeout: timeout)
    }

    public nonisolated func vectorsCheck(timeout: Duration = BridgeClient.slowTimeout) async throws -> VectorsCheck {
        try await call("vectors_check", EmptyArgs(), timeout: timeout)
    }

    public nonisolated func goldenCheck(timeout: Duration = BridgeClient.slowTimeout) async throws -> GoldenCheck {
        try await call("golden_check", EmptyArgs(), timeout: timeout)
    }

    public nonisolated func storeReset(timeout: Duration = BridgeClient.defaultTimeout) async throws -> StoreResetResult {
        try await call("store_reset", EmptyArgs(), timeout: timeout)
    }

    /// Creates a store book; `bookID` must start with `DEMO-`.
    public nonisolated func storeCreate(
        bookID: String, profile: Profile, threshold: String? = nil,
        timeout: Duration = BridgeClient.defaultTimeout
    ) async throws -> StoreCreateResult {
        try await call(
            "store_create", StoreCreateArgs(bookID: bookID, profile: profile, threshold: threshold), timeout: timeout)
    }

    /// Commits an atom. An overwrite throws `OverwriteRejected`; an inadmissible recipe
    /// throws `CommitRejected` (see `BridgeError.validationDetails`).
    public nonisolated func storeCommit(
        bookID: String, atomID: String, semanticLabel: String?, recipe: Recipe,
        timeout: Duration = .seconds(60)
    ) async throws -> StoreCommitResult {
        try await call(
            "store_commit",
            StoreCommitArgs(bookID: bookID, atomID: atomID, semanticLabel: semanticLabel, recipe: recipe),
            timeout: timeout)
    }

    public nonisolated func storeList(
        bookID: String, timeout: Duration = BridgeClient.defaultTimeout
    ) async throws -> StoreListResult {
        try await call("store_list", BookArgs(bookID: bookID), timeout: timeout)
    }

    public nonisolated func storeRecords(
        bookID: String, timeout: Duration = BridgeClient.defaultTimeout
    ) async throws -> [JSONValue] {
        let result: StoreRecordsResult = try await call("store_records", BookArgs(bookID: bookID), timeout: timeout)
        return result.records
    }

    public nonisolated func storeFreeze(
        bookID: String, timeout: Duration = BridgeClient.defaultTimeout
    ) async throws -> StoreFreezeResult {
        try await call("store_freeze", BookArgs(bookID: bookID), timeout: timeout)
    }

    public nonisolated func storeVerify(
        bookID: String, expectedHead: String? = nil, timeout: Duration = .seconds(60)
    ) async throws -> StoreVerifyResult {
        try await call(
            "store_verify", StoreVerifyArgs(bookID: bookID, expectedHead: expectedHead), timeout: timeout)
    }

    /// Demo only: damages the bridge's temp store so `storeVerify` can show detection.
    public nonisolated func storeTamper(
        bookID: String, kind: StoreTamperKind, timeout: Duration = BridgeClient.defaultTimeout
    ) async throws -> StoreTamperResult {
        try await call("store_tamper", StoreTamperArgs(bookID: bookID, kind: kind), timeout: timeout)
    }

    public nonisolated func fallbackDemo(
        profile: Profile, timeout: Duration = BridgeClient.slowTimeout
    ) async throws -> FallbackDemo {
        try await call("fallback_demo", ProfileArgs(profile: profile), timeout: timeout)
    }

    public nonisolated func fallbackScan(
        profile: Profile, book: [AtomReference], used: [Int]? = nil, timeout: Duration = .seconds(300)
    ) async throws -> ScanResult {
        try await call("fallback_scan", FallbackScanArgs(profile: profile, book: book, used: used), timeout: timeout)
    }

    public nonisolated func packageDemo(timeout: Duration = BridgeClient.slowTimeout) async throws -> PackageDemo {
        try await call("package_demo", EmptyArgs(), timeout: timeout)
    }
}
