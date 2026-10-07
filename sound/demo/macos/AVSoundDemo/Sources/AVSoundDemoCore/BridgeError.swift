import Foundation

/// Every failure of a bridge call.
public enum BridgeError: Error, Sendable, Hashable {
    /// The engine refused the request (`ok: false`). `type` is the Python exception class
    /// (`RecipeError`, `HeldOutMessageError`, `CommitRejected`, ...), `code` the engine's
    /// reason code (`E_DOMAIN`, `E_HELDOUT`, ...) or `nil`, `details` the optional
    /// `error.details` (for `CommitRejected`: the validation result).
    case engine(type: String, code: String?, message: String, details: JSONValue?)
    /// The bridge broke the protocol (unparseable response, missing fields, wrong version).
    case protocolViolation(String)
    /// The bridge is not running (not started, stopping, or stopped).
    case notRunning
    /// The bridge process exited while the call was pending. `status` is the exit status,
    /// or 128 + the signal number when it was killed by a signal.
    case processExited(status: Int32, stderrTail: String)
    /// No response arrived within the call's timeout.
    case timeout(cmd: String)
    /// The response was well formed but its `result` did not match the expected shape.
    case decoding(String)
    /// The bridge process could not be launched (uv or the repository not found, ...).
    case launchFailed(String)
    /// The request arguments could not be encoded as JSON (for example a non-finite number).
    case invalidArguments(String)

    /// The engine error code (`E_HELDOUT`, ...) of an `.engine` error.
    public var engineCode: String? {
        if case .engine(_, let code, _, _) = self { return code }
        return nil
    }

    /// The Python exception class of an `.engine` error.
    public var engineType: String? {
        if case .engine(let type, _, _, _) = self { return type }
        return nil
    }

    /// `error.details` decoded as a validation result (`CommitRejected`).
    public var validationDetails: ValidationResult? {
        guard case .engine(_, _, _, let details?) = self else { return nil }
        return try? details.decode(ValidationResult.self)
    }

    /// One-line text for status bars and alerts.
    public var message: String {
        switch self {
        case .engine(let type, let code, let message, _):
            let prefix = code.map { "\(type) \($0)" } ?? type
            return message.isEmpty ? prefix : "\(prefix): \(message)"
        case .protocolViolation(let text):
            return "Protocol violation: \(text)"
        case .notRunning:
            return "The sound engine bridge is not running."
        case .processExited(let status, let tail):
            let last = tail.split(separator: "\n").last.map(String.init)
            let base = "The sound engine bridge exited with status \(status)."
            return last.map { "\(base) Last output: \($0)" } ?? base
        case .timeout(let cmd):
            return "The bridge did not answer \"\(cmd)\" in time."
        case .decoding(let text):
            return "Unexpected response: \(text)"
        case .launchFailed(let text):
            return "Could not start the bridge: \(text)"
        case .invalidArguments(let text):
            return "Invalid request: \(text)"
        }
    }

    func withCommand(_ cmd: String) -> BridgeError {
        switch self {
        case .protocolViolation(let text): .protocolViolation("\(cmd): \(text)")
        case .decoding(let text): .decoding("\(cmd): \(text)")
        default: self
        }
    }
}

extension BridgeError: LocalizedError {
    public var errorDescription: String? { message }
}
