import Foundation

/// Minimum and maximum per bucket, for drawing a waveform `bucketCount` points wide.
///
/// Values are normalized to -1...1 (`sample / 32768`). Bucket `i` covers the samples
/// `[i * n / bucketCount, (i + 1) * n / bucketCount)`, so every sample is in exactly one
/// bucket. With fewer samples than requested buckets there is one bucket per sample.
public struct WaveformSummary: Sendable, Hashable {
    public let mins: [Float]
    public let maxs: [Float]
    public let sampleCount: Int

    public var bucketCount: Int { mins.count }
    public var isEmpty: Bool { mins.isEmpty }
    /// The largest absolute normalized value.
    public var peak: Float {
        max(mins.map { -$0 }.max() ?? 0, maxs.max() ?? 0)
    }

    public init(samples: [Int16], buckets: Int) {
        let n = samples.count
        let count = max(0, min(buckets, n))
        sampleCount = n
        guard count > 0 else {
            mins = []
            maxs = []
            return
        }
        var lows = [Float](repeating: 0, count: count)
        var highs = [Float](repeating: 0, count: count)
        samples.withUnsafeBufferPointer { buffer in
            for bucket in 0..<count {
                let start = bucket * n / count
                let end = (bucket + 1) * n / count
                var low = buffer[start]
                var high = buffer[start]
                var i = start + 1
                while i < end {
                    let v = buffer[i]
                    if v < low { low = v }
                    if v > high { high = v }
                    i += 1
                }
                lows[bucket] = Float(low) / 32768
                highs[bucket] = Float(high) / 32768
            }
        }
        mins = lows
        maxs = highs
    }

    public init(audio: VerifiedAudio, buckets: Int) {
        self.init(samples: audio.samples, buckets: buckets)
    }

    /// The sample range of `bucket`.
    public func sampleRange(ofBucket bucket: Int) -> Range<Int> {
        let count = bucketCount
        precondition(bucket >= 0 && bucket < count, "bucket out of range")
        return (bucket * sampleCount / count)..<((bucket + 1) * sampleCount / count)
    }

    /// The x position (0...1) of a sample index, for event and gap markers.
    public func position(ofSample index: Int) -> Double {
        sampleCount == 0 ? 0 : Double(index) / Double(sampleCount)
    }
}
