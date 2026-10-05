import AVSoundDemoCore
import Observation
import SwiftUI

// MARK: - Model

@MainActor
@Observable
final class PackageModel {
    /// The counts a DEMO package must have.
    static let expectedCounts = (atomWavs: 16, messageWavs: 18, heldoutIDs: 14)

    @ObservationIgnored weak var app: AppModel?

    private(set) var package: PackageDemo?
    /// When `package` was built. Every build is a new one: the bridge builds, seals, loads
    /// and scans a fresh package in a new directory and removes the previous one.
    private(set) var builtAt: Date?
    var fileSelection: PackageFile.ID?
    /// Why no package is shown although one was built (the bridge stopped).
    private(set) var notice: String?
    let activity = Activity()

    func build() {
        guard let client = app?.client else { return }
        activity.run("build") { [weak self] in
            let package = try await client.packageDemo()
            self?.package = package
            self?.builtAt = Date()
            self?.notice = nil
        }
    }

    /// The bridge stopped (or was replaced): its temp directory, and the package in it,
    /// is gone, so the package is no longer shown as current.
    func bridgeDidStop() {
        guard package != nil else { return }
        package = nil
        builtAt = nil
        fileSelection = nil
        notice = "The bridge stopped, and its temp directory with the package was removed. Build again to make a new package."
    }
}

// MARK: - View

struct PackagesView: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        let model = app.packages
        SectionPage(.packages, summary: "Build, seal, load and leak-scan the synthetic DEMO stimulus package in a temp directory: 16 atom WAVs, 18 trained message WAVs, and only the hashes of the 14 held-out messages.") {
            Card("DEMO package", systemImage: "shippingbox") {
                ActionButton(model.package == nil ? "Build DEMO Package" : "Build Again", systemImage: "hammer",
                             isRunning: model.activity.isRunning("build"), prominent: model.package == nil) {
                    model.build()
                }
                .requiresBridge()
            } content: {
                if let error = model.activity.error {
                    ErrorBanner(message: error) { model.activity.error = nil }
                }
                if let package = model.package {
                    PackageSummary(package: package, builtAt: model.builtAt)
                } else if model.activity.isRunning("build") {
                    ProgressView("Building the package\u{2026}")
                } else {
                    Text(model.notice ?? "Not built yet.").foregroundStyle(.secondary)
                }
            }
            if let package = model.package {
                PackageFilesCard(package: package)
                Card("Leak report", systemImage: "drop.triangle",
                     subtitle: "scan_package: no held-out audio, no method strings in the package.") {
                    if let ok = package.leakReportOK {
                        PassFail(ok: ok, text: ok ? "clean" : "findings")
                    }
                } content: {
                    CodeBlock(text: package.leakReport.prettyString, maxHeight: 220)
                }
                Card("Answers preview", systemImage: "list.bullet.clipboard",
                     subtitle: "The first \(package.answersPreview.count) entries of answers.json (synthetic labels).") {
                    CodeBlock(text: JSONValue.array(package.answersPreview).prettyString, maxHeight: 300)
                }
            }
        }
    }
}

private struct PackageSummary: View {
    let package: PackageDemo
    let builtAt: Date?

    var body: some View {
        let expected = PackageModel.expectedCounts
        Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 8) {
            if let builtAt {
                InfoRow("Built") {
                    Text("\(builtAt.formatted(date: .omitted, time: .standard)), a fresh build (Build Again builds, seals, loads and scans a new package)")
                        .foregroundStyle(.secondary)
                }
            }
            InfoRow("package_sha256") { HashText(hash: package.packageSHA256, length: 32) }
            InfoRow("Atom WAVs") { countRow(package.counts.atomWavs, expected.atomWavs) }
            InfoRow("Message WAVs") { countRow(package.counts.messageWavs, expected.messageWavs) }
            InfoRow("Held-out IDs") { countRow(package.counts.heldoutIDs, expected.heldoutIDs) }
            InfoRow("loader_ok") { PassFail(ok: package.loaderOK, text: package.loaderOK ? "load_package accepted it" : "load_package refused it") }
            InfoRow("Leak report") {
                if let ok = package.leakReportOK {
                    PassFail(ok: ok, text: ok ? "ok" : "findings (see below)")
                } else {
                    Text("no ok field").foregroundStyle(.secondary)
                }
            }
            InfoRow("Files", "\(package.files.count) \u{00B7} \(Fmt.bytes(package.files.reduce(0) { $0 + $1.bytes }))")
            InfoRow("Directory") {
                HStack(spacing: 6) {
                    Text(package.dir)
                        .font(.callout.monospaced())
                        .textSelection(.enabled)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Button {
                        Panels.reveal(URL(fileURLWithPath: package.dir))
                    } label: {
                        Image(systemName: "folder")
                    }
                    .buttonStyle(.borderless)
                    .help("Show in Finder (a temp directory, removed by the next build or when the bridge exits)")
                    .accessibilityLabel("Show in Finder")
                }
            }
        }
    }

    private func countRow(_ actual: Int, _ expected: Int) -> some View {
        PassFail(ok: actual == expected, text: actual == expected ? "\(actual)" : "\(actual) (expected \(expected))")
    }
}

private struct PackageFilesCard: View {
    @Environment(AppModel.self) private var app
    let package: PackageDemo

    var body: some View {
        @Bindable var model = app.packages
        Card("Files (\(package.files.count))", systemImage: "doc.on.doc",
             subtitle: "From manifest.json: every file with its SHA-256 and size.") {
            Table(package.files, selection: $model.fileSelection) {
                TableColumn("Path") { file in Text(file.path).font(.callout.monospaced()) }
                    .width(min: 200, ideal: 300)
                TableColumn("sha256") { file in
                    HStack(spacing: 4) {
                        Text(Fmt.shortHash(file.sha256, length: 20)).font(.callout.monospaced()).help(file.sha256)
                        CopyButton(text: file.sha256)
                    }
                }
                .width(min: 180, ideal: 220)
                TableColumn("Size") { file in
                    Text(Fmt.bytes(file.bytes)).monospacedDigit()
                }
                .width(min: 60, ideal: 80)
            }
            .frame(height: 300)
        }
    }
}
