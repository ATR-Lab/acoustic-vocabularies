import Foundation

/// A strict JSON parser that keeps what `JSONDecoder` loses: whether a number was written
/// as an integer (`1`) or a float (`1.0`, `1e2`). The bridge sends Python's JSON, where
/// that difference is part of the bytes (store records are hashed as canonical JSON), so
/// raw payloads shown in the app must keep it.
///
/// It accepts RFC 8259 JSON plus the non-finite tokens Python writes (`NaN`, `Infinity`,
/// `-Infinity`). Duplicate object keys keep the last value, as `JSONDecoder` does.
struct JSONValueParser {
    struct ParseError: Error, CustomStringConvertible {
        let offset: Int
        let reason: String

        var description: String { "invalid JSON at byte \(offset): \(reason)" }
    }

    /// Nesting deeper than this is refused. The bridge never nests more than a few levels,
    /// and the parser recurses once per level, so the limit keeps it far from the stack
    /// limit of a secondary thread (512 KiB), also in debug builds.
    static let maxDepth = 64

    private let bytes: [UInt8]
    private var index = 0

    private init(_ data: Data) {
        bytes = [UInt8](data)
    }

    /// Parses one JSON document (UTF-8).
    static func parse(_ data: Data) throws(ParseError) -> JSONValue {
        var parser = JSONValueParser(data)
        parser.skipWhitespace()
        let value = try parser.parseValue(depth: 0)
        parser.skipWhitespace()
        guard parser.index == parser.bytes.count else { throw parser.error("trailing characters") }
        return value
    }

    // MARK: Values

    private mutating func parseValue(depth: Int) throws(ParseError) -> JSONValue {
        guard depth < Self.maxDepth else { throw error("nesting deeper than \(Self.maxDepth)") }
        guard let byte = peek() else { throw error("unexpected end of input") }
        switch byte {
        case UInt8(ascii: "{"): return try parseObject(depth: depth)
        case UInt8(ascii: "["): return try parseArray(depth: depth)
        case UInt8(ascii: "\""): return .string(try parseString())
        case UInt8(ascii: "t"): try expect("true"); return .bool(true)
        case UInt8(ascii: "f"): try expect("false"); return .bool(false)
        case UInt8(ascii: "n"): try expect("null"); return .null
        case UInt8(ascii: "N"): try expect("NaN"); return .double(.nan)
        case UInt8(ascii: "I"): try expect("Infinity"); return .double(.infinity)
        case UInt8(ascii: "-") where peek(at: 1) == UInt8(ascii: "I"):
            index += 1
            try expect("Infinity")
            return .double(-.infinity)
        case UInt8(ascii: "-"), UInt8(ascii: "0")...UInt8(ascii: "9"): return try parseNumber()
        default: throw error("unexpected character")
        }
    }

    private mutating func parseObject(depth: Int) throws(ParseError) -> JSONValue {
        index += 1  // {
        var object: [String: JSONValue] = [:]
        skipWhitespace()
        if peek() == UInt8(ascii: "}") {
            index += 1
            return .object(object)
        }
        while true {
            skipWhitespace()
            guard peek() == UInt8(ascii: "\"") else { throw error("expected a string key") }
            let key = try parseString()
            skipWhitespace()
            guard peek() == UInt8(ascii: ":") else { throw error("expected ':'") }
            index += 1
            skipWhitespace()
            object[key] = try parseValue(depth: depth + 1)
            skipWhitespace()
            switch peek() {
            case UInt8(ascii: ","): index += 1
            case UInt8(ascii: "}"): index += 1; return .object(object)
            default: throw error("expected ',' or '}'")
            }
        }
    }

    private mutating func parseArray(depth: Int) throws(ParseError) -> JSONValue {
        index += 1  // [
        var array: [JSONValue] = []
        skipWhitespace()
        if peek() == UInt8(ascii: "]") {
            index += 1
            return .array(array)
        }
        while true {
            skipWhitespace()
            array.append(try parseValue(depth: depth + 1))
            skipWhitespace()
            switch peek() {
            case UInt8(ascii: ","): index += 1
            case UInt8(ascii: "]"): index += 1; return .array(array)
            default: throw error("expected ',' or ']'")
            }
        }
    }

    /// `-? (0 | [1-9][0-9]*) (. [0-9]+)? ([eE] [+-]? [0-9]+)?`: an `.int` when there is
    /// neither a fraction nor an exponent (and it fits), otherwise a `.double`.
    private mutating func parseNumber() throws(ParseError) -> JSONValue {
        let start = index
        var isFloat = false
        if peek() == UInt8(ascii: "-") { index += 1 }
        guard let first = peek(), isDigit(first) else { throw error("expected a digit") }
        if first == UInt8(ascii: "0") {
            index += 1
            if let next = peek(), isDigit(next) { throw error("leading zero") }
        } else {
            skipDigits()
        }
        if peek() == UInt8(ascii: ".") {
            isFloat = true
            index += 1
            guard let next = peek(), isDigit(next) else { throw error("expected a digit after '.'") }
            skipDigits()
        }
        if peek() == UInt8(ascii: "e") || peek() == UInt8(ascii: "E") {
            isFloat = true
            index += 1
            if peek() == UInt8(ascii: "+") || peek() == UInt8(ascii: "-") { index += 1 }
            guard let next = peek(), isDigit(next) else { throw error("expected an exponent") }
            skipDigits()
        }
        let text = String(decoding: bytes[start..<index], as: UTF8.self)
        if !isFloat, let value = Int(text) { return .int(value) }
        guard let value = Double(text) else { throw error("number out of range") }
        return .double(value)
    }

    private mutating func parseString() throws(ParseError) -> String {
        index += 1  // opening quote
        var buffer: [UInt8] = []
        while true {
            guard let byte = peek() else { throw error("unterminated string") }
            switch byte {
            case UInt8(ascii: "\""):
                index += 1
                return String(decoding: buffer, as: UTF8.self)
            case UInt8(ascii: "\\"):
                index += 1
                guard let escape = peek() else { throw error("unterminated escape") }
                index += 1
                switch escape {
                case UInt8(ascii: "\""): buffer.append(UInt8(ascii: "\""))
                case UInt8(ascii: "\\"): buffer.append(UInt8(ascii: "\\"))
                case UInt8(ascii: "/"): buffer.append(UInt8(ascii: "/"))
                case UInt8(ascii: "b"): buffer.append(0x08)
                case UInt8(ascii: "f"): buffer.append(0x0C)
                case UInt8(ascii: "n"): buffer.append(0x0A)
                case UInt8(ascii: "r"): buffer.append(0x0D)
                case UInt8(ascii: "t"): buffer.append(0x09)
                case UInt8(ascii: "u"): buffer.append(contentsOf: try parseUnicodeEscape())
                default: throw error("invalid escape")
                }
            case 0x00..<0x20:
                throw error("control character in a string")
            default:
                buffer.append(byte)
                index += 1
            }
        }
    }

    /// The UTF-8 bytes of `\uXXXX` (after the `u`), joining a surrogate pair. A lone
    /// surrogate becomes U+FFFD.
    private mutating func parseUnicodeEscape() throws(ParseError) -> [UInt8] {
        let high = try parseHex4()
        var scalar = Unicode.Scalar(high)
        if (0xD800..<0xDC00).contains(high), peek() == UInt8(ascii: "\\"), peek(at: 1) == UInt8(ascii: "u") {
            let saved = index
            index += 2
            let low = try parseHex4()
            if (0xDC00..<0xE000).contains(low) {
                scalar = Unicode.Scalar(0x10000 + ((high - 0xD800) << 10) + (low - 0xDC00))
            } else {
                index = saved
            }
        }
        return Array(String(Character(scalar ?? "\u{FFFD}")).utf8)
    }

    private mutating func parseHex4() throws(ParseError) -> UInt32 {
        guard index + 4 <= bytes.count else { throw error("short \\u escape") }
        var value: UInt32 = 0
        for _ in 0..<4 {
            let byte = bytes[index]
            let digit: UInt32
            switch byte {
            case UInt8(ascii: "0")...UInt8(ascii: "9"): digit = UInt32(byte - UInt8(ascii: "0"))
            case UInt8(ascii: "a")...UInt8(ascii: "f"): digit = UInt32(byte - UInt8(ascii: "a") + 10)
            case UInt8(ascii: "A")...UInt8(ascii: "F"): digit = UInt32(byte - UInt8(ascii: "A") + 10)
            default: throw error("invalid hex digit")
            }
            value = value << 4 | digit
            index += 1
        }
        return value
    }

    // MARK: Scanning

    private func peek(at offset: Int = 0) -> UInt8? {
        index + offset < bytes.count ? bytes[index + offset] : nil
    }

    private func isDigit(_ byte: UInt8) -> Bool {
        byte >= UInt8(ascii: "0") && byte <= UInt8(ascii: "9")
    }

    private mutating func skipDigits() {
        while let byte = peek(), isDigit(byte) { index += 1 }
    }

    private mutating func skipWhitespace() {
        while let byte = peek(), byte == 0x20 || byte == 0x09 || byte == 0x0A || byte == 0x0D { index += 1 }
    }

    private mutating func expect(_ literal: String) throws(ParseError) {
        for expected in literal.utf8 {
            guard peek() == expected else { throw error("expected \(literal)") }
            index += 1
        }
    }

    private func error(_ reason: String) -> ParseError {
        ParseError(offset: index, reason: reason)
    }
}

/// The JSON document a `JSONDecoder` is decoding, parsed on demand by `JSONValueParser`.
///
/// `BridgeCoding` puts one in the decoder's `userInfo`; `JSONValue.init(from:)` then
/// takes the node at the decoder's coding path from it instead of decoding through
/// `JSONDecoder`, so number lexemes survive (`1.0` stays a float).
final class JSONDocumentSource: @unchecked Sendable {
    static let userInfoKey = CodingUserInfoKey(rawValue: "AVSoundDemoCore.JSONDocumentSource")!

    private let data: Data
    private let lock = NSLock()
    private var parsed = false
    private var root: JSONValue?

    init(_ data: Data) {
        self.data = data
    }

    /// The value at `codingPath`, or `nil` when the document is not strict JSON or the
    /// path does not lead to a value (the caller then decodes as before).
    func value(at codingPath: [any CodingKey]) -> JSONValue? {
        lock.lock()
        defer { lock.unlock() }
        if !parsed {
            parsed = true
            root = try? JSONValueParser.parse(data)
        }
        guard var node = root else { return nil }
        for key in codingPath {
            switch node {
            case .array(let array):
                guard let i = key.intValue, array.indices.contains(i) else { return nil }
                node = array[i]
            case .object(let object):
                guard let child = object[key.stringValue] else { return nil }
                node = child
            default:
                return nil
            }
        }
        return node
    }
}
