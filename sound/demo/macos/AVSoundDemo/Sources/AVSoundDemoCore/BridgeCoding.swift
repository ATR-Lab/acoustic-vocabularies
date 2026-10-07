import Foundation

/// Encoding and decoding of protocol lines (PROTOCOL.md, "Envelope").
public enum BridgeCoding {
    /// The decoder used for every response. It also accepts the non-finite number tokens
    /// that Python's `json.dumps` writes (`NaN`, `Infinity`, `-Infinity`).
    public static func makeDecoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.allowsJSON5 = true
        return decoder
    }

    /// `makeDecoder()` for decoding `document`: every `JSONValue` inside it keeps the
    /// document's integer/float distinction (`1.0` stays a float in raw views).
    public static func makeDecoder(for document: Data) -> JSONDecoder {
        let decoder = makeDecoder()
        decoder.userInfo[JSONDocumentSource.userInfoKey] = JSONDocumentSource(document)
        return decoder
    }

    /// The encoder used for request arguments: compact, sorted keys.
    public static func makeEncoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return encoder
    }

    /// One request line without the trailing newline:
    /// `{"id":7,"cmd":"render","args":{...}}`. `argsJSON` must be a JSON object.
    public static func requestLine(id: Int, cmd: String, argsJSON: Data) -> Data {
        var line = Data("{\"id\":\(id),\"cmd\":".utf8)
        var cmdText = ""
        JSONValue.writeString(cmd, to: &cmdText)
        line.append(Data(cmdText.utf8))
        line.append(Data(",\"args\":".utf8))
        line.append(argsJSON)
        line.append(UInt8(ascii: "}"))
        return line
    }

    /// A success response line (for in-memory transports, fixtures and previews).
    public static func successLine(id: Int?, result: JSONValue) -> String {
        let response: JSONValue = [
            "id": id.map(JSONValue.int) ?? .null, "ok": true, "result": result,
        ]
        return response.compactString
    }

    /// A failure response line (for in-memory transports, fixtures and previews).
    public static func errorLine(
        id: Int?, type: String, code: String?, message: String, details: JSONValue? = nil
    ) -> String {
        var error: [String: JSONValue] = [
            "type": .string(type), "code": code.map(JSONValue.string) ?? .null,
            "message": .string(message),
        ]
        if let details { error["details"] = details }
        let response: JSONValue = [
            "id": id.map(JSONValue.int) ?? .null, "ok": false, "error": .object(error),
        ]
        return response.compactString
    }

    /// Decodes the `result` of a response line as `R`, or throws the `BridgeError` the
    /// line describes (`.engine`, `.protocolViolation` or `.decoding`).
    public static func decodeResult<R: Decodable>(_ type: R.Type, from line: Data, cmd: String) throws -> R {
        let body: ResponseBody<R>
        do {
            body = try makeDecoder(for: line).decode(ResponseBody<R>.self, from: line)
        } catch {
            throw BridgeError.protocolViolation("\(cmd): response is not a JSON object (\(error.localizedDescription))")
        }
        switch body.outcome {
        case .success(let value): return value
        case .failure(let error): throw error.withCommand(cmd)
        }
    }

    /// A readable description of a decoding error, with its coding path (without the
    /// first `droppingFirst` keys, for example the envelope's `result`).
    public static func describe(_ error: any Error, droppingFirst: Int = 0) -> String {
        guard let error = error as? DecodingError else { return String(describing: error) }
        func path(_ codingPath: [any CodingKey]) -> String {
            let text = codingPath.dropFirst(droppingFirst).map { key in
                key.intValue.map { "[\($0)]" } ?? ".\(key.stringValue)"
            }.joined()
            return text.isEmpty ? "<root>" : String(text.drop(while: { $0 == "." }))
        }
        switch error {
        case .keyNotFound(let key, let context):
            let at = path(context.codingPath + [key])
            return "missing key \(at)"
        case .typeMismatch(let type, let context):
            return "wrong type at \(path(context.codingPath)): expected \(type)"
        case .valueNotFound(let type, let context):
            return "null at \(path(context.codingPath)): expected \(type)"
        case .dataCorrupted(let context):
            return "invalid value at \(path(context.codingPath)): \(context.debugDescription)"
        @unknown default:
            return String(describing: error)
        }
    }
}

/// The routing fields of a response line.
struct ResponseHeader: Decodable {
    var id: Int?
    var hasIDKey: Bool
    var ok: Bool?
    var error: EngineErrorPayload?

    enum CodingKeys: String, CodingKey { case id, ok, error }

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        hasIDKey = c.contains(.id)
        id = try? c.decodeIfPresent(Int.self, forKey: .id)
        ok = try? c.decodeIfPresent(Bool.self, forKey: .ok)
        error = try? c.decodeIfPresent(EngineErrorPayload.self, forKey: .error)
    }
}

/// `error` of a failure response.
struct EngineErrorPayload: Decodable, Sendable {
    var type: String
    var code: String?
    var message: String
    var details: JSONValue?

    enum CodingKeys: String, CodingKey { case type, code, message, details }

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        type = try c.decode(String.self, forKey: .type)
        code = try c.decodeIfPresent(String.self, forKey: .code)
        message = (try? c.decodeIfPresent(String.self, forKey: .message)) ?? ""
        let details = try c.decodeIfPresent(JSONValue.self, forKey: .details)
        self.details = (details?.isNull ?? true) ? nil : details
    }

    var bridgeError: BridgeError {
        .engine(type: type, code: code, message: message, details: details)
    }
}

/// A whole response decoded for a known result type.
struct ResponseBody<R: Decodable>: Decodable {
    var outcome: Result<R, BridgeError>

    enum CodingKeys: String, CodingKey { case id, ok, result, error }

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        guard let ok = try? c.decode(Bool.self, forKey: .ok) else {
            outcome = .failure(.protocolViolation("response has no boolean \"ok\""))
            return
        }
        if ok {
            guard c.contains(.result) else {
                outcome = .failure(.protocolViolation("success response has no \"result\""))
                return
            }
            do {
                outcome = .success(try c.decode(R.self, forKey: .result))
            } catch {
                outcome = .failure(.decoding(BridgeCoding.describe(error, droppingFirst: 1)))
            }
        } else {
            do {
                let payload = try c.decode(EngineErrorPayload.self, forKey: .error)
                outcome = .failure(payload.bridgeError)
            } catch {
                outcome = .failure(
                    .protocolViolation("failure response has no valid \"error\" (\(BridgeCoding.describe(error)))"))
            }
        }
    }
}

// MARK: - Lenient decoding helpers

extension KeyedDecodingContainer {
    /// A string, or the compact JSON text of any other value (for loosely specified fields).
    func decodeLenientString(forKey key: Key) throws -> String {
        if let text = try? decode(String.self, forKey: key) { return text }
        let value = try decode(JSONValue.self, forKey: key)
        return value.compactString
    }

    /// An optional value that is `nil` when missing, `null` or not decodable as `T`.
    func decodeLossy<T: Decodable>(_ type: T.Type, forKey key: Key) -> T? {
        (try? decodeIfPresent(T.self, forKey: key)) ?? nil
    }
}
