import Foundation

/// Any JSON value, for raw bridge payloads (validation results, store records, scan logs).
///
/// Numbers keep the integer/float distinction of the JSON text: `1` decodes as `.int(1)`,
/// `1.0` and `1e2` as `.double`. That holds for `init(jsonData:)` and for every value
/// inside a bridge response decoded through `BridgeCoding`, so raw views and
/// `compactString` show the bytes' numbers (a store record keeps `1.0`). A plain
/// `JSONDecoder` without `BridgeCoding`'s document source cannot see the difference; then
/// a whole number decodes as `.int` (also `1.0`).
public enum JSONValue: Sendable, Hashable, Codable {
    case null
    case bool(Bool)
    case int(Int)
    case double(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    public init(from decoder: any Decoder) throws {
        if let source = decoder.userInfo[JSONDocumentSource.userInfoKey] as? JSONDocumentSource,
            let value = source.value(at: decoder.codingPath)
        {
            self = value
            return
        }
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Int.self) {
            self = .int(value)
        } else if let value = try? container.decode(Double.self) {
            self = .double(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container, debugDescription: "Value is not a JSON value")
        }
    }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .null: try container.encodeNil()
        case .bool(let value): try container.encode(value)
        case .int(let value): try container.encode(value)
        case .double(let value): try container.encode(value)
        case .string(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        }
    }

    /// Decodes a JSON document (UTF-8), keeping integer and float numbers apart.
    /// Non-finite numbers (`NaN`, `Infinity`) are accepted; a document that is not strict
    /// JSON is read with `BridgeCoding.makeDecoder()` (JSON5).
    public init(jsonData: Data) throws {
        if let value = try? JSONValueParser.parse(jsonData) {
            self = value
        } else {
            self = try BridgeCoding.makeDecoder().decode(JSONValue.self, from: jsonData)
        }
    }

    /// Decodes a JSON document given as text.
    public init(jsonString: String) throws {
        try self.init(jsonData: Data(jsonString.utf8))
    }

    // MARK: Accessors

    public subscript(key: String) -> JSONValue? {
        if case .object(let object) = self { return object[key] }
        return nil
    }

    public subscript(index: Int) -> JSONValue? {
        if case .array(let array) = self, array.indices.contains(index) { return array[index] }
        return nil
    }

    public var isNull: Bool {
        if case .null = self { return true }
        return false
    }

    public var boolValue: Bool? {
        if case .bool(let value) = self { return value }
        return nil
    }

    /// The value as an integer (`.int`, or a `.double` that is a whole number).
    public var intValue: Int? {
        switch self {
        case .int(let value): return value
        case .double(let value):
            guard value.isFinite, value.rounded() == value, abs(value) < 9.0e15 else { return nil }
            return Int(value)
        default: return nil
        }
    }

    /// The value as a floating-point number (`.int` or `.double`).
    public var doubleValue: Double? {
        switch self {
        case .int(let value): return Double(value)
        case .double(let value): return value
        default: return nil
        }
    }

    public var stringValue: String? {
        if case .string(let value) = self { return value }
        return nil
    }

    public var arrayValue: [JSONValue]? {
        if case .array(let value) = self { return value }
        return nil
    }

    public var objectValue: [String: JSONValue]? {
        if case .object(let value) = self { return value }
        return nil
    }

    // MARK: Text

    /// Compact JSON with sorted keys, Python style for floats (`1.0`, not `1`). For an
    /// ASCII value this is the engine's `canonical_json` (store records, recipes).
    public var compactString: String {
        var out = ""
        write(to: &out, indent: nil, level: 0)
        return out
    }

    /// Indented JSON with sorted keys (two spaces), for detail and log views.
    public var prettyString: String {
        var out = ""
        write(to: &out, indent: "  ", level: 0)
        return out
    }

    /// Decodes this value as a `Decodable` type.
    public func decode<T: Decodable>(_ type: T.Type) throws -> T {
        let data = try BridgeCoding.makeEncoder().encode(self)
        return try BridgeCoding.makeDecoder().decode(T.self, from: data)
    }

    private func write(to out: inout String, indent: String?, level: Int) {
        switch self {
        case .null: out += "null"
        case .bool(let value): out += value ? "true" : "false"
        case .int(let value): out += String(value)
        case .double(let value): out += JSONValue.formatDouble(value)
        case .string(let value): JSONValue.writeString(value, to: &out)
        case .array(let values):
            if values.isEmpty {
                out += "[]"
                return
            }
            out += "["
            for (i, value) in values.enumerated() {
                if i > 0 { out += "," }
                if let indent {
                    out += "\n" + String(repeating: indent, count: level + 1)
                }
                value.write(to: &out, indent: indent, level: level + 1)
            }
            if let indent { out += "\n" + String(repeating: indent, count: level) }
            out += "]"
        case .object(let object):
            if object.isEmpty {
                out += "{}"
                return
            }
            out += "{"
            for (i, key) in object.keys.sorted().enumerated() {
                if i > 0 { out += "," }
                if let indent {
                    out += "\n" + String(repeating: indent, count: level + 1)
                }
                JSONValue.writeString(key, to: &out)
                out += indent == nil ? ":" : ": "
                object[key]!.write(to: &out, indent: indent, level: level + 1)
            }
            if let indent { out += "\n" + String(repeating: indent, count: level) }
            out += "}"
        }
    }

    /// Shortest round-trip text of a double, as Python's `repr` writes it (`1.0`, `0.6`).
    static func formatDouble(_ value: Double) -> String {
        if value.isNaN { return "NaN" }
        if value.isInfinite { return value < 0 ? "-Infinity" : "Infinity" }
        return value.description
    }

    static func writeString(_ value: String, to out: inout String) {
        out += "\""
        for scalar in value.unicodeScalars {
            switch scalar {
            case "\"": out += "\\\""
            case "\\": out += "\\\\"
            case "\n": out += "\\n"
            case "\r": out += "\\r"
            case "\t": out += "\\t"
            case "\u{08}": out += "\\b"
            case "\u{0C}": out += "\\f"
            default:
                if scalar.value < 0x20 {
                    out += String(format: "\\u%04x", scalar.value)
                } else {
                    out.unicodeScalars.append(scalar)
                }
            }
        }
        out += "\""
    }
}

extension JSONValue: ExpressibleByBooleanLiteral,
    ExpressibleByIntegerLiteral, ExpressibleByFloatLiteral, ExpressibleByStringLiteral,
    ExpressibleByArrayLiteral, ExpressibleByDictionaryLiteral
{
    public init(booleanLiteral value: Bool) { self = .bool(value) }
    public init(integerLiteral value: Int) { self = .int(value) }
    public init(floatLiteral value: Double) { self = .double(value) }
    public init(stringLiteral value: String) { self = .string(value) }
    public init(arrayLiteral elements: JSONValue...) { self = .array(elements) }
    public init(dictionaryLiteral elements: (String, JSONValue)...) {
        self = .object(Dictionary(elements, uniquingKeysWith: { _, last in last }))
    }
}
