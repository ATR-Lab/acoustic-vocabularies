import AVSoundDemoCore
import Observation
import SwiftUI

// MARK: - Model

@MainActor
@Observable
final class ValidatorModel {
    /// One validation and what was validated.
    struct Outcome {
        let source: String
        let profile: Profile
        let bookCount: Int
        let threshold: String?
        let result: ValidationResult
    }

    @ObservationIgnored weak var app: AppModel?

    /// Exact threshold text, a plain decimal (`"0.1"`); defaults to `hello.threshold`.
    var thresholdText = ""

    /// Whether the threshold text is one the engine accepts (empty: the configured one).
    var isThresholdValid: Bool {
        thresholdText.nonEmpty.map(ThresholdText.isValid) ?? true
    }
    var useReserved = true
    var candidateText = Recipe.example.canonicalJSON
    private(set) var outcome: Outcome?
    let activity = Activity()

    /// Examples for the raw-JSON editor.
    static let samples: [(title: String, text: String)] = [
        ("Malformed JSON (E_JSON)", #"{"total_ms": 600, "pitches": [-3, 0, 4]"#),
        ("Missing fields (E_SCHEMA)", #"{"total_ms": 600, "pitches": [-3, 0, 4]}"#),
        ("Out of domain (E_DOMAIN)",
         #"{"total_ms": 640, "pitches": [-3, 0, 9], "rhythm_weights": [2, 1, 3], "gaps_ms": [40, 25], "amplitudes": [1.0, 0.6, 0.7]}"#),
        ("Short event (E_EVENT_SHORT)",
         #"{"total_ms": 450, "pitches": [0, 0, 0], "rhythm_weights": [1, 4, 4], "gaps_ms": [60, 60], "amplitudes": [1.0, 0.8, 0.6]}"#),
        ("Wrong type (E_SCHEMA)", #"[600, [-3, 0, 4]]"#),
    ]

    func validateCurrent() {
        guard let app else { return }
        validate(.recipe(app.recipe), source: "Current recipe \u{00B7} \(app.recipe.summary)")
    }

    func validateText() {
        validate(.text(candidateText), source: "Raw JSON text (\(candidateText.utf8.count) bytes)")
    }

    func useCurrentRecipeText() {
        guard let app else { return }
        candidateText = app.recipe.canonicalJSON
    }

    func resetThreshold() {
        if let threshold = app?.hello?.threshold { thresholdText = threshold }
    }

    func profileDidChange() {
        outcome = nil
    }

    /// The result shown is always the verdict on the last request: the previous one is
    /// cleared when a validation starts, so a request that fails (for example a threshold
    /// the engine refuses) shows its error alone, not under an older verdict.
    private func validate(_ candidate: ValidationCandidate, source: String) {
        guard let app, let client = app.client else { return }
        outcome = nil
        let profile = app.profile
        let committed = app.book.references
        let threshold = thresholdText.nonEmpty
        let useReserved = useReserved
        activity.run("validate") { [weak self] in
            let result = try await client.validate(
                candidate, profile: profile, committed: committed, threshold: threshold, useReserved: useReserved)
            guard self?.app?.isCurrent(client) == true else { return }  // a replaced bridge's verdict
            self?.outcome = Outcome(
                source: source, profile: profile, bookCount: committed.count, threshold: threshold, result: result)
        }
    }
}

// MARK: - View

struct ValidatorBookView: View {
    @Environment(AppModel.self) private var app
    /// Cmd-Return validates what is being edited: the raw JSON text while its editor has
    /// the focus, the current recipe otherwise. The editor reports its focus itself (it is
    /// an AppKit text view, see `PlainTextEditor`).
    @State private var isEditingRawJSON = false

    var body: some View {
        HStack(spacing: 0) {
            BookPanel()
                .frame(width: 380)
            Divider()
            SectionPage(.validator, summary: "The engine's admissibility check (validate) against a scratch book of committed atoms for the current profile. Rejections are normal results with reason codes.") {
                ValidateCurrentCard(shortcut: isEditingRawJSON ? nil : Self.validateShortcut)
                RawJSONCard(isEditing: $isEditingRawJSON,
                            shortcut: isEditingRawJSON ? Self.validateShortcut : nil)
                ValidationResultCard()
            }
        }
    }

    static let validateShortcut = KeyboardShortcut(.return, modifiers: .command)
}

/// The scratch book of the current profile: 16 slots by family and role.
private struct BookPanel: View {
    @Environment(AppModel.self) private var app
    @State private var playing: String?

    var body: some View {
        let book = app.book
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text("Scratch book \u{00B7} \(app.profile.rawValue)").font(.headline)
                Spacer()
                if let label = book.label {
                    Pill(text: label, tint: .green)
                        .help("An unchanged copy of \(label); any change makes it an edited book")
                } else if !book.isEmpty {
                    Pill(text: "edited", tint: .orange)
                }
            }
            Text("\(book.count) of 16 atoms. Validation, messages and the fallback scan use this book.")
                .font(.callout)
                .foregroundStyle(.secondary)
            HStack {
                ActionButton("Load DEMO Book", systemImage: "books.vertical",
                             isRunning: app.activity.isRunning("demoBook")) {
                    app.loadDemoBook()
                }
                .help("Replace this book by the synthetic DEMO-\(app.profile.rawValue) book")
                Button(role: .destructive) {
                    app.clearBook()
                } label: {
                    Label("Clear", systemImage: "trash")
                }
                .disabled(book.isEmpty)
            }
            .requiresBridge()
            List {
                ForEach(AtomSlots.groups) { group in
                    Section(group.title) {
                        ForEach(group.ids, id: \.self) { atomID in
                            BookSlotRow(atomID: atomID, recipe: book.atoms[atomID], playing: $playing)
                        }
                    }
                }
            }
            .listStyle(.inset)
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .padding(16)
    }
}

private struct BookSlotRow: View {
    @Environment(AppModel.self) private var app
    let atomID: String
    let recipe: Recipe?
    @Binding var playing: String?

    var body: some View {
        HStack(spacing: 8) {
            Text(atomID)
                .font(.body.monospaced().weight(.semibold))
                .frame(width: 46, alignment: .leading)
            if let recipe {
                VStack(alignment: .leading, spacing: 1) {
                    Text("\(recipe.totalMs) ms \u{00B7} rhythm \(recipe.rhythmWeights.map(String.init).joined(separator: ":")) \u{00B7} gaps \(recipe.gapsMs.map(String.init).joined(separator: "/"))")
                    Text("pitch \(recipe.pitches.map(Fmt.signed).joined(separator: " ")) \u{00B7} amp \(recipe.amplitudes.map(Fmt.amplitude).joined(separator: " "))")
                        .foregroundStyle(.secondary)
                }
                .font(.caption.monospacedDigit())
                .lineLimit(1)
                .help(recipe.canonicalJSON)
                Spacer(minLength: 4)
                Button {
                    play(recipe)
                } label: {
                    if playing == atomID {
                        ProgressView().controlSize(.mini)
                    } else {
                        Image(systemName: "play.fill")
                    }
                }
                .help("Render and play \(atomID)")
                .accessibilityLabel("Play \(atomID)")
                .requiresBridge()
                Button {
                    app.selection = .recipeLab  // first, so the lab plays the new recipe
                    app.recipe = recipe
                } label: {
                    Image(systemName: "slider.horizontal.3")
                }
                .help("Open \(atomID) in the Recipe Lab")
                .accessibilityLabel("Open \(atomID) in the Recipe Lab")
                Button {
                    app.setAtom(atomID, recipe: nil)
                } label: {
                    Image(systemName: "xmark.circle")
                }
                .help("Remove \(atomID) from the book")
                .accessibilityLabel("Remove \(atomID) from the book")
            } else {
                Text("empty").font(.caption).foregroundStyle(.tertiary)
                Spacer(minLength: 4)
                Button {
                    app.setAtom(atomID, recipe: app.recipe)
                } label: {
                    Image(systemName: "plus.circle")
                }
                .help("Put the current Recipe Lab recipe into \(atomID)")
                .accessibilityLabel("Put the current recipe into \(atomID)")
            }
        }
        .buttonStyle(.borderless)
    }

    private func play(_ recipe: Recipe) {
        let profile = app.profile
        playing = atomID
        app.activity.run("play-\(atomID)") { [app] in
            defer { playing = nil }
            try await app.renderAndPlay(recipe, profile: profile)
        }
    }
}

private struct ValidateCurrentCard: View {
    @Environment(AppModel.self) private var app
    /// Cmd-Return, unless the raw JSON editor has the focus.
    let shortcut: KeyboardShortcut?

    var body: some View {
        @Bindable var validator = app.validator
        Card("Validate the current recipe", systemImage: "checkmark.seal",
             subtitle: "\(app.recipe.summary) \u{00B7} \(app.profile.rawValue) \u{00B7} against \(app.book.count) book atoms") {
            ActionButton("Validate", systemImage: "play.circle", isRunning: validator.activity.isRunning("validate"),
                         prominent: true) {
                validator.validateCurrent()
            }
            .keyboardShortcut(shortcut)
            .help("Validate the current recipe (\u{2318}\u{21A9} outside the JSON editor)")
            .requiresBridge()
        } content: {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 16) {
                    LabeledContent("Threshold") {
                        HStack(spacing: 6) {
                            TextField("0.1", text: $validator.thresholdText)
                                .textFieldStyle(.roundedBorder)
                                .font(.body.monospaced())
                                .frame(width: 110)
                                .help("Exact separation threshold: \(ThresholdText.hint) (not a fraction like 1/10). Empty uses the configured threshold.")
                            if let hello = app.hello, validator.thresholdText != hello.threshold {
                                Button("Reset to \(hello.threshold)") { validator.resetThreshold() }
                                    .buttonStyle(.link)
                            }
                        }
                    }
                    Toggle("Check the reserved nonlexical assets (use_reserved)", isOn: $validator.useReserved)
                }
                if !validator.isThresholdValid {
                    Label("The engine accepts only \(ThresholdText.hint); it will refuse this threshold.",
                          systemImage: "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }
            }
        }
    }
}

private struct RawJSONCard: View {
    @Environment(AppModel.self) private var app
    @Binding var isEditing: Bool
    /// Cmd-Return while the editor has the focus.
    let shortcut: KeyboardShortcut?

    var body: some View {
        @Bindable var validator = app.validator
        Card("Validate raw JSON text", systemImage: "curlybraces",
             subtitle: "Any candidate text goes to the engine as is: malformed JSON gives E_JSON, wrong shapes E_SCHEMA, values outside the domain E_DOMAIN.") {
            HStack {
                Menu("Examples") {
                    Button("Current recipe") { validator.useCurrentRecipeText() }
                    Divider()
                    ForEach(ValidatorModel.samples, id: \.title) { sample in
                        Button(sample.title) { validator.candidateText = sample.text }
                    }
                }
                .fixedSize()
                ActionButton("Validate Text", systemImage: "play.circle",
                             isRunning: validator.activity.isRunning("validate")) {
                    validator.validateText()
                }
                .keyboardShortcut(shortcut)
                .help("Validate this text (\u{2318}\u{21A9} while editing it)")
                .requiresBridge()
            }
        } content: {
            // Not TextEditor: it turns typed straight quotes into curly ones (E_JSON).
            PlainTextEditor(text: $validator.candidateText,
                            onFocusChange: { isEditing = $0 },
                            accessibilityLabel: "Raw JSON text")
                .padding(6)
                .frame(minHeight: 90, maxHeight: 140)
                .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 6))
                .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(Color(nsColor: .separatorColor)))
        }
    }
}

private struct ValidationResultCard: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        let validator = app.validator
        Card("Result", systemImage: "doc.text.magnifyingglass") {
            if validator.activity.isRunning("validate") { ProgressView().controlSize(.small) }
        } content: {
            if let error = validator.activity.error {
                ErrorBanner(message: error) { validator.activity.error = nil }
            }
            if let outcome = validator.outcome {
                OutcomeView(outcome: outcome, featureNames: app.hello?.featureNames ?? [])
            } else if validator.activity.isRunning("validate") {
                Text("Asking the engine\u{2026}").foregroundStyle(.secondary)
            } else if validator.activity.error != nil {
                Text("No verdict: the engine did not validate this request (see the error above).")
                    .foregroundStyle(.secondary)
            } else {
                Text("Validate the current recipe or a JSON text to see the engine's verdict.")
                    .foregroundStyle(.secondary)
            }
        }
    }
}

private struct OutcomeView: View {
    let outcome: ValidatorModel.Outcome
    let featureNames: [String]

    var body: some View {
        let result = outcome.result
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 10) {
                if result.ok {
                    CodeChip(code: "ADMISSIBLE", tint: .green)
                } else {
                    ForEach(result.codes, id: \.self) { CodeChip(code: $0) }
                }
                Spacer()
                Text("\(outcome.profile.rawValue) \u{00B7} \(outcome.bookCount) references \u{00B7} threshold \(result.threshold ?? outcome.threshold ?? "default")")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Text(outcome.source).font(.callout).foregroundStyle(.secondary).lineLimit(2)

            if !result.ok {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(result.reasons.enumerated()), id: \.offset) { _, reason in
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text(reason.code).font(.caption.monospaced().weight(.semibold)).foregroundStyle(.red)
                                .frame(width: 120, alignment: .leading)
                            Text(reason.message).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }

            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
                InfoRow("Nearest reference") {
                    if let nearest = result.nearestID {
                        Text("\(nearest) (index \(result.nearestIndex.map(String.init) ?? "?"))").font(.body.monospaced())
                    } else if result.features == nil {
                        Text("not computed (the candidate is not a valid recipe)").foregroundStyle(.secondary)
                    } else {
                        Text("none (empty book)").foregroundStyle(.secondary)
                    }
                }
                if let distance = result.nearestDistance {
                    InfoRow("Distance") {
                        let threshold = (result.threshold ?? outcome.threshold).flatMap(ExactNumber.double)
                        HStack(spacing: 6) {
                            Text(Fmt.number(distance, digits: 4)).monospacedDigit()
                            if let threshold {
                                Text(distance >= threshold ? "\u{2265} \(Fmt.number(threshold, digits: 4))" : "< \(Fmt.number(threshold, digits: 4))")
                                    .foregroundStyle(distance >= threshold ? Color.green : Color.red)
                            }
                        }
                    }
                }
                InfoRow("pcm_sha256") { HashText(hash: result.pcmSHA256) }
                InfoRow("recipe_sha256") { HashText(hash: result.recipeSHA256) }
                if let events = result.eventSamples {
                    InfoRow("Event samples", events.map(Fmt.int).joined(separator: " \u{00B7} "))
                }
                InfoRow("Versions", "validator \(result.validatorVersion ?? "?") \u{00B7} renderer \(result.rendererVersion ?? "?")")
            }

            if let features = result.features {
                FeatureTable(names: featureNames, exact: features)
            } else {
                Text("No features: the candidate did not parse as a recipe.").foregroundStyle(.secondary)
            }

            DisclosureGroup("Raw result JSON") {
                CodeBlock(text: result.raw.prettyString, maxHeight: 260)
            }
        }
    }
}

/// The 12 normalized features: name, exact value, float.
struct FeatureTable: View {
    let names: [String]
    let exact: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Features").font(.subheadline.weight(.semibold))
            Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 4) {
                GridRow {
                    Text("#").foregroundStyle(.secondary)
                    Text("Name").foregroundStyle(.secondary)
                    Text("Exact").foregroundStyle(.secondary)
                    Text("Float").foregroundStyle(.secondary)
                    Text("").gridCellUnsizedAxes(.horizontal)
                }
                .font(.caption.weight(.semibold))
                ForEach(Array(exact.enumerated()), id: \.offset) { i, value in
                    let float = ExactNumber.double(value)
                    GridRow {
                        Text("\(i + 1)").foregroundStyle(.secondary).monospacedDigit()
                        Text(i < names.count ? names[i] : "feature \(i + 1)").font(.body.monospaced())
                        Text(value).font(.body.monospaced())
                        Text(float.map { Fmt.number($0, digits: 4) } ?? "\u{2014}").monospacedDigit()
                        FeatureBar(value: float ?? 0)
                    }
                }
            }
        }
    }
}

private struct FeatureBar: View {
    let value: Double

    var body: some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                Capsule().fill(Color.secondary.opacity(0.15))
                Capsule().fill(Color.accentColor.opacity(0.7))
                    .frame(width: proxy.size.width * min(max(value, 0), 1))
            }
        }
        .frame(width: 120, height: 6)
    }
}
