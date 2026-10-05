import AVSoundDemoCore
import Observation
import SwiftUI

// MARK: - Model

@MainActor
@Observable
final class MessagesModel {
    /// The engine's answer for the selected message.
    enum Outcome {
        /// Both atoms must be in the scratch book. `missing` follows the book.
        case missingAtoms(MessageRef, missing: [String])
        /// A trained message composed and verified (and played when a click asked for it).
        /// `expected` is its `composite_hash`, asked for before it was composed.
        case composed(MessageRef, ComposeResult, AudioClip, expected: CompositeHashResult)
        /// A held-out message: the engine's refusal and the expected hash only.
        case heldOut(MessageRef, refusal: BridgeError?, unexpectedAudio: Bool, hash: CompositeHashResult?)
        /// A trained message that this scratch book would make the same audio as held-out
        /// `heldOut` (for example when two slots hold the same recipe). It is not composed:
        /// a held-out message never exists as audio, under its own ID or another one.
        case sameAsHeldOut(MessageRef, heldOut: [MessageRef], hash: CompositeHashResult)
    }

    /// What an outcome was made from: both atom references and the book ID sent with them,
    /// and for a trained message every atom of the book: whether it may be composed
    /// depends on the held-out messages of the whole book (`heldOutTwins`).
    struct Source: Equatable {
        let action: AtomReference
        let referent: AtomReference
        let bookID: String?
        /// The book's atoms (trained messages only; `nil` for a held-out message).
        let bookAtoms: [String: Recipe]?
    }

    @ObservationIgnored weak var app: AppModel?

    var family = "K"
    private(set) var selected: MessageRef?
    var selectedID: String? { selected?.messageID }
    private(set) var outcome: Outcome?
    let activity = Activity()

    /// Whether the engine's answer for the selected message is still awaited.
    var isAsking: Bool { activity.isRunning("compose") }

    /// The request for the selected message failed (a timeout, or the bridge stopped or
    /// restarted meanwhile): there is no answer, and none is awaited. `retry()` asks again.
    var hasFailed: Bool { selected != nil && outcome == nil && !isAsking }

    @ObservationIgnored private var task: Task<Void, Never>?
    /// The atoms of the shown or pending outcome (`nil` while atoms are missing).
    @ObservationIgnored private var source: Source?
    /// Whether the selected trained message is still to be played once it is composed: a
    /// click asks for it, and so does a message that waits for its atoms; a recomposition
    /// after a book change does not.
    @ObservationIgnored private var wantsPlay = false

    func profileDidChange() {
        clearSelection()
    }

    func familyDidChange() {
        clearSelection()
    }

    private func clearSelection() {
        task?.cancel()
        stopOwnClip()
        selected = nil
        outcome = nil
        source = nil
        wantsPlay = false
    }

    /// The scratch book changed: the selected message follows it. A message that waited
    /// for missing atoms is composed once the book has them, and played if Messages is
    /// the visible section then (elsewhere it would sound like that section's own
    /// sound); while it still waits, the list of missing atoms follows the book. A
    /// composed message (or a held-out hash) made from atoms that changed or left the
    /// book is asked for again with the current atoms without playing, or shows the
    /// missing atoms; its clip stops if it plays. A trained message is also asked for
    /// again when any other atom changes: the change can make it equal to a held-out
    /// message of the book.
    func bookDidChange() {
        guard let app, let message = selected else { return }
        let book = app.book
        let current = Self.source(of: message, in: book)
        guard current != source else {
            // Still waiting for atoms: only which of them are missing can have changed.
            if current == nil, case .missingAtoms(let waiting, let missing)? = outcome {
                let now = Self.missingAtoms(of: waiting, in: book)
                if now != missing { outcome = .missingAtoms(waiting, missing: now) }
            }
            return
        }
        stopOwnClip()
        load(message, play: wantsPlay)
    }

    /// A click on a cell: any sound that plays stops at once, so that no earlier sound
    /// goes on under the new card (a held-out card says that nothing is played), and the
    /// message is asked for and, when trained, played.
    func select(_ message: MessageRef) {
        guard let app, app.client != nil else { return }
        app.stopPlayback()
        load(message, play: true)
    }

    /// Asks the engine again for the selected message (after `hasFailed`), as a click on
    /// its cell does.
    func retry() {
        guard let message = selected else { return }
        select(message)
    }

    /// Selects `message` and asks the engine for it. A trained message is composed,
    /// verified and, with `play` and while Messages is visible, played. A held-out
    /// message shows the engine's refusal and its expected hash only.
    private func load(_ message: MessageRef, play: Bool) {
        guard let app, let client = app.client else { return }
        task?.cancel()
        selected = message
        outcome = nil
        activity.error = nil
        wantsPlay = play
        let book = app.book
        let profile = app.profile
        let source = Self.source(of: message, in: book)
        self.source = source
        guard let source else {
            outcome = .missingAtoms(message, missing: Self.missingAtoms(of: message, in: book))
            wantsPlay = true  // played when the atoms arrive while Messages is visible
            return
        }
        let action = source.action
        let referent = source.referent
        let bookID = source.bookID
        let heldOutMessages = app.grammar?.heldoutMessages
        task = activity.run("compose") { [weak self] in
            if message.isHeldout {
                // Show the refusal: compose must fail with E_HELDOUT. Any audio returned by
                // mistake is dropped unread (never verified, never played).
                var refusal: BridgeError?
                var unexpectedAudio = false
                do {
                    _ = try await client.compose(action: action, referent: referent, profile: profile, bookID: bookID)
                    unexpectedAudio = true
                } catch let error as BridgeError {
                    refusal = error
                }
                try Task.checkCancellation()
                let hash = try await client.compositeHash(
                    action: action, referent: referent, profile: profile, bookID: bookID)
                try Task.checkCancellation()
                guard let self else { return }
                self.outcome = .heldOut(message, refusal: refusal, unexpectedAudio: unexpectedAudio, hash: hash)
                self.wantsPlay = false
            } else {
                // Before any audio exists: the expected hash (composite_hash returns no
                // audio), then the held-out messages of this book with the same hash.
                // When there is one, the message is not composed.
                guard let heldOutMessages else { throw AppError("The grammar is not loaded yet. Try again.") }
                let expected = try await client.compositeHash(
                    action: action, referent: referent, profile: profile, bookID: bookID)
                try Task.checkCancellation()
                let twins = try await Self.heldOutTwins(
                    of: expected.compositeSHA256, among: heldOutMessages, in: book, profile: profile, client: client)
                try Task.checkCancellation()
                if !twins.isEmpty {
                    guard let self else { return }
                    self.outcome = .sameAsHeldOut(message, heldOut: twins, hash: expected)
                    self.wantsPlay = false
                    return
                }
                let composed = try await client.compose(
                    action: action, referent: referent, profile: profile, bookID: bookID)
                let clip = try await Offload.clip(composed.audio)
                try Task.checkCancellation()
                guard let self else { return }
                self.outcome = .composed(message, composed, clip, expected: expected)
                if self.wantsPlay, app.isMessagesVisible { app.play(clip) }
                self.wantsPlay = false
            }
        }
    }

    /// The atoms `message` is made from in `book`, or `nil` when one is missing.
    static func source(of message: MessageRef, in book: ScratchBook) -> Source? {
        guard let action = book.reference(message.action), let referent = book.reference(message.referent)
        else { return nil }
        return Source(
            action: action, referent: referent, bookID: book.origin,
            bookAtoms: message.isHeldout ? nil : book.atoms)
    }

    /// The atoms of `message` that `book` does not hold, action first.
    static func missingAtoms(of message: MessageRef, in book: ScratchBook) -> [String] {
        [message.action, message.referent].filter { book.atoms[$0] == nil }
    }

    /// The held-out messages whose composite in `book` has the PCM hash `hash`: a trained
    /// message with that hash would be their audio. Each held-out message with both atoms
    /// in the book is hashed with `composite_hash`, which never returns audio. A held-out
    /// message the engine cannot hash (an atom that does not render, for example) has no
    /// audio, so it cannot be equal; any other failure is thrown (nothing is composed).
    static func heldOutTwins(
        of hash: String, among heldOut: [MessageRef], in book: ScratchBook, profile: Profile, client: BridgeClient
    ) async throws -> [MessageRef] {
        var twins: [MessageRef] = []
        for message in heldOut where message.isHeldout {
            guard let source = source(of: message, in: book) else { continue }
            do {
                let result = try await client.compositeHash(
                    action: source.action, referent: source.referent, profile: profile, bookID: source.bookID)
                if result.compositeSHA256 == hash { twins.append(message) }
            } catch let error as BridgeError where error.engineType != nil {
                continue
            }
        }
        return twins
    }

    /// Stops the composed clip of the current outcome if it is the sound that plays.
    private func stopOwnClip() {
        guard let app, case .composed(_, _, let clip, _)? = outcome, app.isPlaying(clip) else { return }
        app.stopPlayback()
    }
}

// MARK: - View

struct MessagesView: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        @Bindable var messages = app.messages
        SectionPage(.messages, summary: "The fixed 4 \u{00D7} 4 message matrix of each family: action atom (rows) then 200 ms of silence then referent atom (columns). Trained cells compose from the scratch book; held-out cells are never composed: the engine refuses them and only their expected hash is shown.") {
            Card("Matrix", systemImage: "square.grid.4x3.fill",
                 subtitle: "Profile \(app.profile.rawValue) \u{00B7} scratch book: \(app.book.count) of 16 atoms\(app.book.origin.map { " (\($0))" } ?? "")") {
                HStack {
                    Picker("Family", selection: $messages.family) {
                        ForEach(AtomSlots.families, id: \.self) { Text("Family \($0)").tag($0) }
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    .frame(width: 200)
                    .onChange(of: messages.family) { messages.familyDidChange() }
                    if app.book.isEmpty {
                        ActionButton("Load DEMO Book", systemImage: "books.vertical",
                                     isRunning: app.activity.isRunning("demoBook")) {
                            app.loadDemoBook()
                        }
                        .requiresBridge()
                    }
                }
            } content: {
                if let grammar = app.grammar {
                    MatrixGrid(grammar: grammar, family: messages.family)
                    Legend()
                } else if app.isReady {
                    ProgressView("Loading the grammar\u{2026}")
                } else {
                    Text("The matrix loads from the engine's grammar once the bridge is ready.")
                        .foregroundStyle(.secondary)
                }
            }
            MessageDetailCard()
        }
    }
}

private struct MatrixGrid: View {
    @Environment(AppModel.self) private var app
    let grammar: Grammar
    let family: String

    var body: some View {
        let cells = Dictionary(
            grammar.messages.filter { $0.family == family }.map { message in
                ("\(AtomSlots.index(of: message.action) ?? 0)-\(AtomSlots.index(of: message.referent) ?? 0)", message)
            },
            uniquingKeysWith: { first, _ in first })
        Grid(horizontalSpacing: 8, verticalSpacing: 8) {
            GridRow {
                Text("")
                ForEach(1...4, id: \.self) { r in
                    Text("\(family)-r\(r)").font(.callout.monospaced().weight(.semibold)).foregroundStyle(.secondary)
                }
            }
            ForEach(1...4, id: \.self) { a in
                GridRow {
                    Text("\(family)-a\(a)").font(.callout.monospaced().weight(.semibold)).foregroundStyle(.secondary)
                    ForEach(1...4, id: \.self) { r in
                        if let message = cells["\(a)-\(r)"] {
                            MatrixCell(message: message)
                        } else {
                            Text("\u{2014}")
                        }
                    }
                }
            }
        }
        .requiresBridge()
    }
}

private struct MatrixCell: View {
    @Environment(AppModel.self) private var app
    let message: MessageRef

    var body: some View {
        let selected = app.messages.selectedID == message.messageID
        let tint = MessageStyle.tint(message)
        let book = app.book
        let present = book.atoms[message.action] != nil && book.atoms[message.referent] != nil
        Button {
            app.messages.select(message)
        } label: {
            VStack(spacing: 4) {
                Text(message.messageID).font(.callout.monospaced().weight(.semibold))
                Text(MessageStyle.caption(message)).font(.caption)
                Image(systemName: present ? (message.isHeldout ? "lock.fill" : "speaker.wave.2.fill") : "questionmark.circle")
                    .font(.caption)
                    .foregroundStyle(present ? tint : Color.secondary)
            }
            .frame(maxWidth: .infinity, minHeight: 74)
            .foregroundStyle(.primary)
            .background(tint.opacity(selected ? 0.32 : 0.13), in: RoundedRectangle(cornerRadius: 8))
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .strokeBorder(selected ? tint : tint.opacity(0.3), lineWidth: selected ? 2 : 1))
            .contentShape(RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
        .help(message.isHeldout
              ? "Held out (\(message.status)): shows the engine's refusal and the expected hash"
              : "Trained (\(message.status)): compose \(message.action) + gap + \(message.referent) and play")
        // The selection and the missing atoms are shown by color and icon only: say them.
        .accessibilityLabel(message.messageID)
        .accessibilityValue(accessibilityValue(book: book))
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private func accessibilityValue(book: ScratchBook) -> String {
        let missing = MessagesModel.missingAtoms(of: message, in: book)
        let atoms = missing.isEmpty ? "both atoms in the book" : "missing atoms: \(missing.joined(separator: ", "))"
        return "\(MessageStyle.caption(message).replacingOccurrences(of: " \u{00B7} ", with: ", ")), \(atoms)"
    }
}

enum MessageStyle {
    static func tint(_ message: MessageRef) -> Color {
        if message.isHeldout { return (message.heldoutSet ?? "").hasPrefix("H-W") ? .pink : .orange }
        switch message.trainingWave {
        case 1: return .green
        case 2: return .teal
        default: return .blue
        }
    }

    static func caption(_ message: MessageRef) -> String {
        if message.isHeldout { return "held out \u{00B7} \(message.heldoutSet ?? message.status)" }
        return "trained \u{00B7} wave \(message.trainingWave.map(String.init) ?? "?")"
    }
}

private struct Legend: View {
    var body: some View {
        HStack(spacing: 14) {
            swatch(.green, "Trained, wave 1")
            swatch(.teal, "wave 2")
            swatch(.blue, "wave 3")
            swatch(.orange, "Held out, H-V sets")
            swatch(.pink, "H-W sets")
            Spacer()
            Label("missing atom", systemImage: "questionmark.circle").foregroundStyle(.secondary)
        }
        .font(.caption)
    }

    private func swatch(_ color: Color, _ text: String) -> some View {
        HStack(spacing: 4) {
            RoundedRectangle(cornerRadius: 3).fill(color.opacity(0.5)).frame(width: 12, height: 12)
            Text(text)
        }
    }
}

private struct MessageDetailCard: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        let model = app.messages
        Card("Message", systemImage: "text.bubble") {
            if model.activity.isRunning("compose") { ProgressView().controlSize(.small) }
        } content: {
            if let error = model.activity.error {
                ErrorBanner(message: error) { model.activity.error = nil }
            }
            switch model.outcome {
            case nil:
                if let id = model.selectedID, model.hasFailed {
                    HStack(spacing: 10) {
                        Text("Could not get \(id) from the engine. Click its cell, or Try Again, to ask again.")
                            .foregroundStyle(.secondary)
                        Button("Try Again") { model.retry() }
                            .requiresBridge()
                    }
                } else {
                    Text(model.selectedID == nil ? "Select a cell of the matrix." : "Asking the engine\u{2026}")
                        .foregroundStyle(.secondary)
                }
            case .missingAtoms(let message, let missing)?:
                MissingAtomsView(message: message, missing: missing)
            case .composed(let message, let composed, let clip, let expected)?:
                ComposedView(message: message, composed: composed, clip: clip, expected: expected)
            case .heldOut(let message, let refusal, let unexpectedAudio, let hash)?:
                HeldOutView(message: message, refusal: refusal, unexpectedAudio: unexpectedAudio, hash: hash)
            case .sameAsHeldOut(let message, let heldOut, let hash)?:
                SameAsHeldOutView(message: message, heldOut: heldOut, hash: hash)
            }
        }
    }
}

private struct MessageTitle: View {
    let message: MessageRef

    var body: some View {
        HStack(spacing: 10) {
            Text(message.messageID).font(.title3.monospaced().weight(.semibold))
            Pill(text: MessageStyle.caption(message), tint: MessageStyle.tint(message))
            Spacer()
            Text("action \(message.action) \u{00B7} referent \(message.referent)")
                .font(.callout.monospaced())
                .foregroundStyle(.secondary)
        }
    }
}

private struct MissingAtomsView: View {
    @Environment(AppModel.self) private var app
    let message: MessageRef
    let missing: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            MessageTitle(message: message)
            Label {
                Text("\(message.messageID) needs both \(message.action) and \(message.referent) in the \(app.profile.rawValue) scratch book. Missing: \(missing.joined(separator: ", ")). Load the synthetic DEMO book, or add recipes from the Recipe Lab (Add to Book).")
                    .fixedSize(horizontal: false, vertical: true)
            } icon: {
                Image(systemName: "info.circle").foregroundStyle(.blue)
            }
            .noticeStyle(.blue)
            HStack {
                ActionButton("Load DEMO Book", systemImage: "books.vertical",
                             isRunning: app.activity.isRunning("demoBook")) {
                    app.loadDemoBook()
                }
                Button("Open Validator & Book") { app.selection = .validator }
            }
            .requiresBridge()
        }
    }
}

private struct ComposedView: View {
    let message: MessageRef
    let composed: ComposeResult
    let clip: AudioClip
    let expected: CompositeHashResult

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            MessageTitle(message: message)
            HStack {
                PlayButton(clip: clip, title: "Play").buttonStyle(.borderedProminent)
                Text("\(Fmt.seconds(composed.durationS)) \u{00B7} \(Fmt.int(composed.nSamples)) samples")
                    .font(.callout.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            ClipWaveform(clip: clip, sampleCount: composed.nSamples, spans: spans, height: 190)
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
                InfoRow("Duration", "\(Fmt.seconds(composed.durationS)) (\(Fmt.int(composed.nSamples)) samples)")
                InfoRow("Action", "\(message.action): samples 0 to \(Fmt.int(composed.actionSamples)) (\(Fmt.ms(samples: composed.actionSamples)))")
                InfoRow("Gap", "\(Fmt.int(composed.referentOnset - composed.actionSamples)) zero samples (\(Fmt.ms(samples: composed.referentOnset - composed.actionSamples)))")
                InfoRow("Referent", "\(message.referent): onset \(Fmt.int(composed.referentOnset)), \(Fmt.int(composed.referentSamples)) samples (\(Fmt.ms(samples: composed.referentSamples)))")
                InfoRow("pcm_sha256") { HashText(hash: clip.audio.pcmSHA256) }
                InfoRow("file_sha256") { HashText(hash: clip.audio.fileSHA256) }
                InfoRow("composite_hash") {
                    HStack(spacing: 8) {
                        HashText(hash: expected.compositeSHA256)
                        PassFail(ok: expected.compositeSHA256 == clip.audio.pcmSHA256,
                                 text: expected.compositeSHA256 == clip.audio.pcmSHA256
                                    ? "equals the composed waveform" : "differs from the composed waveform")
                    }
                }
            }
        }
    }

    private var spans: [WaveSpan] {
        [
            WaveSpan(id: "action", range: 0..<composed.actionSamples, kind: .event,
                     title: "action \(message.action)", detail: Fmt.ms(samples: composed.actionSamples),
                     color: SpanPalette.action),
            WaveSpan(id: "gap", range: composed.actionSamples..<composed.referentOnset, kind: .gap,
                     title: "gap", detail: Fmt.ms(samples: composed.referentOnset - composed.actionSamples)),
            WaveSpan(id: "referent", range: composed.referentOnset..<(composed.referentOnset + composed.referentSamples),
                     kind: .event, title: "referent \(message.referent)",
                     detail: Fmt.ms(samples: composed.referentSamples), color: SpanPalette.referent),
        ]
    }
}

private struct HeldOutView: View {
    let message: MessageRef
    let refusal: BridgeError?
    let unexpectedAudio: Bool
    let hash: CompositeHashResult?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            MessageTitle(message: message)
            if unexpectedAudio {
                ErrorBanner(message: "The engine composed a held-out message. Its audio was discarded without being decoded or played. This is a bug in the engine or the bridge.")
            } else if let refusal {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "hand.raised.fill").foregroundStyle(.orange).font(.title3)
                    VStack(alignment: .leading, spacing: 6) {
                        HStack(spacing: 8) {
                            Text("compose refused").font(.headline)
                            if let type = refusal.engineType { CodeChip(code: type, tint: .orange) }
                            if let code = refusal.engineCode { CodeChip(code: code, tint: .orange) }
                        }
                        Text(refusal.engineMessage).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                        if refusal.engineCode != "E_HELDOUT" {
                            Text("Expected E_HELDOUT.").foregroundStyle(.red)
                        }
                    }
                }
                .noticeStyle(.orange)
            }
            Label("A held-out message never exists as audio: nothing was decoded or played. composite_hash gives its expected hash only.",
                  systemImage: "speaker.slash")
                .foregroundStyle(.secondary)
            if let hash {
                Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
                    InfoRow("Expected hash") { HashText(hash: hash.compositeSHA256, length: 24) }
                    InfoRow("Length", "\(Fmt.int(hash.nSamples)) samples (\(Fmt.seconds(hash.durationS)))")
                    InfoRow("is_heldout", Fmt.yesNo(hash.isHeldout))
                }
            }
        }
    }
}

/// A trained message that the scratch book would make into a held-out message's audio.
private struct SameAsHeldOutView: View {
    @Environment(AppModel.self) private var app
    let message: MessageRef
    let heldOut: [MessageRef]
    let hash: CompositeHashResult

    var body: some View {
        let ids = heldOut.map(\.messageID).joined(separator: ", ")
        VStack(alignment: .leading, spacing: 12) {
            MessageTitle(message: message)
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "hand.raised.fill").foregroundStyle(.orange).font(.title3)
                VStack(alignment: .leading, spacing: 6) {
                    Text("Not composed: this pair is also held-out \(ids)").font(.headline)
                    Text("In this scratch book, \(message.messageID) (\(message.action) + \(message.referent)) has the same composite hash as held-out \(ids): its atoms make the same sounds, for example because two slots hold the same recipe. A held-out message never exists as audio, so \(message.messageID) is not composed or played either. Give each atom its own recipe (Validator & Book), or load the DEMO book.")
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .noticeStyle(.orange)
            Label("Nothing was composed, decoded or played. composite_hash gives the hash only.",
                  systemImage: "speaker.slash")
                .foregroundStyle(.secondary)
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
                InfoRow("Composite hash") { HashText(hash: hash.compositeSHA256, length: 24) }
                InfoRow("Same as", ids)
            }
            HStack {
                Button("Open Validator & Book") { app.selection = .validator }
                ActionButton("Load DEMO Book", systemImage: "books.vertical",
                             isRunning: app.activity.isRunning("demoBook")) {
                    app.loadDemoBook()
                }
            }
            .requiresBridge()
        }
    }
}
