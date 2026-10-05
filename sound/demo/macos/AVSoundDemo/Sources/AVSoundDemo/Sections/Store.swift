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
    /// The chain head recorded for the current book: the head it was created with, then
    /// the head of its log as read after each write (`follow`), but only while the log
    /// still holds the recorded head. Verification can require it, which is how a cut of
    /// a self-consistent log is detected (PROTOCOL.md, "Damaged books"); a write after the
    /// cut does not move it, so the cut stays detected.
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
    /// Set by a tamper action on the current book (Open says so when the user comes
    /// back to it).
    private(set) var isTampered = false
    /// Recorded heads that a log no longer held, already logged (`follow`).
    @ObservationIgnored private var lostAnchors: Set<String> = []
    /// The recorded head and tamper state of the other books of this store, by book ID.
    /// Observed: the profile's book button says "Open" for a book listed here.
    private var otherBooks: [String: BookMemory] = [:]
    var commitAtomID = "K-a1" {
        didSet { if oldValue != commitAtomID { semanticLabel = SemanticLabels.demoLabel(for: commitAtomID) } }
    }
    /// `nil` sends `semantic_label: null` (the store refuses it with E_LABEL).
    var semanticLabel: String? = SemanticLabels.demoLabel(for: "K-a1")
    var verifyAgainstRecordedHead = true
    var recordSelection: RecordRow.ID?
    let activity = Activity()

    var hasBook: Bool { bookID != nil }

    /// What the book button does for a profile: create its `DEMO-` book, go back to it
    /// (the store already has it), or nothing (it is the current book).
    enum BookAction: Equatable {
        case create(String)
        case open(String)
        case current(String)

        var bookID: String {
            switch self {
            case .create(let id), .open(let id), .current(let id): id
            }
        }

        var title: String {
            switch self {
            case .create(let id): "Create \(id)"
            case .open(let id), .current(let id): "Open \(id)"
            }
        }

        var help: String {
            switch self {
            case .create(let id): "Create the book \(id) in the store (store_create)"
            case .open(let id): "Go back to \(id), created earlier in this store: its own recorded head and damage come back"
            case .current(let id): "\(id) is the current book"
            }
        }

        var systemImage: String {
            if case .create = self { return "plus.rectangle.on.folder" }
            return "folder"
        }

        var isCurrent: Bool {
            if case .current = self { return true }
            return false
        }
    }

    /// The book button for `profile`. The store has the books created in it since the
    /// last reset (the bridge's temp store goes with the bridge).
    func bookAction(for profile: Profile) -> BookAction {
        let id = "DEMO-\(profile.rawValue)"
        if id == bookID { return .current(id) }
        return otherBooks[id] != nil ? .open(id) : .create(id)
    }

    /// The book button: creates the current profile's book, or opens it when the store
    /// already has it.
    func createOrOpenBook() {
        guard let app else { return }
        switch bookAction(for: app.profile) {
        case .create: createBook()
        case .open: openBook()
        case .current: break
        }
    }

    /// The bridge restarted: its temp store is gone.
    func bridgeDidStop() {
        guard root != nil || bookID != nil else { return }
        clearStore()
        root = nil
        log(.info, "The bridge stopped; its temp store was removed.")
    }

    func reset() {
        guard let client = app?.client else { return }
        activity.run("reset") { [weak self] in
            let result = try await client.storeReset()
            guard let self, self.app?.isCurrent(client) == true else { return }  // a replaced bridge's store is gone
            self.clearStore()
            self.root = result.root
            self.log(.ok, "Fresh temp store", detail: result.root)
        }
    }

    /// Forgets every book of the store (not the root).
    private func clearStore() {
        otherBooks = [:]
        bookID = nil
        bookProfile = nil
        recordedHead = nil
        isTampered = false
        lostAnchors = []
        listing = nil
        listingError = nil
        records = []
        recordSelection = nil
        recordsError = nil
        verification = nil
        verifiedAgainstHead = nil
    }

    func createBook() {
        guard let app, let client = app.client else { return }
        let profile = app.profile
        let bookID = "DEMO-\(profile.rawValue)"
        activity.run("create") { [weak self] in
            do {
                let result = try await client.storeCreate(bookID: bookID, profile: profile)
                guard let self, self.app?.isCurrent(client) == true else { return }
                self.select(book: result.bookID, profile: profile, createdHead: result.chainHead)
                self.log(.ok, "Created \(result.bookID) (\(profile.rawValue))", detail: "chain head \(Fmt.shortHash(result.chainHead))")
            } catch let error as BridgeError where error.engineType == "BookExists" {
                // The store has the book although this section did not know it (the
                // button said "Create"): go back to it, as "Open" does.
                guard let self, self.app?.isCurrent(client) == true else { return }
                self.select(book: bookID, profile: profile, createdHead: nil)
                self.log(.info, "Opened \(bookID) (\(profile.rawValue)): the store already has it", detail: error.engineMessage)
            } catch let error as BridgeError where error.engineType != nil {
                guard self?.app?.isCurrent(client) == true else { return }
                self?.log(.refused, "create_book: \(error.engineTitle ?? "refused")", detail: error.engineMessage)
            }
            await self?.refresh(client)
        }
    }

    /// Goes back to the current profile's book, created earlier in this store: it gets its
    /// own recorded head and tamper state back (not the book it leaves), then its listing
    /// and records. No store command is needed to switch.
    func openBook() {
        guard let app else { return }
        let profile = app.profile
        guard case .open(let id) = bookAction(for: profile) else { return }
        select(book: id, profile: profile, createdHead: nil)
        let head = recordedHead.map { "its own recorded head \(Fmt.shortHash($0))" } ?? "the head of its log"
        log(.info, "Opened \(id) (\(profile.rawValue))",
            detail: "Back to \(head)" + (isTampered ? "; it was tampered with earlier (Verify checks it)." : "."))
        guard let client = app.client else { return }
        activity.run("open") { [weak self] in await self?.refresh(client) }
    }

    func commitCurrent() {
        guard let app, let client = app.client, let bookID else { return }
        let recipe = app.recipe
        let atomID = commitAtomID
        let label = semanticLabel
        activity.run("commit") { [weak self] in
            await self?.commit(client, bookID: bookID, atomID: atomID, label: label, recipe: recipe)
            await self?.refresh(client)
        }
    }

    /// Commits every atom of the scratch book of the store book's profile, in slot order.
    func commitBook() {
        guard let app, let client = app.client, let bookID, let profile = bookProfile else { return }
        let references = (app.books[profile] ?? ScratchBook()).references
        guard !references.isEmpty else {
            log(.info, "The \(profile.rawValue) scratch book is empty", detail: "Load the DEMO book in Validator & Book first.")
            return
        }
        activity.run("commitBook") { [weak self] in
            for reference in references {
                try Task.checkCancellation()
                await self?.commit(
                    client, bookID: bookID, atomID: reference.refID,
                    label: SemanticLabels.demoLabel(for: reference.refID), recipe: reference.recipe)
            }
            await self?.refresh(client)
        }
    }

    /// Commits a different recipe (same label) to an atom that is already committed: the
    /// store must refuse it (`OverwriteRejected`) and log the attempt.
    func tryOverwrite() {
        guard let client = app?.client, let bookID, let entry = listing?.entries.first else { return }
        var recipe = entry.recipe
        if let pitch = recipe.pitches.first { recipe.pitches[0] = pitch < 6 ? pitch + 1 : pitch - 1 }
        let label = records.last { $0.event == "commit" && $0.atomID == entry.atomID }?
            .raw["semantic_label"]?.stringValue
        activity.run("overwrite") { [weak self] in
            await self?.commit(
                client, bookID: bookID, atomID: entry.atomID, label: label, recipe: recipe,
                note: "overwrite attempt on \(entry.atomID)")
            await self?.refresh(client)
        }
    }

    /// Freezes the book. Its new head becomes the recorded head through `refresh()`, as
    /// for a commit: only while the log still holds the recorded head (`follow`).
    func freeze() {
        guard let client = app?.client, let bookID else { return }
        activity.run("freeze") { [weak self] in
            do {
                let result = try await client.storeFreeze(bookID: bookID)
                guard self?.app?.isCurrent(client) == true else { return }
                self?.log(.ok, "Froze \(bookID)", detail: "chain head \(Fmt.shortHash(result.chainHead)); further commits are refused")
            } catch let error as BridgeError where error.engineType != nil {
                guard self?.app?.isCurrent(client) == true else { return }
                self?.log(.refused, "freeze: \(error.engineTitle ?? "refused")", detail: error.engineMessage)
            }
            await self?.refresh(client)
        }
    }

    func verify() {
        guard let client = app?.client else { return }
        activity.run("verify") { [weak self] in
            await self?.runVerify(client)
        }
    }

    /// Damages the temp store, then verifies to show the detection. A repeated tamper
    /// never repairs earlier damage: the bridge refuses a blob flip when every committed
    /// blob of the book is already damaged (PROTOCOL.md, "Damaged books"). The refusal is
    /// logged, and the verification still shows the damage.
    func tamper(_ kind: StoreTamperKind) {
        guard let client = app?.client, let bookID else { return }
        activity.run("tamper") { [weak self] in
            do {
                let result = try await client.storeTamper(bookID: bookID, kind: kind)
                guard let self, self.app?.isCurrent(client) == true else { return }
                if self.bookID == bookID {
                    self.isTampered = true
                } else {
                    self.otherBooks[bookID]?.isTampered = true
                }
                self.log(.info, "Tampered \(bookID): \(Self.title(kind))", detail: result.done)
            } catch let error as BridgeError where error.engineType != nil {
                guard self?.app?.isCurrent(client) == true else { return }
                self?.log(.refused, "\(Self.title(kind)) on \(bookID): \(error.engineTitle ?? "refused")",
                          detail: error.engineMessage)
            }
            await self?.runVerify(client)
            await self?.refresh(client)
        }
    }

    func refreshNow() {
        guard let client = app?.client else { return }
        activity.run("refresh") { [weak self] in await self?.refresh(client) }
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

    /// Whether `book` is still the current book and `client` still the app's client: a
    /// reply about another book, or from a replaced bridge, changes nothing shown.
    private func isShowing(_ book: String, from client: BridgeClient) -> Bool {
        book == bookID && app?.isCurrent(client) == true
    }

    /// The log of the current book `book` ends with the line hash `head` (`records` are
    /// its lines). The recorded head moves to it (a write, or a refused commit, which the
    /// store logs too) only while the log still holds the recorded head: a log that no
    /// longer does was cut or rewritten since, and the recorded head stays, so that
    /// verification against it still reports the cut (`E_ANCHOR`), also after later
    /// writes. A line's hash is the next record's `prev_sha256`, and the last line's is
    /// the chain head.
    private func follow(head: String, of book: String, records: [RecordRow]) {
        guard let recorded = recordedHead, recorded != head else {
            recordedHead = head
            return
        }
        if records.contains(where: { $0.prevSHA256 == recorded }) {
            recordedHead = head
        } else if lostAnchors.insert(recorded).inserted {
            log(.info, "\(book): the log no longer holds the recorded head",
                detail: "Recorded head \(Fmt.shortHash(recorded)), log head \(Fmt.shortHash(head)): lines were cut or rewritten. The recorded head stays (later writes do not move it), so Verify with \u{201C}Require the recorded chain head\u{201D} reports the cut (E_ANCHOR).")
        }
    }

    private func commit(
        _ client: BridgeClient, bookID: String, atomID: String, label: String?, recipe: Recipe, note: String? = nil
    ) async {
        let what = note ?? "commit \(atomID)"
        do {
            let result = try await client.storeCommit(bookID: bookID, atomID: atomID, semanticLabel: label, recipe: recipe)
            guard app?.isCurrent(client) == true else { return }
            let detail = "commit_index \(result.entry.commitIndex) \u{00B7} pcm \(Fmt.shortHash(result.entry.pcmSHA256)) \u{00B7} head \(Fmt.shortHash(result.chainHead))"
            if note != nil {
                log(.failed, "\(what): the store accepted it (\(result.outcome))", detail: detail)
            } else {
                log(.ok, result.isNoop ? "\(atomID): recommit (no-op)" : "\(atomID): committed", detail: detail)
            }
        } catch let error as BridgeError where error.engineType != nil {
            guard app?.isCurrent(client) == true else { return }
            var detail = error.engineMessage
            if let validation = error.validationDetails, !validation.codes.isEmpty {
                detail += " [\(validation.codes.joined(separator: ", "))]"
            }
            log(.refused, "\(what): \(error.engineTitle ?? "refused")", detail: detail)
        } catch {
            guard app?.isCurrent(client) == true else { return }
            log(.failed, "\(what) failed", detail: userMessage(error))
        }
    }

    private func runVerify(_ client: BridgeClient) async {
        guard let bookID else { return }
        let expected = verifyAgainstRecordedHead ? recordedHead : nil
        do {
            let result = try await client.storeVerify(bookID: bookID, expectedHead: expected)
            guard isShowing(bookID, from: client) else { return }  // another book, or a replaced bridge
            verification = result
            verifiedAgainstHead = expected
            if result.ok {
                log(.ok, "Verified \(bookID): intact", detail: expected.map { "against head \(Fmt.shortHash($0))" })
            } else {
                log(.failed, "Verified \(bookID): \(result.issues.count) issue(s) detected",
                    detail: result.issues.map(\.code).joined(separator: ", "))
            }
        } catch {
            guard app?.isCurrent(client) == true else { return }
            log(.failed, "store_verify failed", detail: userMessage(error))
        }
    }

    /// Reads the current book's listing and records, and lets the recorded head follow the
    /// log (`follow`).
    private func refresh(_ client: BridgeClient) async {
        guard let bookID else { return }
        var listedHead: String?
        do {
            let listing = try await client.storeList(bookID: bookID)
            guard isShowing(bookID, from: client) else { return }  // another book, or a replaced bridge
            self.listing = listing
            listingError = nil
            listedHead = listing.chainHead
        } catch {
            guard isShowing(bookID, from: client) else { return }
            listing = nil
            listingError = userMessage(error)
        }
        do {
            let raw = try await client.storeRecords(bookID: bookID)
            guard isShowing(bookID, from: client) else { return }
            let rows = raw.enumerated().map { RecordRow(index: $0.offset, record: $0.element) }
            records = rows
            recordsError = nil
            if let listedHead { follow(head: listedHead, of: bookID, records: rows) }
        } catch {
            guard isShowing(bookID, from: client) else { return }
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
                    let bookAction = store.bookAction(for: app.profile)
                    ActionButton(bookAction.title, systemImage: bookAction.systemImage,
                                 isRunning: activity.isRunning("create") || activity.isRunning("open"),
                                 prominent: !store.hasBook) { store.createOrOpenBook() }
                        .disabled(bookAction.isCurrent)
                        .help(bookAction.help)
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
                        .help("Pass expected_head = the recorded head: the head of this book's log after its last write, as long as the log still held the head recorded before (a write after a cut does not move it). Needed to detect a log cut that leaves a self-consistent log: a cut of a commit, or of a refused commit logged after the freeze (E_ANCHOR), also after later writes. A cut of the freeze record (E_MARKER) or of the only record, create_book (E_EVENT), is found without it.")
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
