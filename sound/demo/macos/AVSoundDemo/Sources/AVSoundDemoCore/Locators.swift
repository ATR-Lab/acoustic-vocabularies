import Foundation

/// Finds the `uv` executable.
///
/// Order: an explicit path, every `PATH` directory (like `which uv`), then
/// `/opt/homebrew/bin/uv`, `/usr/local/bin/uv`, `~/.local/bin/uv`, `~/.cargo/bin/uv`.
public enum UVLocator {
    /// Every place searched, in order, without duplicates.
    public static func candidates(
        explicit: String? = nil,
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) -> [URL] {
        let home = environment["HOME"] ?? NSHomeDirectory()
        var paths: [String] = []
        if let explicit, !explicit.trimmingCharacters(in: .whitespaces).isEmpty {
            paths.append(expandTilde(explicit.trimmingCharacters(in: .whitespaces), home: home))
        }
        for dir in (environment["PATH"] ?? "").split(separator: ":") where !dir.isEmpty {
            paths.append(URL(fileURLWithPath: String(dir)).appendingPathComponent("uv").path)
        }
        paths += ["/opt/homebrew/bin/uv", "/usr/local/bin/uv", "\(home)/.local/bin/uv", "\(home)/.cargo/bin/uv"]
        var seen = Set<String>()
        return paths.filter { seen.insert($0).inserted }.map { URL(fileURLWithPath: $0) }
    }

    /// The first executable candidate, or `nil`.
    public static func locate(
        explicit: String? = nil,
        environment: [String: String] = ProcessInfo.processInfo.environment,
        isExecutable: (String) -> Bool = { path in
            var isDirectory: ObjCBool = false
            return FileManager.default.fileExists(atPath: path, isDirectory: &isDirectory)
                && !isDirectory.boolValue && FileManager.default.isExecutableFile(atPath: path)
        }
    ) -> URL? {
        candidates(explicit: explicit, environment: environment).first { isExecutable($0.path) }
    }

    static func expandTilde(_ path: String, home: String) -> String {
        if path == "~" { return home }
        if path.hasPrefix("~/") { return home + path.dropFirst(1) }
        return path
    }
}

/// Finds the repository checkout that holds the engine and the bridge.
///
/// A valid repository contains `sound/pyproject.toml` and
/// `sound/demo/macos/bridge/av_sound_bridge.py`.
public enum RepoLocator {
    /// Environment variable with a repository path.
    public static let environmentKey = "AV_SOUND_REPO"
    /// `UserDefaults` key of the repository path the user chose.
    public static let defaultsKey = "AVSoundDemo.repoPath"

    public static let requiredFiles = ["sound/pyproject.toml", ProcessBridgeTransport.Configuration.bridgeScriptRelativePath]

    public enum Source: String, Sendable, Hashable {
        case explicit
        case environment
        case executable
        case currentDirectory
        case persisted
    }

    public struct Candidate: Sendable, Hashable {
        public let url: URL
        public let source: Source
        /// Whether `url` is searched upwards (its ancestors too).
        public let walksUp: Bool
    }

    public struct Located: Sendable, Hashable {
        public let url: URL
        public let source: Source
    }

    public static func isValidRepo(_ url: URL, fileManager: FileManager = .default) -> Bool {
        requiredFiles.allSatisfy { fileManager.fileExists(atPath: url.appendingPathComponent($0).path) }
    }

    /// `start` or its nearest ancestor that is a valid repository.
    public static func walkUp(from start: URL, maxLevels: Int = 16, fileManager: FileManager = .default) -> URL? {
        var url = start.standardizedFileURL
        for _ in 0...maxLevels {
            if isValidRepo(url, fileManager: fileManager) { return url }
            let parent = url.deletingLastPathComponent()
            if parent.path == url.path { break }
            url = parent
        }
        return nil
    }

    /// The value of `AV_SOUND_REPO` in `environment`, or `nil` when it is unset or blank.
    public static func environmentPath(_ environment: [String: String]) -> String? {
        guard let path = environment[environmentKey]?.trimmingCharacters(in: .whitespacesAndNewlines),
            !path.isEmpty
        else { return nil }
        return path
    }

    /// The candidates in search order: explicit path, `AV_SOUND_REPO`, the executable's and
    /// the app bundle's location (walking up), the current directory (walking up), the
    /// persisted path.
    public static func candidates(
        explicit: URL? = nil,
        environment: [String: String] = ProcessInfo.processInfo.environment,
        executableURL: URL? = Bundle.main.executableURL,
        bundleURL: URL? = Bundle.main.bundleURL,
        currentDirectory: URL? = URL(fileURLWithPath: FileManager.default.currentDirectoryPath),
        defaults: UserDefaults? = .standard
    ) -> [Candidate] {
        var list: [Candidate] = []
        if let explicit { list.append(Candidate(url: explicit, source: .explicit, walksUp: false)) }
        if let path = environmentPath(environment) {
            list.append(Candidate(url: URL(fileURLWithPath: path), source: .environment, walksUp: false))
        }
        if let executableURL {
            list.append(
                Candidate(url: executableURL.deletingLastPathComponent(), source: .executable, walksUp: true))
        }
        if let bundleURL {
            list.append(Candidate(url: bundleURL.deletingLastPathComponent(), source: .executable, walksUp: true))
        }
        if let currentDirectory {
            list.append(Candidate(url: currentDirectory, source: .currentDirectory, walksUp: true))
        }
        if let path = defaults?.string(forKey: defaultsKey), !path.isEmpty {
            list.append(Candidate(url: URL(fileURLWithPath: path), source: .persisted, walksUp: false))
        }
        return list
    }

    /// The first valid repository among `candidates(...)`.
    ///
    /// `AV_SOUND_REPO` overrides the search: when it is set (and no valid explicit path
    /// comes first) but is not the repository, the search stops with
    /// `BridgeSetupError.environmentRepoInvalid` instead of going on to another checkout
    /// (the app's own, or the current directory's). Without any valid candidate it throws
    /// `BridgeSetupError.repoNotFound`.
    public static func resolve(
        explicit: URL? = nil,
        environment: [String: String] = ProcessInfo.processInfo.environment,
        executableURL: URL? = Bundle.main.executableURL,
        bundleURL: URL? = Bundle.main.bundleURL,
        currentDirectory: URL? = URL(fileURLWithPath: FileManager.default.currentDirectoryPath),
        defaults: UserDefaults? = .standard,
        fileManager: FileManager = .default
    ) throws(BridgeSetupError) -> Located {
        let list = candidates(
            explicit: explicit, environment: environment, executableURL: executableURL,
            bundleURL: bundleURL, currentDirectory: currentDirectory, defaults: defaults)
        for candidate in list {
            if candidate.walksUp {
                if let url = walkUp(from: candidate.url, fileManager: fileManager) {
                    return Located(url: url, source: candidate.source)
                }
            } else if isValidRepo(candidate.url.standardizedFileURL, fileManager: fileManager) {
                return Located(url: candidate.url.standardizedFileURL, source: candidate.source)
            } else if candidate.source == .environment {
                throw .environmentRepoInvalid(path: candidate.url.path)
            }
        }
        throw .repoNotFound
    }

    /// The repository `resolve(...)` finds, or `nil` (also when `AV_SOUND_REPO` is set but
    /// is not the repository).
    public static func locate(
        explicit: URL? = nil,
        environment: [String: String] = ProcessInfo.processInfo.environment,
        executableURL: URL? = Bundle.main.executableURL,
        bundleURL: URL? = Bundle.main.bundleURL,
        currentDirectory: URL? = URL(fileURLWithPath: FileManager.default.currentDirectoryPath),
        defaults: UserDefaults? = .standard,
        fileManager: FileManager = .default
    ) -> Located? {
        try? resolve(
            explicit: explicit, environment: environment, executableURL: executableURL, bundleURL: bundleURL,
            currentDirectory: currentDirectory, defaults: defaults, fileManager: fileManager)
    }

    /// Remembers a repository path the user chose.
    public static func persist(_ url: URL, defaults: UserDefaults = .standard) {
        defaults.set(url.standardizedFileURL.path, forKey: defaultsKey)
    }

    /// Forgets the persisted path.
    public static func clearPersisted(defaults: UserDefaults = .standard) {
        defaults.removeObject(forKey: defaultsKey)
    }
}

/// What prevents the bridge from being launched.
public enum BridgeSetupError: Error, Sendable, Hashable {
    case uvNotFound(searched: [String])
    case repoNotFound
    /// `AV_SOUND_REPO` is set to a folder that is not the repository. The variable
    /// overrides the search, so no other checkout is used in its place.
    case environmentRepoInvalid(path: String)

    public var message: String {
        switch self {
        case .uvNotFound(let searched):
            "uv was not found. Searched: \(searched.joined(separator: ", ")). Install uv or choose its path."
        case .repoNotFound:
            "The repository was not found. Choose the folder that contains sound/pyproject.toml, or set \(RepoLocator.environmentKey)."
        case .environmentRepoInvalid(let path):
            "\(RepoLocator.environmentKey) is set to \(path), which is not the repository (it must contain "
                + RepoLocator.requiredFiles.joined(separator: " and ")
                + "). Correct or unset \(RepoLocator.environmentKey): it overrides the search, so no other checkout is used."
        }
    }
}

extension BridgeSetupError: LocalizedError {
    public var errorDescription: String? { message }
}

extension ProcessBridgeTransport.Configuration {
    /// Locates uv and the repository and returns the launch configuration.
    public static func locate(
        explicitUV: String? = nil, explicitRepo: URL? = nil,
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) throws -> ProcessBridgeTransport.Configuration {
        guard let uv = UVLocator.locate(explicit: explicitUV, environment: environment) else {
            throw BridgeSetupError.uvNotFound(
                searched: UVLocator.candidates(explicit: explicitUV, environment: environment).map(\.path))
        }
        // A valid explicit path first, else AV_SOUND_REPO (an invalid value is an error,
        // not a fall-through), else the search.
        let repo = try RepoLocator.resolve(explicit: explicitRepo, environment: environment)
        return .bridge(uv: uv, repoRoot: repo.url, baseEnvironment: environment)
    }
}
