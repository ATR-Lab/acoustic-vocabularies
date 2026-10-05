import AVSoundDemoCore
import Observation
import SwiftUI

// MARK: - Model

@MainActor
@Observable
final class FallbackModel {
    @ObservationIgnored weak var app: AppModel?

    private(set) var demo: FallbackDemo?
    private(set) var demoProfile: Profile?
    private(set) var scan: ScanResult?
    private(set) var scanProfile: Profile?
    private(set) var scanBookCount = 0
    /// Bank indices already used (comma separated), passed as `used`.
    var usedText = ""
    var bankSelection: FallbackBankEntry.ID?
    var bookSelection: FallbackBookAtom.ID?
    /// The scratch-book slot that "Put in Scratch Book" fills with the selected bank recipe.
    var targetSlot = "K-a1"
    /// The waveform hashes (`pcm_sha256`) of bank and book rows whose bridge render
    /// matched them. Keyed by what was checked, not by the row: a render that returns after
    /// another profile's set was loaded marks no row of that set.
    private(set) var verifiedPCM: Set<String> = []
    let activity = Activity()

    var isStale: Bool { demo != nil && demoProfile != app?.profile }

    /// The last scan, only while it belongs to the current profile and the shown bank (a
    /// scan of another profile's bank must not mark rows of this one).
    var currentScan: ScanResult? {
        guard let scan, let scanProfile, scanProfile == app?.profile, scanProfile == demoProfile else { return nil }
        return scan
    }

    func load() {
        guard let app, let client = app.client else { return }
        let profile = app.profile
        let task = activity.run("load") { [weak self] in
            let demo = try await client.fallbackDemo(profile: profile)
            guard let self, self.app?.isCurrent(client) == true else { return }  // a replaced bridge's set
            if self.demoProfile != profile { self.clearScan() }
            self.demo = demo
            self.demoProfile = profile
            self.verifiedPCM = []
            self.bankSelection = nil
            self.bookSelection = nil
        }
        // A profile change while this load runs is not lost (`loadIfNeeded` skips it while
        // a load runs): once this load has ended, also when it failed, the set of the
        // current profile is loaded if it is not the one shown. A failed load of the
        // current profile is not retried (Reload does that).
        Task { [weak self] in
            await task.value
            guard let self, let app = self.app, app.profile != profile else { return }
            self.loadIfNeeded()
        }
    }

    private func clearScan() {
        scan = nil
        scanProfile = nil
        scanBookCount = 0
    }

    /// The selected bank entry, when one is selected and the bank is the current profile's.
    var selectedBankEntry: FallbackBankEntry? {
        guard !isStale, let index = bankSelection else { return nil }
        return demo?.bank.first { $0.index == index }
    }

    /// Puts the selected bank recipe into `targetSlot` of the bank profile's scratch book.
    ///
    /// A scan walks the bank in order and stops at the first admissible entry, so it can
    /// reject only an entry it reaches. Put the entry the last scan selected (a scan also
    /// selects it in the table) and the next scan rejects it (E_DUPLICATE, E_SEPARATION)
    /// and selects a later one. A row after the selected one is never reached: putting it
    /// in the book changes nothing the scan shows (`putHint` says so). The DEMO books are
    /// too far from every bank recipe to cause a rejection by themselves.
    func putSelectedInScratchBook() {
        guard let app, let entry = selectedBankEntry, let profile = demoProfile else { return }
        var book = app.books[profile] ?? ScratchBook()
        book.set(targetSlot, entry.recipe)
        app.books[profile] = book
    }

    /// Why putting the selected bank row into the scratch book would show no rejection in
    /// the next scan, when it would not (`nil`: it would, or no row is selected).
    var putHint: String? {
        guard let entry = selectedBankEntry else { return nil }
        if (try? Self.parseUsed(usedText))?.contains(entry.index) == true {
            return "#\(entry.index) is in the used indices: the scan skips it, so it cannot be rejected."
        }
        guard let scan = currentScan else {
            return "Scan first: a scan stops at its first admissible entry (starred), and only an entry it reaches can be rejected."
        }
        if let selected = scan.selectedIndex, entry.index > selected {
            return "The last scan stopped at #\(selected), before #\(entry.index): put #\(selected) (starred) in the book to see a rejection."
        }
        return nil
    }

    func loadIfNeeded() {
        guard let app, app.isReady, !activity.isRunning("load") else { return }
        if demo == nil || demoProfile != app.profile { load() }
    }

    func play(bank entry: FallbackBankEntry) {
        bankSelection = entry.index
        play(recipe: entry.recipe, expectedPCM: entry.pcmSHA256)
    }

    func play(atom: FallbackBookAtom) {
        bookSelection = atom.atomID
        play(recipe: atom.recipe, expectedPCM: atom.pcmSHA256)
    }

    /// Whether the row with the waveform hash `pcm` was rendered and matched it.
    func isVerified(_ pcm: String) -> Bool { verifiedPCM.contains(pcm) }

    /// The activity key of the render of the row with the waveform hash `pcm` (its
    /// spinner), keyed like `verifiedPCM`.
    static func playKey(_ pcm: String) -> String { "play-\(pcm)" }

    /// Copies the fallback book into the scratch book of its profile. The scratch book is
    /// labeled as this book until its first edit; it is no synthetic DEMO book, so no
    /// book ID is sent with it (`origin` stays `nil`).
    func useBookAsScratch() {
        guard let app, let demo, let profile = demoProfile else { return }
        app.books[profile] = .fallback(demo, profile: profile)
    }

    /// Parses the `used` field: bank indices separated by commas or white space. Every
    /// token must be an integer; any other token is an error (it is never dropped, which
    /// would scan with another `used` list than the one typed). The bridge checks the range.
    static func parseUsed(_ text: String) throws -> [Int] {
        let tokens = text.split(whereSeparator: { $0 == "," || $0.isWhitespace })
        let invalid = tokens.filter { Int($0) == nil }
        guard invalid.isEmpty else {
            let list = invalid.map { "\u{201C}\($0)\u{201D}" }.joined(separator: ", ")
            throw AppError("Used indices must be integers separated by commas or spaces (such as 0, 3), not \(list).")
        }
        return tokens.compactMap { Int($0) }
    }

    /// Why the `used` field cannot be sent (shown under it; Scan is disabled meanwhile).
    var usedError: String? {
        do {
            _ = try Self.parseUsed(usedText)
            return nil
        } catch {
            return userMessage(error)
        }
    }

    /// Why Scan cannot run now (its help, and a note in the card; Scan is disabled
    /// meanwhile): the `used` field does not parse, or the bank shown is another profile's.
    /// A scan runs for the current profile, and a scan of a bank that is not shown would
    /// never be shown either (`currentScan`).
    var scanBlocker: String? {
        if let usedError { return usedError }
        guard isStale, let shown = demoProfile, let profile = app?.profile else { return nil }
        if activity.isRunning("load") {
            return "The \(profile.rawValue) set is loading; Scan is available once it is shown."
        }
        return "The bank shown is the \(shown.rawValue) set, not the \(profile.rawValue) set: Reload loads the \(profile.rawValue) set, then Scan scans it."
    }

    /// Scans the bank against the current scratch book (`fallback_scan`). Not while the
    /// bank shown is another profile's (`scanBlocker`): the load's error stays shown.
    func scanCurrentBook() {
        guard let app, !isStale else { return }
        let used: [Int]
        do {
            used = try Self.parseUsed(usedText)
        } catch {
            activity.error = userMessage(error)
            return
        }
        guard let client = app.client else { return }
        let profile = app.profile
        let book = app.book.references
        activity.run("scan") { [weak self] in
            let result = try await client.fallbackScan(profile: profile, book: book, used: used.isEmpty ? nil : used)
            guard self?.app?.isCurrent(client) == true else { return }
            self?.scan = result
            self?.scanProfile = profile
            self?.scanBookCount = book.count
            if let index = result.selectedIndex { self?.bankSelection = index }
        }
    }

    private func play(recipe: Recipe, expectedPCM: String) {
        guard let app, let profile = demoProfile else { return }
        activity.run(Self.playKey(expectedPCM)) { [weak self] in
            try await app.renderAndPlay(recipe, profile: profile, expectedPCM: expectedPCM)
            self?.verifiedPCM.insert(expectedPCM)
        }
    }
}

// MARK: - View

struct FallbackView: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        let model = app.fallback
        SectionPage(.fallback, summary: "The public DEMO fallback set: a 64-recipe bank and a 16-atom fallback book per profile. A scan walks the bank in order and selects the first recipe that is admissible against the book.") {
            FallbackHeaderCard()
            if model.demo != nil {
                ScanCard()
                BankCard()
                FallbackBookCard()
            }
        }
        .onAppear { model.loadIfNeeded() }
        .onChange(of: app.isReady) { model.loadIfNeeded() }
        .onChange(of: app.profile) { model.loadIfNeeded() }
    }
}

private struct FallbackHeaderCard: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        let model = app.fallback
        Card("DEMO fallback set", systemImage: "arrow.triangle.branch",
             subtitle: "Profile \(app.profile.rawValue). Built by the engine from the public demo seed, never from a study seed.") {
            ActionButton("Reload", systemImage: "arrow.clockwise", isRunning: model.activity.isRunning("load")) {
                model.load()
            }
            .requiresBridge()
        } content: {
            if let error = model.activity.error {
                ErrorBanner(message: error) { model.activity.error = nil }
            }
            if let demo = model.demo {
                Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
                    InfoRow("Seed label", demo.seedLabel)
                    InfoRow("Profile", model.demoProfile?.rawValue ?? "?")
                    InfoRow("fallback_bank_hash") { HashText(hash: demo.fallbackBankHash, length: 24) }
                    InfoRow("Sizes", "bank \(demo.bank.count) recipes \u{00B7} book \(demo.book.count) atoms")
                }
                if model.isStale {
                    if model.activity.isRunning("load") {
                        Label("Showing \(model.demoProfile?.rawValue ?? "?"); loading \(app.profile.rawValue)\u{2026}",
                              systemImage: "clock")
                            .foregroundStyle(.secondary)
                    } else {
                        Label("Showing \(model.demoProfile?.rawValue ?? "?"): the \(app.profile.rawValue) set is not loaded. Reload loads it.",
                              systemImage: "exclamationmark.circle")
                            .foregroundStyle(.orange)
                    }
                }
            } else if model.activity.isRunning("load") {
                ProgressView("Building the fallback set\u{2026}")
            } else {
                Text(app.isReady ? "Not loaded." : "Waiting for the bridge.").foregroundStyle(.secondary)
            }
        }
    }
}

private struct ScanCard: View {
    @Environment(AppModel.self) private var app

    /// How to see a rejection: the DEMO books (synthetic and fallback) are farther than the
    /// threshold from every bank recipe (sound/docs/fallback.md), so they never cause one.
    /// The scan stops at its first admissible entry, so only the entry it selected (or an
    /// earlier one) can be rejected by the next scan.
    static let rejectionHint = "The DEMO books are farther than the threshold from every bank recipe, so they cause no rejection. To see one, scan, put the entry the scan selected (the starred row, which the scan also selects in the Bank table) in a scratch-book slot with Put in Scratch Book, and scan again: the scan rejects that entry (E_DUPLICATE, E_SEPARATION) and selects a later one. A row after the selected one is never reached, so it cannot be rejected."

    var body: some View {
        @Bindable var model = app.fallback
        Card("Scan against the scratch book", systemImage: "magnifyingglass",
             subtitle: "fallback_scan(\(app.profile.rawValue), book = the \(app.book.count) atoms of the \(app.profile.rawValue) scratch book)") {
            HStack {
                TextField("used indices, e.g. 0, 3", text: $model.usedText)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 170)
                    .help("Bank indices already used (skipped by the scan)")
                ActionButton("Scan", systemImage: "play.circle", isRunning: model.activity.isRunning("scan"), prominent: true) {
                    model.scanCurrentBook()
                }
                .disabled(model.scanBlocker != nil)
                .help(model.scanBlocker ?? "Scan the bank against the scratch book")
            }
            .requiresBridge()
        } content: {
            if let usedError = model.usedError {
                Label(usedError, systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.red)
            } else if let blocker = model.scanBlocker {
                Label(blocker, systemImage: "exclamationmark.circle")
                    .foregroundStyle(.orange)
            }
            if let scan = model.currentScan {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 10) {
                        if scan.isExhausted {
                            CodeChip(code: "EXHAUSTED", tint: .orange)
                            Text("No bank recipe passed: the whole-book fallback applies.")
                        } else if let index = scan.selectedIndex {
                            CodeChip(code: "SELECTED", tint: .green)
                            Text("Bank index \(index)").font(.headline.monospacedDigit())
                            if let source = scan.selectedSource { Text(source).font(.callout.monospaced()).foregroundStyle(.secondary) }
                        }
                        Spacer()
                        Text("\(scan.log.count) step(s) \u{00B7} \(model.scanBookCount) references \u{00B7} \(model.scanProfile?.rawValue ?? "")")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
                        InfoRow("Selected recipe") { HashText(hash: scan.selectedRecipeSHA256) }
                        InfoRow("Selected waveform") { HashText(hash: scan.selectedPcmSHA256) }
                        InfoRow("Used", scan.used.isEmpty ? "none" : scan.used.map(String.init).joined(separator: ", "))
                        InfoRow("References", scan.referenceIDs.isEmpty ? "none" : scan.referenceIDs.joined(separator: " "))
                    }
                    Text("Scan log").font(.subheadline.weight(.semibold))
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(Array(scan.log.enumerated()), id: \.offset) { _, step in
                            HStack(alignment: .firstTextBaseline, spacing: 8) {
                                Text("#\(step.index)").font(.body.monospacedDigit()).frame(width: 40, alignment: .trailing)
                                Text(step.outcome)
                                    .foregroundStyle(step.outcome == "selected" ? Color.green : (step.outcome == "used" ? Color.secondary : Color.orange))
                                    .frame(width: 70, alignment: .leading)
                                ForEach(step.codes, id: \.self) { CodeChip(code: $0, tint: .orange) }
                                Text(step.messages.joined(separator: "; "))
                                    .font(.callout).foregroundStyle(.secondary).lineLimit(2)
                                    .help(step.messages.joined(separator: "\n"))
                            }
                        }
                    }
                    DisclosureGroup("Raw scan record") {
                        CodeBlock(text: scan.raw.prettyString, maxHeight: 260)
                    }
                    if !scan.log.contains(where: { $0.outcome == "rejected" }) {
                        Text(Self.rejectionHint).font(.callout).foregroundStyle(.secondary)
                    }
                }
            } else {
                Text((app.book.isEmpty
                      ? "The scratch book is empty: the first bank recipe that passes against the reserved assets is selected. "
                      : "Run a scan to see which bank recipe would replace a failed atom. ")
                     + Self.rejectionHint)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

private struct BankCard: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        @Bindable var model = app.fallback
        let selectedByScan = model.currentScan?.selectedIndex
        Card("Bank (\(model.demo?.bank.count ?? 0))", systemImage: "tray.full",
             subtitle: "Play renders the recipe on the bridge and checks it against the listed pcm_sha256. To make the scan reject an entry, put the row the last scan selected (starred) in a scratch-book slot and scan again; the scan stops at that row, so a later row is never reached.") {
            HStack(spacing: 6) {
                Picker("Slot", selection: $model.targetSlot) {
                    ForEach(AtomSlots.all, id: \.self) { Text($0).tag($0) }
                }
                .labelsHidden()
                .frame(width: 90)
                Button("Put in Scratch Book") { model.putSelectedInScratchBook() }
                    .disabled(model.selectedBankEntry == nil)
                    .help(model.selectedBankEntry.map {
                        "Put bank recipe #\($0.index) into \(model.targetSlot) of the \(model.demoProfile?.rawValue ?? "") scratch book"
                    } ?? "Select a bank row first")
            }
        } content: {
            if let hint = model.putHint {
                Label(hint, systemImage: "info.circle")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Table(model.demo?.bank ?? [], selection: $model.bankSelection) {
                TableColumn("") { entry in
                    RowPlayButton(pcm: entry.pcmSHA256, name: "bank recipe \(entry.index)") { model.play(bank: entry) }
                }
                .width(28)
                TableColumn("#") { entry in
                    HStack(spacing: 4) {
                        Text("\(entry.index)").monospacedDigit()
                        if entry.index == selectedByScan {
                            Image(systemName: "star.fill").foregroundStyle(.green).help("Selected by the last scan")
                        }
                    }
                }
                .width(44)
                TableColumn("Recipe") { entry in
                    Text(entry.recipe.summary).font(.callout.monospacedDigit()).help(entry.recipe.canonicalJSON)
                }
                .width(min: 220, ideal: 300)
                TableColumn("pcm_sha256") { entry in
                    HStack(spacing: 4) {
                        Text(Fmt.shortHash(entry.pcmSHA256)).font(.body.monospaced()).help(entry.pcmSHA256)
                        if model.isVerified(entry.pcmSHA256) {
                            Image(systemName: "checkmark.seal.fill").foregroundStyle(.green).help("Rendered audio matched")
                        }
                    }
                }
                .width(min: 130, ideal: 150)
            }
            .frame(height: 320)
            .requiresBridge()
        }
    }
}

private struct FallbackBookCard: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        @Bindable var model = app.fallback
        Card("Fallback book (\(model.demo?.book.count ?? 0))", systemImage: "books.vertical",
             subtitle: "The DEMO fallback book of \(model.demoProfile?.rawValue ?? "?").") {
            Button("Use as Scratch Book") { model.useBookAsScratch() }
                .help("Copy these 16 atoms into the \(model.demoProfile?.rawValue ?? "") scratch book")
                .disabled(model.demo == nil)
        } content: {
            Table(model.demo?.book ?? [], selection: $model.bookSelection) {
                TableColumn("") { atom in
                    RowPlayButton(pcm: atom.pcmSHA256, name: "fallback atom \(atom.atomID)") { model.play(atom: atom) }
                }
                .width(28)
                TableColumn("Atom") { atom in Text(atom.atomID).font(.body.monospaced()) }.width(54)
                TableColumn("Recipe") { atom in
                    Text(atom.recipe.summary).font(.callout.monospacedDigit()).help(atom.recipe.canonicalJSON)
                }
                .width(min: 220, ideal: 300)
                TableColumn("pcm_sha256") { atom in
                    HStack(spacing: 4) {
                        Text(Fmt.shortHash(atom.pcmSHA256)).font(.body.monospaced()).help(atom.pcmSHA256)
                        if model.isVerified(atom.pcmSHA256) {
                            Image(systemName: "checkmark.seal.fill").foregroundStyle(.green).help("Rendered audio matched")
                        }
                    }
                }
                .width(min: 130, ideal: 150)
            }
            .frame(height: 300)
            .requiresBridge()
        }
    }
}

private struct RowPlayButton: View {
    @Environment(AppModel.self) private var app
    /// The row's waveform hash: its render's spinner is keyed by it (`playKey`).
    let pcm: String
    /// What the row plays, for VoiceOver ("bank recipe 3").
    let name: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            if app.fallback.activity.isRunning(FallbackModel.playKey(pcm)) {
                ProgressView().controlSize(.mini)
            } else {
                Image(systemName: "play.fill")
            }
        }
        .buttonStyle(.borderless)
        .help("Render and play \(name)")
        .accessibilityLabel("Play \(name)")
    }
}
