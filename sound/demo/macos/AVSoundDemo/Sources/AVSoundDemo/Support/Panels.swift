import AppKit
import UniformTypeIdentifiers

/// Open and save panels, and the pasteboard.
@MainActor
enum Panels {
    /// Asks for the repository folder (the one that contains `sound/pyproject.toml`).
    static func chooseRepository(startingAt start: URL?) -> URL? {
        let panel = NSOpenPanel()
        panel.title = "Choose the repository folder"
        panel.message = "Choose the acoustic-vocabularies checkout: the folder that contains sound/pyproject.toml."
        panel.prompt = "Choose"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = false
        panel.directoryURL = start
        return panel.runModal() == .OK ? panel.url : nil
    }

    /// Asks for the uv executable (hidden folders such as `~/.local/bin` are shown).
    static func chooseUV(startingAt start: URL?) -> URL? {
        let panel = NSOpenPanel()
        panel.title = "Choose the uv executable"
        panel.message = "Choose the uv binary, for example /opt/homebrew/bin/uv or ~/.local/bin/uv."
        panel.prompt = "Choose"
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        panel.showsHiddenFiles = true
        panel.treatsFilePackagesAsDirectories = true
        panel.resolvesAliases = true
        panel.directoryURL = start
        return panel.runModal() == .OK ? panel.url : nil
    }

    /// Asks where to save a WAV file.
    static func saveWAV(suggestedName: String) -> URL? {
        let panel = NSSavePanel()
        panel.title = "Export WAV"
        panel.message = "The canonical WAV bytes whose file and waveform hashes were verified."
        panel.allowedContentTypes = [.wav]
        panel.nameFieldStringValue = suggestedName
        panel.canCreateDirectories = true
        panel.isExtensionHidden = false
        return panel.runModal() == .OK ? panel.url : nil
    }

    static func copy(_ text: String) {
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(text, forType: .string)
    }

    static func reveal(_ url: URL) {
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }
}

/// Whether `path` is an executable regular file.
func isExecutableFile(_ path: String) -> Bool {
    var isDirectory: ObjCBool = false
    return FileManager.default.fileExists(atPath: path, isDirectory: &isDirectory) && !isDirectory.boolValue
        && FileManager.default.isExecutableFile(atPath: path)
}
