import Foundation

/// The audio fields of a response (PROTOCOL.md, "Audio"). `wavB64` is `nil` when no
/// audio exists (overflow, held-out message).
public struct AudioPayload: Decodable, Sendable {
    public let wavB64: String?
    public let fileSHA256: String?
    public let pcmSHA256: String?

    public init(wavB64: String?, fileSHA256: String?, pcmSHA256: String?) {
        self.wavB64 = wavB64
        self.fileSHA256 = fileSHA256
        self.pcmSHA256 = pcmSHA256
    }

    enum CodingKeys: String, CodingKey {
        case wavB64 = "wav_b64"
        case fileSHA256 = "file_sha256"
        case pcmSHA256 = "pcm_sha256"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        wavB64 = try c.decodeIfPresent(String.self, forKey: .wavB64)
        fileSHA256 = try c.decodeIfPresent(String.self, forKey: .fileSHA256)
        pcmSHA256 = try c.decodeIfPresent(String.self, forKey: .pcmSHA256)
    }

    public var hasAudio: Bool { wavB64 != nil }

    /// Decodes and checks the audio. This is the only way to get playable samples.
    public func verified() throws -> VerifiedAudio {
        guard let wavB64 else { throw AudioVerificationError.missingAudio }
        guard let fileSHA256 else { throw AudioVerificationError.malformedHash(field: "file_sha256", value: "null") }
        guard let pcmSHA256 else { throw AudioVerificationError.malformedHash(field: "pcm_sha256", value: "null") }
        return try VerifiedAudio(wavBase64: wavB64, fileSHA256: fileSHA256, pcmSHA256: pcmSHA256)
    }
}

extension AudioPayload: Hashable {
    public static func == (lhs: AudioPayload, rhs: AudioPayload) -> Bool {
        lhs.fileSHA256 == rhs.fileSHA256 && lhs.pcmSHA256 == rhs.pcmSHA256 && lhs.wavB64 == rhs.wavB64
    }

    public func hash(into hasher: inout Hasher) {
        hasher.combine(fileSHA256)
        hasher.combine(pcmSHA256)
    }
}

/// Why audio was refused. Unverified audio is never played.
public enum AudioVerificationError: Error, Sendable, Hashable {
    /// The response carries no audio (`wav_b64` is null).
    case missingAudio
    /// An expected hash is not 64 hex digits.
    case malformedHash(field: String, value: String)
    /// `wav_b64` is not valid base64.
    case invalidBase64
    /// `sha256(file bytes) != file_sha256`.
    case fileHashMismatch(expected: String, actual: String)
    /// Fewer than 44 bytes.
    case truncated(byteCount: Int)
    /// The sample data has an odd number of bytes.
    case oddDataLength(byteCount: Int)
    /// The header is not the canonical 44-byte header (renderer spec D8).
    case nonCanonicalHeader(String)
    /// `sha256(sample bytes) != pcm_sha256`.
    case pcmHashMismatch(expected: String, actual: String)

    public var message: String {
        switch self {
        case .missingAudio: "No audio in the response."
        case .malformedHash(let field, let value): "\(field) is not a SHA-256 hex digest: \(value.prefix(80))"
        case .invalidBase64: "wav_b64 is not valid base64."
        case .fileHashMismatch(let expected, let actual):
            "File hash mismatch: expected \(expected), got \(actual)."
        case .truncated(let count): "The WAV file has only \(count) bytes."
        case .oddDataLength(let count): "The WAV sample data has an odd length (\(count) bytes)."
        case .nonCanonicalHeader(let text): "Not a canonical WAV header: \(text)."
        case .pcmHashMismatch(let expected, let actual):
            "Waveform hash mismatch: expected \(expected), got \(actual)."
        }
    }
}

extension AudioVerificationError: LocalizedError {
    public var errorDescription: String? { message }
}

/// The canonical 48 kHz mono 16-bit WAV layout (renderer spec D8).
public enum CanonicalWAV {
    public static let headerSize = 44
    public static let sampleRate = 48_000
    public static let channels = 1
    public static let bitsPerSample = 16
    public static let byteRate = 96_000
    public static let blockAlign = 2

    /// The canonical header for `nSamples` samples.
    public static func header(nSamples: Int) -> Data {
        var data = Data(capacity: headerSize)
        let dataBytes = UInt32(2 * nSamples)
        data.append(contentsOf: Array("RIFF".utf8))
        append(UInt32(36) + dataBytes, to: &data)
        data.append(contentsOf: Array("WAVE".utf8))
        data.append(contentsOf: Array("fmt ".utf8))
        append(UInt32(16), to: &data)
        append(UInt16(1), to: &data)
        append(UInt16(channels), to: &data)
        append(UInt32(sampleRate), to: &data)
        append(UInt32(byteRate), to: &data)
        append(UInt16(blockAlign), to: &data)
        append(UInt16(bitsPerSample), to: &data)
        data.append(contentsOf: Array("data".utf8))
        append(dataBytes, to: &data)
        return data
    }

    /// int16 little-endian sample bytes.
    public static func pcmData(samples: [Int16]) -> Data {
        var data = Data(count: samples.count * 2)
        data.withUnsafeMutableBytes { raw in
            for (i, sample) in samples.enumerated() {
                raw.storeBytes(of: sample.littleEndian, toByteOffset: 2 * i, as: Int16.self)
            }
        }
        return data
    }

    /// Complete canonical WAV file bytes (header + samples).
    public static func fileData(samples: [Int16]) -> Data {
        header(nSamples: samples.count) + pcmData(samples: samples)
    }

    /// Checks the canonical layout field by field and returns the sample count.
    public static func validateLayout(_ data: Data) throws -> Int {
        guard data.count >= headerSize else { throw AudioVerificationError.truncated(byteCount: data.count) }
        let dataBytes = data.count - headerSize
        guard dataBytes % 2 == 0 else { throw AudioVerificationError.oddDataLength(byteCount: dataBytes) }
        let bytes = [UInt8](data.prefix(headerSize))
        func tag(_ offset: Int) -> String { String(decoding: bytes[offset..<offset + 4], as: UTF8.self) }
        func u32(_ offset: Int) -> UInt32 {
            UInt32(bytes[offset]) | UInt32(bytes[offset + 1]) << 8 | UInt32(bytes[offset + 2]) << 16
                | UInt32(bytes[offset + 3]) << 24
        }
        func u16(_ offset: Int) -> UInt16 { UInt16(bytes[offset]) | UInt16(bytes[offset + 1]) << 8 }
        func require(_ condition: Bool, _ text: @autoclosure () -> String) throws {
            if !condition { throw AudioVerificationError.nonCanonicalHeader(text()) }
        }
        try require(tag(0) == "RIFF", "chunk ID is \(tag(0).debugDescription), expected \"RIFF\"")
        try require(u32(4) == UInt32(36 + dataBytes), "RIFF size \(u32(4)), expected \(36 + dataBytes)")
        try require(tag(8) == "WAVE", "format is \(tag(8).debugDescription), expected \"WAVE\"")
        try require(tag(12) == "fmt ", "first subchunk is \(tag(12).debugDescription), expected \"fmt \"")
        try require(u32(16) == 16, "fmt size \(u32(16)), expected 16")
        try require(u16(20) == 1, "audio format \(u16(20)), expected 1 (PCM)")
        try require(u16(22) == UInt16(channels), "\(u16(22)) channels, expected 1")
        try require(u32(24) == UInt32(sampleRate), "sample rate \(u32(24)), expected 48000")
        try require(u32(28) == UInt32(byteRate), "byte rate \(u32(28)), expected 96000")
        try require(u16(32) == UInt16(blockAlign), "block align \(u16(32)), expected 2")
        try require(u16(34) == UInt16(bitsPerSample), "\(u16(34)) bits per sample, expected 16")
        try require(tag(36) == "data", "second subchunk is \(tag(36).debugDescription), expected \"data\"")
        try require(u32(40) == UInt32(dataBytes), "data size \(u32(40)), expected \(dataBytes)")
        return dataBytes / 2
    }

    private static func append<T: FixedWidthInteger>(_ value: T, to data: inout Data) {
        withUnsafeBytes(of: value.littleEndian) { data.append(contentsOf: $0) }
    }
}

/// Audio whose file hash, canonical header and waveform hash were all checked.
///
/// Build one with `init(wavBase64:fileSHA256:pcmSHA256:)` or `AudioPayload.verified()`.
/// The app plays only `VerifiedAudio`.
public struct VerifiedAudio: Sendable, Identifiable {
    /// Complete canonical WAV file bytes.
    public let wavData: Data
    /// int16 samples, 48 kHz mono.
    public let samples: [Int16]
    public let fileSHA256: String
    public let pcmSHA256: String

    public static let sampleRate = CanonicalWAV.sampleRate

    public var id: String { fileSHA256 }
    public var nSamples: Int { samples.count }
    public var durationSeconds: Double { Double(samples.count) / Double(Self.sampleRate) }
    /// The sample bytes (int16 little-endian), without the header.
    public var pcmData: Data { wavData.subdata(in: CanonicalWAV.headerSize..<wavData.count) }

    /// Decodes `wavBase64` and checks, in order: `sha256(file) == fileSHA256`, the canonical
    /// 44-byte header, `sha256(samples) == pcmSHA256`.
    public init(wavBase64: String, fileSHA256: String, pcmSHA256: String) throws {
        try Self.checkHashFormat(fileSHA256, field: "file_sha256")
        try Self.checkHashFormat(pcmSHA256, field: "pcm_sha256")
        guard let data = Data(base64Encoded: wavBase64) else { throw AudioVerificationError.invalidBase64 }
        try self.init(wavData: data, fileSHA256: fileSHA256, pcmSHA256: pcmSHA256)
    }

    /// Checks WAV file bytes against both hashes and the canonical header.
    public init(wavData: Data, fileSHA256: String, pcmSHA256: String) throws {
        try Self.checkHashFormat(fileSHA256, field: "file_sha256")
        try Self.checkHashFormat(pcmSHA256, field: "pcm_sha256")
        let expectedFile = fileSHA256.lowercased()
        let expectedPCM = pcmSHA256.lowercased()
        let actualFile = Hashing.sha256Hex(wavData)
        guard actualFile == expectedFile else {
            throw AudioVerificationError.fileHashMismatch(expected: expectedFile, actual: actualFile)
        }
        let count = try CanonicalWAV.validateLayout(wavData)
        let pcm = wavData.subdata(in: CanonicalWAV.headerSize..<wavData.count)
        let actualPCM = Hashing.sha256Hex(pcm)
        guard actualPCM == expectedPCM else {
            throw AudioVerificationError.pcmHashMismatch(expected: expectedPCM, actual: actualPCM)
        }
        var samples = [Int16](repeating: 0, count: count)
        pcm.withUnsafeBytes { raw in
            for i in 0..<count {
                samples[i] = Int16(littleEndian: raw.loadUnaligned(fromByteOffset: 2 * i, as: Int16.self))
            }
        }
        self.wavData = wavData
        self.samples = samples
        self.fileSHA256 = expectedFile
        self.pcmSHA256 = expectedPCM
    }

    private static func checkHashFormat(_ value: String, field: String) throws {
        guard Hashing.isSHA256Hex(value.lowercased()) else {
            throw AudioVerificationError.malformedHash(field: field, value: value)
        }
    }
}

extension VerifiedAudio: Hashable {
    /// Verified audio is identified by its two checked hashes.
    public static func == (lhs: VerifiedAudio, rhs: VerifiedAudio) -> Bool {
        lhs.fileSHA256 == rhs.fileSHA256 && lhs.pcmSHA256 == rhs.pcmSHA256
    }

    public func hash(into hasher: inout Hasher) {
        hasher.combine(fileSHA256)
        hasher.combine(pcmSHA256)
    }
}
