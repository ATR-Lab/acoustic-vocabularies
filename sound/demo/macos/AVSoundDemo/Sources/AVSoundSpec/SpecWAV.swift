// Canonical WAV container (renderer spec D8), SHA-256 helpers (D9) and the message
// composite hash (D9, composition spec).

import CryptoKit
import Foundation

public enum SpecWAV {
    /// Canonical 44-byte header (48 kHz, mono, 16-bit PCM, no other chunks) followed by
    /// `pcm`. `pcm` must hold int16 little-endian samples (an even byte count).
    public static func canonicalBytes(pcm: Data) -> Data {
        let dataBytes = UInt32(truncatingIfNeeded: pcm.count)
        var out = Data(capacity: 44 + pcm.count)
        func u32(_ v: UInt32) { withUnsafeBytes(of: v.littleEndian) { out.append(contentsOf: $0) } }
        func u16(_ v: UInt16) { withUnsafeBytes(of: v.littleEndian) { out.append(contentsOf: $0) } }
        out.append(contentsOf: Array("RIFF".utf8))
        u32(36 &+ dataBytes)
        out.append(contentsOf: Array("WAVE".utf8))
        out.append(contentsOf: Array("fmt ".utf8))
        u32(16)  // fmt chunk size
        u16(1)  // PCM
        u16(1)  // channels
        u32(48_000)  // sample rate
        u32(96_000)  // byte rate
        u16(2)  // block align
        u16(16)  // bits per sample
        out.append(contentsOf: Array("data".utf8))
        u32(dataBytes)
        out.append(pcm)
        return out
    }
}

public enum SpecHash {
    /// Lowercase hex SHA-256 of `data`.
    public static func sha256Hex(_ data: Data) -> String {
        hex(SHA256.hash(data: data))
    }

    static func hex(_ digest: SHA256.Digest) -> String {
        let digits = Array("0123456789abcdef".utf8)
        var chars: [UInt8] = []
        chars.reserveCapacity(64)
        for byte in digest {
            chars.append(digits[Int(byte >> 4)])
            chars.append(digits[Int(byte & 0x0F)])
        }
        return String(decoding: chars, as: UTF8.self)
    }
}

public enum SpecComposer {
    /// Silent gap between action and referent: 200 ms at 48 kHz.
    public static let gapSamples = 9600

    /// SHA-256 of `actionPCM + 2 * gapSamples zero bytes + referentPCM`, lowercase hex.
    public static func compositeHash(actionPCM: Data, referentPCM: Data) -> String {
        var hasher = SHA256()
        hasher.update(data: actionPCM)
        hasher.update(data: Data(count: 2 * gapSamples))
        hasher.update(data: referentPCM)
        return SpecHash.hex(hasher.finalize())
    }
}
