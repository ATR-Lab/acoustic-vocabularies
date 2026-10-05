import AVSoundDemoCore
import AVSoundSpec
import Foundation

/// `AVSoundDemo --self-check [--repo <path>] [--uv <path>] [--count <n>]`
///
/// Runs without UI: starts the bridge, checks the engine end to end and the Swift port
/// against it, shuts the bridge down, prints a JSON summary on stdout (progress goes to
/// stderr) and exits with `exitStatus`: 0 when every check passed, 1 when an engine check
/// failed, 2 when the bridge could not run (the repository or uv was not found, or the
/// bridge did not start and answer `hello` with a supported `bridge_version`).
enum SelfCheck {
    static let flag = "--self-check"
    /// pcm_sha256 of the renderer spec's worked example at P2 (renderer 0.1.0).
    static let workedExamplePCM = "4c0467de354c076c0b30bc9af794e31384afdf161af24605fb621cc455c35d87"
    /// Event 1 has 1,760 samples (< 2,880): E_EVENT_SHORT.
    static let shortEventRecipe = Recipe(
        totalMs: 450, pitches: [0, 0, 0], rhythmWeights: [1, 4, 4], gapsMs: [60, 60], amplitudes: [1.0, 0.8, 0.6])

    struct Options: Sendable {
        var repo: String?
        var uv: String?
        var conformanceCount = 10

        init(arguments: [String]) {
            var i = 0
            while i < arguments.count {
                let argument = arguments[i]
                let next = i + 1 < arguments.count ? arguments[i + 1] : nil
                switch argument {
                case "--repo":
                    repo = next
                    i += 1
                case "--uv":
                    uv = next
                    i += 1
                case "--count":
                    conformanceCount = next.flatMap(Int.init) ?? conformanceCount
                    i += 1
                default:
                    if argument.hasPrefix("--repo=") {
                        repo = String(argument.dropFirst("--repo=".count))
                    } else if argument.hasPrefix("--uv=") {
                        uv = String(argument.dropFirst("--uv=".count))
                    } else if argument.hasPrefix("--count="), let count = Int(argument.dropFirst("--count=".count)) {
                        conformanceCount = count
                    }
                }
                i += 1
            }
            conformanceCount = max(1, conformanceCount)
        }
    }

    struct Report {
        private(set) var checks: [JSONValue] = []
        private(set) var passed = 0
        private(set) var failed = 0

        var allOK: Bool { failed == 0 && passed > 0 }

        mutating func record(_ name: String, ok: Bool, seconds: Double = 0, _ detail: JSONValue) {
            checks.append(["name": .string(name), "ok": .bool(ok), "seconds": .double(rounded(seconds)), "detail": detail])
            if ok { passed += 1 } else { failed += 1 }
            SelfCheck.progress("\(ok ? "PASS" : "FAIL") \(name)")
        }

        mutating func check(_ name: String, _ body: () async throws -> (Bool, JSONValue)) async {
            SelfCheck.progress("\u{2026} \(name)")
            let clock = ContinuousClock()
            let start = clock.now
            do {
                let (ok, detail) = try await body()
                record(name, ok: ok, seconds: seconds(start.duration(to: clock.now)), detail)
            } catch {
                record(name, ok: false, seconds: seconds(start.duration(to: clock.now)), ["error": .string(userMessage(error))])
            }
        }

        private func seconds(_ duration: Duration) -> Double {
            Double(duration.components.seconds) + Double(duration.components.attoseconds) / 1e18
        }

        private func rounded(_ value: Double) -> Double { (value * 1_000).rounded() / 1_000 }
    }

    /// The exit status: 2 without a started bridge (no `hello`), else 0 or 1.
    static func exitStatus(bridgeStarted: Bool, allOK: Bool) -> Int32 {
        guard bridgeStarted else { return 2 }
        return allOK ? 0 : 1
    }

    /// The verdict of `swift_conformance`: every recipe checked and identical, and the
    /// four AVSoundSpec tables equal to the renderer spec's D4 digests. The random recipes
    /// reach only a few table entries, so a wrong entry elsewhere shows only in the
    /// digests.
    static func conformancePassed(checked: Int, expected: Int, mismatches: Int, tableDigestsMatch: Bool) -> Bool {
        checked == expected && mismatches == 0 && tableDigestsMatch
    }

    static func progress(_ text: String) {
        FileHandle.standardError.write(Data("self-check: \(text)\n".utf8))
    }

    static func run(_ options: Options) async -> Int32 {
        let clock = ContinuousClock()
        let started = clock.now
        var report = Report()

        // Setup: an explicit --repo/--uv must be valid; otherwise search as the app does.
        let configuration: ProcessBridgeTransport.Configuration
        do {
            configuration = try locate(options)
        } catch {
            report.record("locate", ok: false, ["error": .string(userMessage(error))])
            emit(report, seconds: started.duration(to: clock.now), hello: nil)
            return exitStatus(bridgeStarted: false, allOK: false)
        }
        report.record("locate", ok: true, [
            "repo": .string(configuration.currentDirectoryURL?.path ?? ""),
            "uv": .string(configuration.executableURL.path),
        ])

        let client = BridgeClient(configuration: configuration)
        var hello: Hello?
        await report.check("bridge_start") {
            let started = try await client.start()
            hello = started
            return (started.bridgeVersion == BridgeClient.supportedBridgeVersion, [
                "bridge_version": .int(started.bridgeVersion),
                "renderer_version": .string(started.rendererVersion),
                "validator_version": .string(started.validatorVersion),
                "python": .string(started.python),
                "numpy": .string(started.numpy),
            ])
        }
        if hello != nil {
            await engineChecks(&report, client: client, conformanceCount: options.conformanceCount)
        }

        await report.check("shutdown") {
            await client.stop()
            let status = await client.status
            return (status == .stopped, ["status": .string(status.label)])
        }
        emit(report, seconds: started.duration(to: clock.now), hello: hello)
        return exitStatus(bridgeStarted: hello != nil, allOK: report.allOK)
    }

    /// The launch configuration: a valid `--repo` first, else `AV_SOUND_REPO` (a value
    /// that is not the repository fails `locate`, exit 2, and no other checkout is
    /// checked in its place), else the app's search.
    static func locate(
        _ options: Options, environment: [String: String] = ProcessInfo.processInfo.environment
    ) throws -> ProcessBridgeTransport.Configuration {
        var repoURL: URL?
        if let repo = options.repo {
            let url = URL(fileURLWithPath: repo.expandingTilde).standardizedFileURL
            guard RepoLocator.isValidRepo(url) else {
                throw AppError("--repo \(repo) is not the repository (it must contain \(RepoLocator.requiredFiles.joined(separator: " and ")))")
            }
            repoURL = url
        }
        if let uv = options.uv, !isExecutableFile(uv.expandingTilde) {
            throw AppError("--uv \(uv) is not an executable file")
        }
        let savedUV = options.uv == nil ? UserDefaults.standard.string(forKey: AppModel.uvDefaultsKey) : nil
        return try ProcessBridgeTransport.Configuration.locate(
            explicitUV: options.uv?.expandingTilde ?? savedUV, explicitRepo: repoURL, environment: environment)
    }

    private static func engineChecks(_ report: inout Report, client: BridgeClient, conformanceCount: Int) async {
        await report.check("self_test") {
            let result = try await client.selfTest()
            return (result.ok, ["message": .string(result.message)])
        }

        await report.check("render_worked_example") {
            let result = try await client.render(.example, profile: .p2)
            let audio = try result.audio.verified()
            let ok = audio.pcmSHA256 == workedExamplePCM && result.recipeSHA256 == Recipe.example.sha256
                && audio.nSamples == result.nSamples && !result.shortEvent && !result.overflow
            return (ok, [
                "recipe": .string(Recipe.example.canonicalJSON),
                "profile": "P2",
                "pcm_sha256": .string(audio.pcmSHA256),
                "expected_pcm_sha256": .string(workedExamplePCM),
                "file_sha256": .string(audio.fileSHA256),
                "n_samples": .int(audio.nSamples),
                "recipe_sha256_matches_swift": .bool(result.recipeSHA256 == Recipe.example.sha256),
            ])
        }

        await report.check("validate_admissible") {
            let result = try await client.validate(.recipe(.example), profile: .p2)
            return (result.ok && result.codes.isEmpty, [
                "ok": .bool(result.ok), "codes": .array(result.codes.map(JSONValue.string)),
            ])
        }

        await report.check("validate_short_event") {
            let result = try await client.validate(.recipe(shortEventRecipe), profile: .p2)
            return (!result.ok && result.codes.contains("E_EVENT_SHORT"), [
                "ok": .bool(result.ok),
                "codes": .array(result.codes.map(JSONValue.string)),
                "event_samples": .array((result.eventSamples ?? []).map(JSONValue.int)),
            ])
        }

        var grammar: Grammar?
        var book: SyntheticBook?
        await report.check("compose_trained") {
            let loadedGrammar = try await client.grammar()
            let loadedBook = try await client.syntheticBook(profile: .p1)
            grammar = loadedGrammar
            book = loadedBook
            guard let message = loadedGrammar.trainedMessages.first,
                let action = loadedBook.atom(message.action), let referent = loadedBook.atom(message.referent)
            else { throw AppError("no trained message with both atoms in \(loadedBook.bookID)") }
            let composed = try await client.compose(
                action: action.reference, referent: referent.reference, profile: .p1, bookID: loadedBook.bookID)
            let audio = try composed.audio.verified()
            let expected = try await client.compositeHash(
                action: action.reference, referent: referent.reference, profile: .p1, bookID: loadedBook.bookID)
            let gap = composed.referentOnset - composed.actionSamples
            let ok = audio.nSamples == composed.nSamples && gap == SpecComposer.gapSamples
                && expected.compositeSHA256 == audio.pcmSHA256 && !expected.isHeldout
            return (ok, [
                "book_id": .string(loadedBook.bookID),
                "message_id": .string(composed.messageID),
                "n_samples": .int(composed.nSamples),
                "gap_samples": .int(gap),
                "pcm_sha256": .string(audio.pcmSHA256),
                "composite_sha256": .string(expected.compositeSHA256),
            ])
        }

        await report.check("compose_heldout_refused") {
            guard let grammar, let book else { throw AppError("compose_trained did not load the grammar and book") }
            guard let message = grammar.heldoutMessages.first,
                let action = book.atom(message.action), let referent = book.atom(message.referent)
            else { throw AppError("no held-out message with both atoms in \(book.bookID)") }
            do {
                _ = try await client.compose(
                    action: action.reference, referent: referent.reference, profile: .p1, bookID: book.bookID)
                return (false, [
                    "message_id": .string(message.messageID),
                    "error": "compose returned audio for a held-out message (discarded unread)",
                ])
            } catch let error as BridgeError where error.engineType != nil {
                let hash = try await client.compositeHash(
                    action: action.reference, referent: referent.reference, profile: .p1, bookID: book.bookID)
                return (error.engineCode == "E_HELDOUT" && hash.isHeldout, [
                    "message_id": .string(message.messageID),
                    "error_type": .string(error.engineType ?? ""),
                    "error_code": error.engineCode.map(JSONValue.string) ?? .null,
                    "expected_composite_sha256": .string(hash.compositeSHA256),
                ])
            }
        }

        await report.check("nonlexical_list") {
            let assets = try await client.nonlexicalList()
            let wellFormed = assets.allSatisfy { Hashing.isSHA256Hex($0.pcmSHA256) && Hashing.isSHA256Hex($0.fileSHA256) }
            return (!assets.isEmpty && wellFormed, [
                "count": .int(assets.count), "ids": .array(assets.map { .string($0.id) }),
            ])
        }

        await report.check("vectors_check") {
            let result = try await client.vectorsCheck()
            return (result.ok, [
                "renderer_checked": .int(result.renderer.checked),
                "renderer_mismatches": .int(result.renderer.mismatches.count),
                "composition_checked": .int(result.composition.checked),
                "composition_mismatches": .int(result.composition.mismatches.count),
            ])
        }

        await report.check("swift_conformance") {
            var rows: [ConformanceRow] = []
            for i in 0..<conformanceCount {
                let profile = Conformance.profile(index: i, cycling: true, fixed: .p2)
                rows.append(try await Conformance.check(seed: i + 1, profile: profile, admissibleOnly: true, client: client))
            }
            let mismatches = rows.filter { !$0.matches }
            let digestsMatch = await Offload.tableDigests() == SpecReference.tableDigests
            let passed = conformancePassed(
                checked: rows.count, expected: conformanceCount, mismatches: mismatches.count,
                tableDigestsMatch: digestsMatch)
            return (passed, [
                "recipes": .int(rows.count),
                "identical": .int(rows.count - mismatches.count),
                "seeds": .string("1...\(conformanceCount), profiles cycled P1/P2/P3, admissible_only"),
                "swift_renderer_version": .string(SpecRenderer.rendererVersion),
                "swift_table_digests_match_spec": .bool(digestsMatch),
                "swift_render_ms_total": .double((rows.reduce(0) { $0 + $1.swiftMilliseconds } * 10).rounded() / 10),
                "mismatches": .array(mismatches.map { row in
                    ["seed": .int(row.seed), "profile": .string(row.profile.rawValue),
                     "differences": .array(row.differences.map(JSONValue.string))]
                }),
            ])
        }
    }

    private static func emit(_ report: Report, seconds: Duration, hello: Hello?) {
        let total = Double(seconds.components.seconds) + Double(seconds.components.attoseconds) / 1e18
        var summary: [String: JSONValue] = [
            "ok": .bool(report.allOK),
            "passed": .int(report.passed),
            "failed": .int(report.failed),
            "seconds": .double((total * 1_000).rounded() / 1_000),
            "checks": .array(report.checks),
        ]
        if let hello {
            summary["engine"] = [
                "renderer_version": .string(hello.rendererVersion),
                "renderer_hash": .string(hello.rendererHash),
                "validator_version": .string(hello.validatorVersion),
                "validator_hash": .string(hello.validatorHash),
            ]
        }
        print(JSONValue.object(summary).prettyString)
    }
}
