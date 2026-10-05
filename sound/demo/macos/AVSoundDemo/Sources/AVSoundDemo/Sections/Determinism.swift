import AVSoundDemoCore
import AVSoundSpec
import Observation
import SwiftUI

// MARK: - Model

@MainActor
@Observable
final class DeterminismModel {
    @ObservationIgnored weak var app: AppModel?

    private(set) var selfTest: SelfTestResult?
    private(set) var vectors: VectorsCheck?
    private(set) var golden: GoldenCheck?
    /// When each engine check last ran. The bridge recomputes every check on request
    /// (nothing is cached), so each run re-reads the files and re-renders.
    private(set) var checkedAt: [String: Date] = [:]
    let activity = Activity()

    // Swift conformance
    var count = 25
    var firstSeed = 1
    var admissibleOnly = true
    var cycleProfiles = true
    private(set) var rows: [ConformanceRow] = []
    private(set) var plannedCount = 0
    private(set) var conformanceError: String?
    var rowSelection: ConformanceRow.ID?
    @ObservationIgnored private var conformanceTask: Task<Void, Never>?

    // Composite cross-check
    private(set) var compositeRows: [CompositeRow] = []
    private(set) var atomRows: [AtomHashRow] = []
    private(set) var compositeBookID: String?
    private(set) var compositeError: String?

    // Tables
    private(set) var digests: [String: String] = [:]

    var isConformanceRunning: Bool { activity.isRunning("conformance") }
    var matchedCount: Int { rows.filter(\.matches).count }

    func runSelfTest() {
        guard let client = app?.client else { return }
        activity.run("selfTest") { [weak self] in
            let result = try await client.selfTest()
            guard let self, self.app?.isCurrent(client) == true else { return }  // a replaced bridge's check
            self.selfTest = result
            self.checkedAt["selfTest"] = Date()
        }
    }

    func runVectors() {
        guard let client = app?.client else { return }
        activity.run("vectors") { [weak self] in
            let result = try await client.vectorsCheck()
            guard let self, self.app?.isCurrent(client) == true else { return }
            self.vectors = result
            self.checkedAt["vectors"] = Date()
        }
    }

    func runGolden() {
        guard let client = app?.client else { return }
        activity.run("golden") { [weak self] in
            let result = try await client.goldenCheck()
            guard let self, self.app?.isCurrent(client) == true else { return }
            self.golden = result
            self.checkedAt["golden"] = Date()
        }
    }

    func runAllEngineChecks() {
        runSelfTest()
        runVectors()
        runGolden()
    }

    /// Renders `count` random recipes on the bridge and in AVSoundSpec and compares them.
    func runConformance() {
        guard let app, let client = app.client else { return }
        conformanceTask?.cancel()
        let count = max(1, count)
        let firstSeed = firstSeed
        let admissibleOnly = admissibleOnly
        let cycle = cycleProfiles
        let fixed = app.profile
        rows = []
        plannedCount = count
        conformanceError = nil
        conformanceTask = activity.run("conformance") { [weak self] in
            do {
                for i in 0..<count {
                    try Task.checkCancellation()
                    let profile = Conformance.profile(index: i, cycling: cycle, fixed: fixed)
                    let row = try await Conformance.check(
                        seed: firstSeed + i, profile: profile, admissibleOnly: admissibleOnly, client: client)
                    guard self?.app?.isCurrent(client) == true else { return }  // the bridge was replaced
                    self?.rows.append(row)
                }
            } catch is CancellationError {
                self?.conformanceError = "Stopped after \(self?.rows.count ?? 0) recipes."
            } catch {
                self?.conformanceError = userMessage(error)
            }
        }
    }

    func stopConformance() {
        conformanceTask?.cancel()
    }

    /// Composite hashes of all 32 messages of the synthetic DEMO book: bridge
    /// `composite_hash` against `SpecComposer` over AVSoundSpec atom renders.
    func runCompositeCheck() {
        guard let app, let client = app.client else { return }
        let profile = app.profile
        compositeRows = []
        atomRows = []
        compositeError = nil
        activity.run("composite") { [weak self] in
            do {
                let grammar = try await client.grammar()
                let book = try await client.syntheticBook(profile: profile)
                self?.compositeBookID = book.bookID
                let pcm = try await Conformance.swiftAtoms(book, profile: profile)
                var atoms: [AtomHashRow] = []
                for atom in book.atoms {
                    atoms.append(AtomHashRow(
                        atomID: atom.atomID, enginePCM: atom.pcmSHA256,
                        swiftPCM: pcm[atom.atomID].map { SpecHash.sha256Hex($0) } ?? "missing"))
                }
                self?.atomRows = atoms
                for message in grammar.messages {
                    try Task.checkCancellation()
                    guard let action = book.atom(message.action), let referent = book.atom(message.referent),
                        let actionPCM = pcm[message.action], let referentPCM = pcm[message.referent]
                    else {
                        throw AppError("\(message.messageID): an atom is missing from \(book.bookID)")
                    }
                    let bridge = try await client.compositeHash(
                        action: action.reference, referent: referent.reference, profile: profile, bookID: book.bookID)
                    let swift = await Conformance.swiftComposite(action: actionPCM, referent: referentPCM)
                    guard self?.app?.isCurrent(client) == true else { return }  // the bridge was replaced
                    self?.compositeRows.append(CompositeRow(
                        messageID: message.messageID, status: message.status, isHeldout: message.isHeldout,
                        bridgeHash: bridge.compositeSHA256, swiftHash: swift, bridgeSamples: bridge.nSamples,
                        swiftSamples: Conformance.compositeSamples(action: actionPCM, referent: referentPCM)))
                }
            } catch {
                self?.compositeError = userMessage(error)
            }
        }
    }

    func loadDigests() {
        guard digests.isEmpty, !activity.isRunning("digests") else { return }
        activity.run("digests") { [weak self] in
            self?.digests = await Offload.tableDigests()
        }
    }
}

// MARK: - View

struct DeterminismView: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        SectionPage(.determinism, summary: "Is the sound bit-exact? The engine re-checks its pinned vectors and golden manifest on this machine, and an independent Swift port of the renderer spec (AVSoundSpec) must produce the same bytes as the Python engine.") {
            EngineChecksCard()
            ConformanceCard()
            CompositeCheckCard()
            TablesCard()
        }
        .onAppear { app.determinism.loadDigests() }
    }
}

private struct EngineChecksCard: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        let model = app.determinism
        let activity = model.activity
        Card("Engine checks", systemImage: "checklist") {
            Button("Run All") { model.runAllEngineChecks() }
                .buttonStyle(.borderedProminent)
                .requiresBridge()
        } content: {
            if let error = activity.error {
                ErrorBanner(message: error) { activity.error = nil }
            }
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 12) {
                GridRow {
                    ActionButton("self_test", systemImage: "stethoscope", isRunning: activity.isRunning("selfTest")) {
                        model.runSelfTest()
                    }
                    .requiresBridge()
                    if let result = model.selfTest {
                        VStack(alignment: .leading, spacing: 4) {
                            PassFail(ok: result.ok, text: result.message)
                            CheckedAt(date: model.checkedAt["selfTest"])
                        }
                    } else {
                        Text("The renderer's built-in pinned vectors.").foregroundStyle(.secondary)
                    }
                }
                GridRow(alignment: .top) {
                    ActionButton("vectors_check", systemImage: "function", isRunning: activity.isRunning("vectors")) {
                        model.runVectors()
                    }
                    .requiresBridge()
                    if let result = model.vectors {
                        VStack(alignment: .leading, spacing: 4) {
                            PassFail(ok: result.ok, text: result.ok ? "all reference vectors reproduce" : "mismatches")
                            Text("renderer: \(result.renderer.checked) checked, \(result.renderer.mismatches.count) mismatches \u{00B7} composition: \(result.composition.checked) checked, \(result.composition.mismatches.count) mismatches")
                                .font(.callout).foregroundStyle(.secondary)
                            MismatchList(items: result.renderer.mismatches + result.composition.mismatches)
                            CheckedAt(date: model.checkedAt["vectors"])
                        }
                    } else {
                        Text("Re-render testvectors/renderer and testvectors/composition.").foregroundStyle(.secondary)
                    }
                }
                GridRow(alignment: .top) {
                    ActionButton("golden_check", systemImage: "medal", isRunning: activity.isRunning("golden")) {
                        model.runGolden()
                    }
                    .requiresBridge()
                    if let result = model.golden {
                        VStack(alignment: .leading, spacing: 4) {
                            PassFail(ok: result.ok, text: result.ok ? "\(result.items) golden items reproduce" : "\(result.mismatches.count) of \(result.items) items differ")
                            HStack(spacing: 6) {
                                Text("digest").foregroundStyle(.secondary)
                                HashText(hash: result.digest, length: 24)
                            }
                            MismatchList(items: result.mismatches)
                            CheckedAt(date: model.checkedAt["golden"])
                        }
                    } else {
                        Text("Recompute the golden manifest (tests/golden/manifest.json).").foregroundStyle(.secondary)
                    }
                }
            }
        }
    }
}

/// "checked at 14:03:22": every run is a new check on the bridge.
private struct CheckedAt: View {
    let date: Date?

    var body: some View {
        if let date {
            Text("checked at \(date.formatted(date: .omitted, time: .standard))")
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
    }
}

private struct MismatchList: View {
    let items: [JSONValue]

    var body: some View {
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: 2) {
                ForEach(Array(items.prefix(20).enumerated()), id: \.offset) { _, item in
                    Text(item.stringValue ?? item.compactString)
                        .font(.caption.monospaced())
                        .foregroundStyle(.red)
                        .textSelection(.enabled)
                }
                if items.count > 20 {
                    Text("\u{2026} and \(items.count - 20) more").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }
}

private struct ConformanceCard: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        @Bindable var model = app.determinism
        let running = model.isConformanceRunning
        Card("Swift conformance", systemImage: "swift",
             subtitle: "random_recipe(seed) \u{2192} render on the bridge and in AVSoundSpec \u{2192} pcm_sha256 and file_sha256 must be identical.") {
            if running {
                Button("Stop", role: .cancel) { model.stopConformance() }
            } else {
                Button("Run") { model.runConformance() }
                    .buttonStyle(.borderedProminent)
                    .requiresBridge()
            }
        } content: {
            HStack(spacing: 18) {
                Stepper("Recipes: \(model.count)", value: $model.count, in: 1...500)
                Stepper("First seed: \(model.firstSeed)", value: $model.firstSeed, in: 0...1_000_000)
                Toggle("Admissible only", isOn: $model.admissibleOnly)
                Toggle("Cycle P1/P2/P3", isOn: $model.cycleProfiles)
                    .help("Off: every recipe uses the current profile (\(app.profile.rawValue))")
            }
            .disabled(running)
            if model.plannedCount > 0 {
                HStack(spacing: 12) {
                    ProgressView(value: Double(model.rows.count), total: Double(max(model.plannedCount, 1)))
                        .frame(maxWidth: 260)
                    let allMatch = model.matchedCount == model.rows.count
                    if !model.rows.isEmpty {
                        PassFail(ok: allMatch,
                                 text: "\(model.matchedCount) of \(model.rows.count) identical\(running ? "" : (model.rows.count == model.plannedCount ? "" : " (incomplete)"))")
                            .fontWeight(.semibold)
                    }
                    if running { ProgressView().controlSize(.small) }
                }
            }
            if let error = model.conformanceError {
                ErrorBanner(message: error)
            }
            if !model.rows.isEmpty {
                Table(model.rows, selection: $model.rowSelection) {
                    TableColumn("") { row in
                        Image(systemName: row.matches ? "checkmark.circle.fill" : "xmark.octagon.fill")
                            .foregroundStyle(row.matches ? .green : .red)
                    }
                    .width(22)
                    TableColumn("Seed") { row in Text("\(row.seed)").monospacedDigit() }.width(44)
                    TableColumn("Profile") { row in Text(row.profile.rawValue) }.width(44)
                    TableColumn("Recipe") { row in
                        Text(row.recipe.summary).font(.caption.monospacedDigit()).help(row.recipe.canonicalJSON)
                    }
                    .width(min: 180, ideal: 250)
                    TableColumn("Python pcm") { row in
                        Text(row.bridgeOverflow ? "overflow" : Fmt.shortHash(row.bridgePCM)).font(.caption.monospaced())
                            .help(row.bridgePCM ?? "no PCM (overflow)")
                    }
                    .width(min: 100, ideal: 110)
                    TableColumn("Swift pcm") { row in
                        Text(row.swiftOverflow ? "overflow" : Fmt.shortHash(row.swiftPCM)).font(.caption.monospaced())
                            .foregroundStyle(row.pcmMatches ? Color.primary : Color.red)
                            .help(row.swiftPCM)
                    }
                    .width(min: 100, ideal: 110)
                    TableColumn("file") { row in
                        Image(systemName: row.fileMatches ? "equal.circle" : "notequal.circle")
                            .foregroundStyle(row.fileMatches ? .green : .red)
                            .help(row.fileMatches ? "file_sha256 identical" : "file_sha256 differs")
                    }
                    .width(30)
                    TableColumn("Swift time") { row in
                        Text("\(Fmt.number(row.swiftMilliseconds, digits: 1)) ms").font(.caption.monospacedDigit())
                    }
                    .width(min: 60, ideal: 70)
                }
                .frame(height: 300)
                if let id = model.rowSelection, let row = model.rows.first(where: { $0.id == id }) {
                    ConformanceDetail(row: row)
                }
            }
        }
    }
}

private struct ConformanceDetail: View {
    let row: ConformanceRow

    var body: some View {
        Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 4) {
            InfoRow("Seed", "\(row.seed) \u{00B7} \(row.profile.rawValue)")
            InfoRow("Recipe") { Text(row.recipe.canonicalJSON).font(.caption.monospaced()).textSelection(.enabled) }
            InfoRow("Python pcm_sha256") { HashText(hash: row.bridgePCM, length: 64) }
            InfoRow("Swift pcm_sha256") { HashText(hash: row.swiftPCM, length: 64, tint: row.pcmMatches ? nil : .red) }
            InfoRow("Python file_sha256") { HashText(hash: row.bridgeFile, length: 64) }
            InfoRow("Swift file_sha256") { HashText(hash: row.swiftFile, length: 64, tint: row.fileMatches ? nil : .red) }
            InfoRow("Layout", "n_samples \(Fmt.int(row.bridgeSamples)) / \(Fmt.int(row.swiftSamples)) \u{00B7} peak \(row.bridgePeak) / \(row.swiftPeak)")
            if !row.differences.isEmpty {
                InfoRow("Differences") {
                    Text(row.differences.joined(separator: "\n")).foregroundStyle(.red).font(.caption.monospaced())
                }
            }
        }
        .padding(10)
        .background(Color.secondary.opacity(0.06), in: RoundedRectangle(cornerRadius: 6))
    }
}

private struct CompositeCheckCard: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        let model = app.determinism
        let running = model.activity.isRunning("composite")
        let rows = model.compositeRows
        let matched = rows.filter(\.matches).count
        let atomsMatched = model.atomRows.filter(\.matches).count
        Card("Composite-hash cross-check", systemImage: "link",
             subtitle: "All 32 messages of the synthetic \(model.compositeBookID ?? "DEMO-\(app.profile.rawValue)") book, held-out ones included (hashes only): bridge composite_hash vs SpecComposer over the Swift atom renders.") {
            ActionButton("Run for \(app.profile.rawValue)", systemImage: "play.circle", isRunning: running, prominent: true) {
                model.runCompositeCheck()
            }
            .requiresBridge()
        } content: {
            if let error = model.compositeError {
                ErrorBanner(message: error)
            }
            if !model.atomRows.isEmpty {
                HStack(spacing: 18) {
                    PassFail(ok: atomsMatched == model.atomRows.count,
                             text: "atoms: \(atomsMatched) of \(model.atomRows.count) waveform hashes identical")
                    if !rows.isEmpty {
                        PassFail(ok: matched == rows.count,
                                 text: "messages: \(matched) of \(rows.count) composite hashes identical\(running ? "\u{2026}" : "")")
                    }
                }
                .fontWeight(.semibold)
            }
            if !rows.isEmpty {
                Table(rows) {
                    TableColumn("") { row in
                        Image(systemName: row.matches ? "checkmark.circle.fill" : "xmark.octagon.fill")
                            .foregroundStyle(row.matches ? .green : .red)
                    }
                    .width(22)
                    TableColumn("Message") { row in Text(row.messageID).font(.body.monospaced()) }.width(80)
                    TableColumn("Status") { row in
                        Text(row.status).foregroundStyle(row.isHeldout ? Color.orange : Color.primary)
                    }
                    .width(70)
                    TableColumn("Bridge composite_hash") { row in
                        Text(Fmt.shortHash(row.bridgeHash, length: 20)).font(.caption.monospaced()).help(row.bridgeHash)
                    }
                    .width(min: 150, ideal: 180)
                    TableColumn("SpecComposer") { row in
                        Text(Fmt.shortHash(row.swiftHash, length: 20)).font(.caption.monospaced())
                            .foregroundStyle(row.matches ? Color.primary : Color.red)
                            .help(row.swiftHash)
                    }
                    .width(min: 150, ideal: 180)
                    TableColumn("Samples") { row in Text(Fmt.int(row.bridgeSamples)).monospacedDigit() }
                        .width(min: 60, ideal: 70)
                }
                .frame(height: 300)
            }
        }
    }
}

private struct TablesCard: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        let model = app.determinism
        Card("AVSoundSpec tables", systemImage: "tablecells",
             subtitle: "SHA-256 of the integer tables the Swift port generates, against the digests published in renderer spec D4.") {
            if model.activity.isRunning("digests") { ProgressView().controlSize(.small) }
        } content: {
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
                ForEach(SpecReference.tableOrder, id: \.self) { key in
                    let actual = model.digests[key]
                    let expected = SpecReference.tableDigests[key]
                    InfoRow(key) {
                        HStack(spacing: 8) {
                            HashText(hash: actual, length: 32)
                            if let actual {
                                PassFail(ok: actual == expected, text: actual == expected ? "matches spec D4" : "differs from spec D4")
                            }
                        }
                    }
                }
                Divider().gridCellColumns(2)
                InfoRow("Renderer version") {
                    let engine = app.hello?.rendererVersion
                    HStack(spacing: 8) {
                        Text("AVSoundSpec \(SpecRenderer.rendererVersion) \u{00B7} engine \(engine ?? "?")")
                        if let engine {
                            PassFail(ok: engine == SpecRenderer.rendererVersion, text: engine == SpecRenderer.rendererVersion ? "same" : "different")
                        }
                    }
                }
                InfoRow("Message gap", "\(Fmt.int(SpecComposer.gapSamples)) samples (engine: \(app.hello.map { Fmt.int($0.gapSamples) } ?? "?"))")
            }
        }
    }
}
