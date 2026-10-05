import AVSoundDemoCore
import Observation
import SwiftUI

// MARK: - Model

@MainActor
@Observable
final class StoreModel {
    struct Event: Identifiable {
        enum Kind { case ok, refused, failed, info }

        let id = UUID()
        let date = Date()
        let kind: Kind
        let title: String
        var detail: String?
    }

    /// One log record for the table.
    struct RecordRow: Identifiable, Hashable {
        let id: Int
        let seq: String
        let event: String
        let atomID: String
        let prevSHA256: String?
        let recordSHA256: String?
        let timestamp: String
        let raw: JSONValue

        init(index: Int, record: JSONValue) {
            id = index
            seq = record["seq"]?.intValue.map(String.init) ?? "?"
            event = record["event"]?.stringValue ?? "?"
            atomID = record["atom_id"]?.stringValue ?? ""
            prevSHA256 = record["prev_sha256"]?.stringValue
            recordSHA256 = record["record_sha256"]?.stringValue
            timestamp = record["timestamp"]?.stringValue ?? ""
            raw = record
        }
    }

    /// What the section keeps of a book it has left (another profile's book was
    /// selected), so that coming back to it neither loses its recorded head and tamper
    /// state nor takes the other book's.
    private struct BookMemory {
        var recordedHead: String?
        var isTampered: Bool
    }

    @ObservationIgnored weak var app: AppModel?

    /// The bridge's temp store root (after `store_reset`).
    private(set) var root: String?
    private(set) var bookID: String?
    private(set) var bookProfile: Profile?
    /// The chain head returned by the last write to the current book. Verification can
    /// require it, which is how a cut of a self-consistent log is detected (PROTOCOL.md,
    /// "Damaged books").
    private(set) var recordedHead: String?
    private(set) var listing: StoreListResult?
    /// Why the last `store_list` failed (a damaged book fails the store's integrity checks).
    private(set) var listingError: String?
    /// The log as of the last successful `store_records`; emptied when a read fails, so
    /// the table never shows records from before the damage as if they were current.
    private(set) var records: [RecordRow] = []
    private(set) var recordsError: String?
    private(set) var verification: StoreVerifyResult?
    private(set) var verifiedAgainstHead: String?
    private(set) var events: [Event] = []
    /// Set by a tamper action on the current book: listings no longer update its
    /// recorded head.
    private(set) var isTampered = false
    /// The recorded head and tamper state of the other books of this store, by book ID.
    @ObservationIgnored private var otherBooks: [String: BookMemory] = [:]
    var commitAtomID = "K-a1" {
        didSet { if oldValue != commitAtomID { semanticLabel = SemanticLabels.demoLabel(for: commitAtomID) } }
    }
    /// `nil` sends `semantic_label: null` (the store refuses it with E_LABEL).
    var semanticLabel: String? = SemanticLabels.demoLabel(for: "K-a1")
    var verifyAgainstRecordedHead = true
    var recordSelection: RecordRow.ID?
    let activity = Activity()

    var hasBook: Bool { bookID != nil }

    /// The bridge restarted: its temp store is gone.
    func bridgeDidStop() {
        guard root != nil || bookID != nil else { return }
        root = nil
        otherBooks = [:]
        bookID = nil
        bookProfile = nil
        recordedHead = nil
        isTampered = false
        listing = nil
        listingError = nil
        records = []
        recordSelection = nil
        recordsError = nil
        verification = nil
        verifiedAgainstHead = nil
        log(.info, "The bridge stopped; its temp store was removed.")
    }

    func reset() {
        guard let client = app?.client else { return }
        activity.run("reset") { [weak self] in
            let result = try await client.storeReset()
            guard let self else { return }
            self.root = result.root
            self.otherBooks = [:]
            self.bookID = nil
            self.bookProfile = nil
            self.recordedHead = nil
            self.isTampered = false
            self.listing = nil
            self.listingError = nil
            self.records = []
            self.recordSelection = nil
            self.recordsError = nil
            self.verification = nil
            self.verifiedAgainstHead = nil
            self.log(.ok, "Fresh temp store", detail: result.root)
        }
    }

    func createBook() {
        guard let app, let client = app.client else { return }
        let profile = app.profile
        let bookID = "DEMO-\(profile.rawValue)"
        activity.run("create") { [weak self] in
            do {
                let result = try await client.storeCreate(bookID: bookID, profile: profile)
                guard let self else { return }
                self.select(book: result.bookID, profile: profile, createdHead: result.chainHead)
                self.log(.ok, "Created \(result.bookID) (\(profile.rawValue))", detail: "chain head \(Fmt.shortHash(result.chainHead))")
            } catch let error as BridgeError where error.engineType != nil {
                self?.log(.refused, "create_book: \(error.engineTitle ?? "refused")", detail: error.engineMessage)
                if error.engineType == "BookExists" {
                    // Back to a book of this store: its own head and state, not the
                    // previous book's.
                    self?.select(book: bookID, profile: profile, createdHead: nil)
                }
            }
            await self?.refresh()
        }
    }

    func commitCurrent() {
        guard let app, let bookID else { return }
        let recipe = app.recipe
        let atomID = commitAtomID
        let label = semanticLabel
        activity.run("commit") { [weak self] in
            await self?.commit(bookID: bookID, atomID: atomID, label: label, recipe: recipe)
            await self?.refresh()
        }
    }

    /// Commits every atom of the scratch book of the store book's profile, in slot order.
    func commitBook() {
        guard let app, let bookID, let profile = bookProfile else { return }
        let references = (app.books[profile] ?? ScratchBook()).references
        guard !references.isEmpty else {
            log(.info, "The \(profile.rawValue) scratch book is empty", detail: "Load the DEMO book in Validator & Book first.")
            return
        }
        activity.run("commitBook") { [weak self] in
            for reference in references {
                try Task.checkCancellation()
                await self?.commit(
                    bookID: bookID, atomID: reference.refID,
                    label: SemanticLabels.demoLabel(for: reference.refID), recipe: reference.recipe)
            }
            await self?.refresh()
        }
    }

    /// Commits a different recipe (same label) to an atom that is already committed: the
    /// store must refuse it (`OverwriteRejected`) and log the attempt.
    func tryOverwrite() {
        guard let bookID, let entry = listing?.entries.first else { return }
        var recipe = entry.recipe
        if let pitch = recipe.pitches.first { recipe.pitches[0] = pitch < 6 ? pitch + 1 : pitch - 1 }
        let label = records.last { $0.event == "commit" && $0.atomID == entry.atomID }?
            .raw["semantic_label"]?.stringValue
        activity.run("overwrite") { [weak self] in
            await self?.commit(
                bookID: bookID, atomID: entry.atomID, label: label, recipe: recipe,
                note: "overwrite attempt on \(entry.atomID)")
            await self?.refresh()
        }
    }

    func freeze() {
        guard let client = app?.client, let bookID else { return }
        activity.run("freeze") { [weak self] in
            do {
                let result = try await client.storeFreeze(bookID: bookID)
                self?.record(head: result.chainHead, of: bookID)
                self?.log(.ok, "Froze \(bookID)", detail: "chain head \(Fmt.shortHash(result.chainHead)); further commits are refused")
            } catch let error as BridgeError where error.engineType != nil {
                self?.log(.refused, "freeze: \(error.engineTitle ?? "refused")", detail: error.engineMessage)
            }
            await self?.refresh()
        }
    }

    func verify() {
        activity.run("verify") { [weak self] in
            await self?.runVerify()
        }
    }

    /// Damages the temp store, then verifies to show the detection.
    func tamper(_ kind: StoreTamperKind) {
        guard let client = app?.client, let bookID else { return }
        activity.run("tamper") { [weak self] in
            let result = try await client.storeTamper(bookID: bookID, kind: kind)
            if self?.bookID == bookID {
                self?.isTampered = true
            } else {
                self?.otherBooks[bookID]?.isTampered = true
            }
            self?.log(.info, "Tampered \(bookID): \(Self.title(kind))", detail: result.done)
            await self?.runVerify()
            await self?.refresh()
        }
    }

    func refreshNow() {
        activity.run("refresh") { [weak self] in await self?.refresh() }
    }

    static func title(_ kind: StoreTamperKind) -> String {
        switch kind {
        case .flipBlobByte: "Flip a blob byte"
        case .editLogLine: "Edit a log line"
        case .truncateLog: "Truncate the log"
        }
    }

    // MARK: Steps

    /// Makes `id` the current book. The book being left keeps its recorded head and tamper
    /// state for later; the new book gets its own (a new book: `createdHead`; a book seen
    /// before: what it had; otherwise none, and `refresh()` takes the log's head). The
    /// left book's verification, listing and records are not shown under the new book.
    private func select(book id: String, profile: Profile, createdHead: String?) {
        let isOtherBook = id != bookID
        if isOtherBook, let current = bookID {
            otherBooks[current] = BookMemory(recordedHead: recordedHead, isTampered: isTampered)
        }
        if let createdHead {
            otherBooks[id] = nil
            recordedHead = createdHead
            isTampered = false
        } else if isOtherBook {
            let memory = otherBooks.removeValue(forKey: id)
            recordedHead = memory?.recordedHead
            isTampered = memory?.isTampered ?? false
        }
        if isOtherBook || createdHead != nil {
            listing = nil
            listingError = nil
            records = []
            recordSelection = nil
            recordsError = nil
            verification = nil
            verifiedAgainstHead = nil
        }
        bookID = id
        bookProfile = profile
    }

    /// A write to `book` returned `head`: it is the recorded head of that book (which may
    /// no longer be the current one).
    private func record(head: String, of book: String) {
        if book == bookID {
            recordedHead = head
        } else {
            otherBooks[book]?.recordedHead = head
        }
    }

    private func commit(bookID: String, atomID: String, label: String?, recipe: Recipe, note: String? = nil) async {
        guard let client = app?.client else { return }
        let what = note ?? "commit \(atomID)"
        do {
            let result = try await client.storeCommit(bookID: bookID, atomID: atomID, semanticLabel: label, recipe: recipe)
            record(head: result.chainHead, of: bookID)
            let detail = "commit_index \(result.entry.commitIndex) \u{00B7} pcm \(Fmt.shortHash(result.entry.pcmSHA256)) \u{00B7} head \(Fmt.shortHash(result.chainHead))"
            if note != nil {
                log(.failed, "\(what): the store accepted it (\(result.outcome))", detail: detail)
            } else {
                log(.ok, result.isNoop ? "\(atomID): recommit (no-op)" : "\(atomID): committed", detail: detail)
            }
        } catch let error as BridgeError where error.engineType != nil {
            var detail = error.engineMessage
            if let validation = error.validationDetails, !validation.codes.isEmpty {
                detail += " [\(validation.codes.joined(separator: ", "))]"
            }
            log(.refused, "\(what): \(error.engineTitle ?? "refused")", detail: detail)
        } catch {
            log(.failed, "\(what) failed", detail: userMessage(error))
        }
    }

    private func runVerify() async {
        guard let client = app?.client, let bookID else { return }
        let expected = verifyAgainstRecordedHead ? recordedHead : nil
        do {
            let result = try await client.storeVerify(bookID: bookID, expectedHead: expected)
            guard bookID == self.bookID else { return }  // another book was selected meanwhile
            verification = result
            verifiedAgainstHead = expected
            if result.ok {
                log(.ok, "Verified \(bookID): intact", detail: expected.map { "against head \(Fmt.shortHash($0))" })
            } else {
                log(.failed, "Verified \(bookID): \(result.issues.count) issue(s) detected",
                    detail: result.issues.map(\.code).joined(separator: ", "))
            }
        } catch {
            log(.failed, "store_verify failed", detail: userMessage(error))
        }
    }

    private func refresh() async {
        guard let client = app?.client, let bookID else { return }
        do {
            let listing = try await client.storeList(bookID: bookID)
            guard bookID == self.bookID else { return }  // another book was selected meanwhile
            self.listing = listing
            listingError = nil
            // Rejected commits append log records too; follow the head until the store is
            // tampered with, so verification can catch a truncated log.
            if !isTampered { recordedHead = listing.chainHead }
        } catch {
            guard bookID == self.bookID else { return }
            listing = nil
            listingError = userMessage(error)
        }
        do {
            let raw = try await client.storeRecords(bookID: bookID)
            guard bookID == self.bookID else { return }
            records = raw.enumerated().map { RecordRow(index: $0.offset, record: $0.element) }
            recordsError = nil
        } catch {
            guard bookID == self.bookID else { return }
            // The store refuses to read a damaged log; do not keep showing the old records.
            records = []
            recordSelection = nil
            recordsError = userMessage(error)
        }
    }

    private func log(_ kind: Event.Kind, _ title: String, detail: String? = nil) {
        events.insert(Event(kind: kind, title: title, detail: detail), at: 0)
        if events.count > 200 { events.removeLast(events.count - 200) }
    }
}

// MARK: - View

struct StoreView: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        SectionPage(.store, summary: "The append-only vocabulary store, in a temp directory the bridge owns (DEMO books only). Commit atoms, try to overwrite one, freeze the book, then damage the files and watch verification catch it.") {
            StoreActionsCard()
            StoreStateCard()
            StoreRecordsCard()
            StoreEventsCard()
        }
    }
}

private struct StoreActionsCard: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        @Bindable var store = app.store
        let activity = store.activity
        Card("Actions", systemImage: "hammer",
             subtitle: store.hasBook ? "Book \(store.bookID ?? "") (\(store.bookProfile?.rawValue ?? "?"))" : "Reset the store, then create the DEMO book for the current profile (\(app.profile.rawValue)).") {
            if activity.isBusy { ProgressView().controlSize(.small) }
        } content: {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    ActionButton("Reset Store", systemImage: "arrow.counterclockwise.circle",
                                 isRunning: activity.isRunning("reset")) { store.reset() }
                    ActionButton("Create DEMO-\(app.profile.rawValue)", systemImage: "plus.rectangle.on.folder",
                                 isRunning: activity.isRunning("create"), prominent: !store.hasBook) { store.createBook() }
                    Spacer()
                    Button {
                        store.refreshNow()
                    } label: {
                        Label("Refresh", systemImage: "arrow.clockwise")
                    }
                    .disabled(!store.hasBook)
                }
                Divider()
                HStack(spacing: 10) {
                    Picker("Atom", selection: $store.commitAtomID) {
                        ForEach(AtomSlots.all, id: \.self) { Text($0).tag($0) }
                    }
                    .frame(width: 140)
                    Picker("Label", selection: $store.semanticLabel) {
                        ForEach(SemanticLabels.labels(for: store.commitAtomID), id: \.self) { label in
                            Text(label).tag(Optional(label))
                        }
                        Divider()
                        Text("none (E_LABEL)").tag(String?.none)
                    }
                    .frame(width: 210)
                    .help("The semantic label bound to the atom (store ontology); each label once per book")
                    ActionButton("Commit Current Recipe", systemImage: "square.and.arrow.down.on.square",
                                 isRunning: activity.isRunning("commit")) { store.commitCurrent() }
                        .help(app.recipe.summary)
                }
                .disabled(!store.hasBook)
                HStack {
                    ActionButton("Commit All Book Atoms", systemImage: "square.stack.3d.down.right",
                                 isRunning: activity.isRunning("commitBook")) { store.commitBook() }
                        .help("Commit every atom of the \(store.bookProfile?.rawValue ?? app.profile.rawValue) scratch book, each with its demo label (K-a1 ADD_ONE, K-r1 A, ...)")
                    ActionButton("Try an Overwrite", systemImage: "exclamationmark.arrow.triangle.2.circlepath",
                                 isRunning: activity.isRunning("overwrite")) { store.tryOverwrite() }
                        .disabled((store.listing?.entries.isEmpty ?? true))
                        .help("Commit a changed recipe to an already committed atom: expect OverwriteRejected")
                    ActionButton("Freeze", systemImage: "snowflake", isRunning: activity.isRunning("freeze")) { store.freeze() }
                }
                .disabled(!store.hasBook)
                Divider()
                HStack {
                    ActionButton("Verify", systemImage: "checkmark.shield", isRunning: activity.isRunning("verify"),
                                 prominent: store.hasBook) { store.verify() }
                    Toggle("Require the recorded chain head", isOn: $store.verifyAgainstRecordedHead)
                        .help("Pass expected_head = the head returned by the last write to this book. Needed to detect a log cut that leaves a self-consistent log: a cut of a commit, or of a refused commit logged after the freeze (E_ANCHOR). A cut of the freeze record (E_MARKER) or of the only record, create_book (E_EVENT), is found without it.")
                    Spacer()
                    Text("Tamper:").foregroundStyle(.secondary)
                    ForEach(StoreTamperKind.allCases) { kind in
                        Button(StoreModel.title(kind)) { store.tamper(kind) }
                            .tint(.red)
                    }
                    .disabled(activity.isRunning("tamper"))
                }
                .disabled(!store.hasBook)
            }
            .requiresBridge()
            if let error = activity.error {
                ErrorBanner(message: error) { activity.error = nil }
            }
        }
    }
}

private struct StoreStateCard: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        let store = app.store
        Card("State", systemImage: "info.circle") {
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
                InfoRow("Temp root") {
                    Text(store.root ?? "the bridge's default temp store (Reset Store makes a fresh one)")
                        .font(.callout.monospaced())
                        .foregroundStyle(store.root == nil ? Color.secondary : Color.primary)
                        .textSelection(.enabled)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                InfoRow("Book", store.bookID.map { "\($0) \u{00B7} \(store.bookProfile?.rawValue ?? "")" } ?? "none")
                if let error = store.listingError {
                    InfoRow("Entries") {
                        Text("store_list refused to read the book: \(error)")
                            .foregroundStyle(.red)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                if let listing = store.listing {
                    InfoRow("Entries", listing.entries.isEmpty
                            ? "none committed"
                            : "\(listing.entries.count) committed: \(listing.entries.map(\.atomID).joined(separator: " "))")
                    InfoRow("Frozen / void", "\(Fmt.yesNo(listing.isFrozen)) / \(Fmt.yesNo(listing.isVoid))")
                    InfoRow("Chain head (log)") { HashText(hash: listing.chainHead) }
                }
                InfoRow("Recorded head") { HashText(hash: store.recordedHead) }
                InfoRow("Verification") {
                    if let verification = store.verification {
                        VStack(alignment: .leading, spacing: 4) {
                            PassFail(ok: verification.ok,
                                     text: verification.ok ? "intact" : "\(verification.issues.count) issue(s) detected")
                            ForEach(Array(verification.issues.enumerated()), id: \.offset) { _, issue in
                                HStack(alignment: .firstTextBaseline, spacing: 8) {
                                    CodeChip(code: issue.code)
                                    if let line = issue.line { Text("line \(line)").foregroundStyle(.secondary).monospacedDigit() }
                                    Text(issue.message).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                                }
                            }
                            Text(store.verifiedAgainstHead.map { "expected_head \(Fmt.shortHash($0))" } ?? "without expected_head")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    } else {
                        Text("not verified yet").foregroundStyle(.secondary)
                    }
                }
            }
        }
    }
}

private struct StoreRecordsCard: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        @Bindable var store = app.store
        Card("Log records", systemImage: "list.number",
             subtitle: "The book's log: one canonical JSON record per line, prev_sha256 chaining each line to the one before. The detail shows the selected record with the log's own numbers (1.0 stays 1.0), indented.") {
            if let error = store.recordsError {
                ErrorBanner(message: "store_records: \(error)")
                Text("The store does not read a log that fails its integrity checks, so no records are shown. Verify lists the damage.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            Table(store.records, selection: $store.recordSelection) {
                TableColumn("seq") { row in Text(row.seq).monospacedDigit() }.width(36)
                TableColumn("Event") { row in
                    Text(row.event).foregroundStyle(Self.color(row.event))
                }
                .width(min: 110, ideal: 150)
                TableColumn("Atom") { row in Text(row.atomID).font(.body.monospaced()) }.width(54)
                TableColumn("prev_sha256") { row in
                    Text(Fmt.shortHash(row.prevSHA256)).font(.body.monospaced()).help(row.prevSHA256 ?? "")
                }
                .width(min: 110, ideal: 130)
                TableColumn("record_sha256") { row in
                    Text(Fmt.shortHash(row.recordSHA256)).font(.body.monospaced()).help(row.recordSHA256 ?? "")
                }
                .width(min: 110, ideal: 130)
                TableColumn("Timestamp") { row in Text(row.timestamp).font(.caption.monospaced()) }
                    .width(min: 150, ideal: 190)
            }
            .frame(height: 240)
            if let id = store.recordSelection, let row = store.records.first(where: { $0.id == id }) {
                CodeBlock(text: row.raw.prettyString, maxHeight: 220)
            }
        }
    }

    static func color(_ event: String) -> Color {
        switch event {
        case "commit", "create_book": .primary
        case "recommit_noop": .secondary
        case "freeze", "void": .blue
        default: .orange
        }
    }
}

private struct StoreEventsCard: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        Card("Activity", systemImage: "clock.arrow.circlepath") {
            if app.store.events.isEmpty {
                Text("Nothing yet.").foregroundStyle(.secondary)
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(app.store.events.prefix(60)) { event in
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Image(systemName: Self.icon(event.kind)).foregroundStyle(Self.tint(event.kind))
                            VStack(alignment: .leading, spacing: 2) {
                                Text(event.title).fontWeight(.medium)
                                if let detail = event.detail {
                                    Text(detail).font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                            }
                            Spacer()
                            Text(event.date, style: .time).font(.caption).foregroundStyle(.tertiary)
                        }
                    }
                }
            }
        }
    }

    static func icon(_ kind: StoreModel.Event.Kind) -> String {
        switch kind {
        case .ok: "checkmark.circle.fill"
        case .refused: "hand.raised.fill"
        case .failed: "xmark.octagon.fill"
        case .info: "info.circle"
        }
    }

    static func tint(_ kind: StoreModel.Event.Kind) -> Color {
        switch kind {
        case .ok: .green
        case .refused: .orange
        case .failed: .red
        case .info: .blue
        }
    }
}
