import AVFoundation
import Foundation
import Synchronization
import Testing

@testable import AVSoundDemoCore

/// `sample[i] = (mul * i + add) mod 65535 - 32767`, the pattern rule of the composition
/// test vectors.
func patternSamples(mul: Int, add: Int, count: Int) -> [Int16] {
    (0..<count).map { i in Int16((mul * i + add) % 65_535 - 32_767) }
}

/// A canonical WAV with its two hashes.
struct TestWAV {
    var data: Data
    var fileSHA256: String
    var pcmSHA256: String

    init(samples: [Int16]) {
        data = CanonicalWAV.fileData(samples: samples)
        fileSHA256 = Hashing.sha256Hex(data)
        pcmSHA256 = Hashing.sha256Hex(CanonicalWAV.pcmData(samples: samples))
    }

    /// Same file hash rule after editing the bytes (so the next check is reached).
    mutating func edit(_ change: (inout Data) -> Void) {
        change(&data)
        fileSHA256 = Hashing.sha256Hex(data)
    }

    func verify() throws -> VerifiedAudio {
        try VerifiedAudio(wavBase64: data.base64EncodedString(), fileSHA256: fileSHA256, pcmSHA256: pcmSHA256)
    }
}

extension Data {
    mutating func putUInt32(_ value: UInt32, at offset: Int) {
        Swift.withUnsafeBytes(of: value.littleEndian) { replaceSubrange(offset..<offset + 4, with: $0) }
    }

    mutating func putUInt16(_ value: UInt16, at offset: Int) {
        Swift.withUnsafeBytes(of: value.littleEndian) { replaceSubrange(offset..<offset + 2, with: $0) }
    }
}

@Suite("VerifiedAudio")
struct VerifiedAudioTests {
    let samples = patternSamples(mul: 7, add: 0, count: 4_800)

    @Test func acceptsACanonicalWAV() throws {
        let wav = TestWAV(samples: samples)
        let audio = try wav.verify()
        #expect(audio.samples == samples)
        #expect(audio.nSamples == 4_800)
        #expect(audio.durationSeconds == 0.1)
        #expect(audio.wavData == wav.data)
        #expect(audio.pcmData == CanonicalWAV.pcmData(samples: samples))
        #expect(audio.fileSHA256 == wav.fileSHA256)
        #expect(audio.id == wav.fileSHA256)
    }

    @Test func acceptsTheEngineWAV() throws {
        let audio = try VerifiedAudio(
            wavBase64: Fixtures.smallWAVBase64, fileSHA256: Fixtures.smallWAVFileSHA256,
            pcmSHA256: Fixtures.smallWAVPCMSHA256)
        #expect(audio.samples == Fixtures.smallWAVSamples)
        // The Swift writer produces the engine's bytes.
        #expect(CanonicalWAV.fileData(samples: Fixtures.smallWAVSamples).base64EncodedString() == Fixtures.smallWAVBase64)
    }

    @Test func hashesMatchTheCompositionVectors() throws {
        let vectors = try RepoFiles.json("testvectors/composition/vectors.json")
        let gap = try #require(vectors["gap_samples"]?.intValue)
        #expect(gap == 9_600)
        let patterns = try #require(vectors["patterns"]?.arrayValue)
        #expect(patterns.count == 4)
        for pattern in patterns {
            func part(_ key: String) throws -> [Int16] {
                let spec = try #require(pattern[key])
                return patternSamples(
                    mul: try #require(spec["mul"]?.intValue), add: try #require(spec["add"]?.intValue),
                    count: try #require(spec["n_samples"]?.intValue))
            }
            let action = try part("action")
            let referent = try part("referent")
            #expect(Hashing.sha256Hex(CanonicalWAV.pcmData(samples: action)) == pattern["action_pcm_sha256"]?.stringValue)
            #expect(
                Hashing.sha256Hex(CanonicalWAV.pcmData(samples: referent))
                    == pattern["referent_pcm_sha256"]?.stringValue)
            let message = action + [Int16](repeating: 0, count: gap) + referent
            #expect(message.count == pattern["n_samples"]?.intValue)
            let wav = TestWAV(samples: message)
            #expect(wav.pcmSHA256 == pattern["composite_sha256"]?.stringValue)
            let audio = try wav.verify()
            // The gap survives verification exactly.
            #expect(audio.samples[action.count..<(action.count + gap)].allSatisfy { $0 == 0 })
            #expect(audio.nSamples == message.count)
        }
        // The engine's file_sha256 of the "pattern-shortest" action.
        let shortest = patternSamples(mul: 7, add: 0, count: 21_600)
        #expect(TestWAV(samples: shortest).fileSHA256 == "1c4a5be0c73d1257a2d032b981d3b9c1509a6395b679c5b49324cfe92de59270")
    }

    @Test func rejectsAFlippedByte() throws {
        var wav = TestWAV(samples: samples)
        let originalFileHash = wav.fileSHA256
        wav.data[100] ^= 0x01
        // Detected by the file hash first ...
        #expect(throws: AudioVerificationError.fileHashMismatch(expected: originalFileHash, actual: Hashing.sha256Hex(wav.data))) {
            try wav.verify()
        }
        // ... and by the waveform hash when the file hash was made to match.
        wav.fileSHA256 = Hashing.sha256Hex(wav.data)
        #expect(throws: AudioVerificationError.self) { try wav.verify() }
        do {
            _ = try wav.verify()
        } catch let error as AudioVerificationError {
            guard case .pcmHashMismatch = error else {
                Issue.record("expected pcmHashMismatch, got \(error)")
                return
            }
        }
    }

    @Test func rejectsWrongHashes() throws {
        var wav = TestWAV(samples: samples)
        wav.fileSHA256 = String(repeating: "0", count: 64)
        #expect(throws: AudioVerificationError.fileHashMismatch(expected: wav.fileSHA256, actual: Hashing.sha256Hex(wav.data))) {
            try wav.verify()
        }
        var wrongPCM = TestWAV(samples: samples)
        wrongPCM.pcmSHA256 = String(repeating: "f", count: 64)
        #expect(throws: AudioVerificationError.pcmHashMismatch(expected: wrongPCM.pcmSHA256, actual: TestWAV(samples: samples).pcmSHA256)) {
            try wrongPCM.verify()
        }
        var malformed = TestWAV(samples: samples)
        malformed.pcmSHA256 = "xyz"
        #expect(throws: AudioVerificationError.malformedHash(field: "pcm_sha256", value: "xyz")) { try malformed.verify() }
        var upper = TestWAV(samples: samples)
        upper.fileSHA256 = upper.fileSHA256.uppercased()
        #expect(try upper.verify().fileSHA256 == upper.fileSHA256.lowercased())
    }

    /// One field of the canonical header changed.
    enum HeaderEdit: String, CaseIterable, Sendable {
        case sampleRate, channels, format, bits, byteRate, blockAlign, fmtSize, riffTag, waveTag, dataTag
        case riffSize, dataSize

        func apply(_ d: inout Data) {
            switch self {
            case .sampleRate: d.putUInt32(44_100, at: 24)
            case .channels: d.putUInt16(2, at: 22)
            case .format: d.putUInt16(3, at: 20)
            case .bits: d.putUInt16(24, at: 34)
            case .byteRate: d.putUInt32(48_000, at: 28)
            case .blockAlign: d.putUInt16(4, at: 32)
            case .fmtSize: d.putUInt32(18, at: 16)
            case .riffTag: d[3] = UInt8(ascii: "X")
            case .waveTag: d[8] = UInt8(ascii: "w")
            case .dataTag: d[36] = UInt8(ascii: "L")
            case .riffSize: d.putUInt32(0, at: 4)
            case .dataSize: d.putUInt32(2, at: 40)
            }
        }
    }

    @Test(arguments: HeaderEdit.allCases)
    func rejectsANonCanonicalHeader(edit: HeaderEdit) throws {
        var wav = TestWAV(samples: samples)
        wav.edit(edit.apply)
        do {
            _ = try wav.verify()
            Issue.record("\(edit): accepted")
        } catch let error as AudioVerificationError {
            guard case .nonCanonicalHeader = error else {
                Issue.record("\(edit): expected nonCanonicalHeader, got \(error)")
                return
            }
        }
    }

    @Test func rejectsAnOddLengthAndTruncation() throws {
        var odd = TestWAV(samples: samples)
        odd.edit { data in
            data.append(0x7F)
            data.putUInt32(UInt32(data.count - 8), at: 4)
            data.putUInt32(UInt32(data.count - 44), at: 40)
        }
        #expect(throws: AudioVerificationError.oddDataLength(byteCount: 9_601)) { try odd.verify() }
        var short = TestWAV(samples: samples)
        short.edit { $0 = $0.prefix(20) }
        #expect(throws: AudioVerificationError.truncated(byteCount: 20)) { try short.verify() }
        var extraChunk = TestWAV(samples: samples)
        extraChunk.edit { data in
            // A LIST chunk before "data" is valid RIFF but not canonical.
            data.insert(contentsOf: Array("LIST".utf8) + [4, 0, 0, 0] + Array("INFO".utf8), at: 36)
            data.putUInt32(UInt32(data.count - 8), at: 4)
        }
        #expect(throws: AudioVerificationError.self) { try extraChunk.verify() }
    }

    @Test func rejectsBadBase64() {
        let wav = TestWAV(samples: samples)
        #expect(throws: AudioVerificationError.invalidBase64) {
            try VerifiedAudio(wavBase64: "not base64!", fileSHA256: wav.fileSHA256, pcmSHA256: wav.pcmSHA256)
        }
        // Line breaks are not part of the protocol's base64.
        let wrapped = wav.data.base64EncodedString(options: .lineLength64Characters)
        #expect(throws: AudioVerificationError.invalidBase64) {
            try VerifiedAudio(wavBase64: wrapped, fileSHA256: wav.fileSHA256, pcmSHA256: wav.pcmSHA256)
        }
    }

    @Test func headerMatchesTheEngineBytes() {
        // wav_header(5) of the engine, hex.
        let expected = "524946462e00000057415645666d7420100000000100010080bb00000077010002001000646174610a000000"
        let actual = CanonicalWAV.header(nSamples: 5).map { String(format: "%02x", $0) }.joined()
        #expect(actual == expected)
        #expect(throws: Never.self) { try CanonicalWAV.validateLayout(CanonicalWAV.header(nSamples: 0)) }
    }
}

@Suite("WaveformSummary")
struct WaveformSummaryTests {
    @Test func minMaxPerBucket() {
        let samples: [Int16] = [0, 16_384, -8_192, 32_767, -32_768, 4, 8, -16]
        let summary = WaveformSummary(samples: samples, buckets: 4)
        #expect(summary.bucketCount == 4)
        let expectedMins: [Float] = [0, -0.25, -1, -16 / 32_768]
        let expectedMaxs: [Float] = [0.5, 32_767 / 32_768, 4 / 32_768, 8 / 32_768]
        #expect(summary.mins == expectedMins)
        #expect(summary.maxs == expectedMaxs)
        #expect(summary.peak == 1)
        #expect(summary.sampleRange(ofBucket: 1) == 2..<4)
        #expect(summary.position(ofSample: 4) == 0.5)
    }

    @Test func unevenBucketsCoverEverySampleOnce() {
        let n = 96_000
        let samples = patternSamples(mul: 13, add: 1_000, count: n)
        let summary = WaveformSummary(samples: samples, buckets: 701)
        #expect(summary.bucketCount == 701)
        var next = 0
        for bucket in 0..<summary.bucketCount {
            let range = summary.sampleRange(ofBucket: bucket)
            #expect(range.lowerBound == next)
            #expect(!range.isEmpty)
            next = range.upperBound
            let slice = samples[range]
            #expect(summary.mins[bucket] == Float(slice.min()!) / 32_768)
            #expect(summary.maxs[bucket] == Float(slice.max()!) / 32_768)
        }
        #expect(next == n)
    }

    @Test func fewerSamplesThanBucketsAndEmpty() {
        let summary = WaveformSummary(samples: [100, -100], buckets: 10)
        #expect(summary.bucketCount == 2)
        #expect(summary.mins == summary.maxs)
        #expect(WaveformSummary(samples: [], buckets: 10).isEmpty)
        #expect(WaveformSummary(samples: [1, 2, 3], buckets: 0).isEmpty)
        #expect(WaveformSummary(samples: [], buckets: 10).peak == 0)
    }

    @Test func fastForAFullMessage() {
        let samples = patternSamples(mul: 3, add: 32_767, count: 96_000)
        let clock = ContinuousClock()
        let elapsed = clock.measure {
            for _ in 0..<10 { _ = WaveformSummary(samples: samples, buckets: 1_200) }
        }
        #expect(elapsed < .seconds(2))
    }
}

@Suite("AudioPlayer")
@MainActor
struct AudioPlayerTests {
    /// The mono WAV signal goes into both channels of a two-channel player (for a stereo
    /// output: the mixer plays a mono connection 3 dB lower on 44.1 kHz stereo outputs, see
    /// `AudioPlayer`), or into the one channel of a mono player (for a mono output).
    @Test func convertsInt16ToFloatInBothChannels() throws {
        let samples: [Int16] = [0, 16_384, -32_768, 32_767, -1]
        let expected: [Float] = [0, 0.5, -1, 32_767 / 32_768, -1 / 32_768]
        #expect(AudioPlayer.floatSamples(samples) == expected)
        let buffer = try #require(AudioPlayer.makeBuffer(samples: samples))
        #expect(buffer.frameLength == 5)
        #expect(buffer.format.sampleRate == 48_000)
        #expect(buffer.format.channelCount == 2)
        #expect(!buffer.format.isInterleaved)
        #expect(buffer.format.commonFormat == .pcmFormatFloat32)
        #expect(buffer.format == AudioPlayer.makeFormat())
        let channels = try #require(buffer.floatChannelData)
        for channel in 0..<2 {
            #expect((0..<5).map { channels[channel][$0] } == expected, "channel \(channel)")
        }
        #expect(AudioPlayer.makeBuffer(samples: []) == nil)

        let mono = try #require(AudioPlayer.makeBuffer(samples: samples, channels: 1))
        #expect(mono.format == AudioPlayer.makeFormat(channels: 1))
        #expect(mono.format.channelCount == 1 && mono.format.sampleRate == 48_000 && mono.frameLength == 5)
        let monoData = try #require(mono.floatChannelData)
        #expect((0..<5).map { monoData[0][$0] } == expected)
        #expect(AudioPlayer.makeBuffer(samples: samples, channels: 3) == nil)
    }

    /// The player matches a mono output with one channel and any other output with two.
    @Test func thePlayerChannelsFollowTheOutput() {
        #expect(AudioPlayer.playerChannelCount(outputChannels: 1) == 1)
        for outputs: AVAudioChannelCount in [0, 2, 6, 8] {
            #expect(AudioPlayer.playerChannelCount(outputChannels: outputs) == 2, "\(outputs) output channels")
        }
    }

    /// What the mixer sends to the output is `sample / 32768` at unity gain for mono and
    /// stereo outputs at 44.1 and 48 kHz, through the player's own connection
    /// (`AudioPlayer.attach`). Rendered offline (no audio device): the output format is
    /// the manual rendering format, and the main mixer is connected to it explicitly, as
    /// the engine does for a device. The signal is a quiet DC plateau (328 / 32768) with
    /// raised-cosine ramps. A two-channel player into a mono output would give sqrt(2).
    @Test(arguments: [1, 2] as [AVAudioChannelCount], [44_100.0, 48_000.0])
    func theOutputGetsUnityGainOffline(outputChannels: AVAudioChannelCount, outputRate: Double) throws {
        let level: Int16 = 328
        let ramp = 4_800
        let rise = (0..<ramp).map { i in
            Int16((Double(level) * (1 - cos(Double.pi * Double(i) / Double(ramp))) / 2).rounded())
        }
        let samples = rise + [Int16](repeating: level, count: 19_200) + rise.reversed()

        let engine = AVAudioEngine()
        let format = try #require(AVAudioFormat(standardFormatWithSampleRate: outputRate, channels: outputChannels))
        let maxFrames: AVAudioFrameCount = 4_096
        try engine.enableManualRenderingMode(.offline, format: format, maximumFrameCount: maxFrames)
        engine.connect(engine.mainMixerNode, to: engine.outputNode, format: format)
        let player = AVAudioPlayerNode()
        let channels = AudioPlayer.attach(player, to: engine)
        #expect(channels == (outputChannels == 1 ? 1 : 2))
        #expect(engine.mainMixerNode.outputFormat(forBus: 0).channelCount == outputChannels)
        let buffer = try #require(AudioPlayer.makeBuffer(samples: samples, channels: channels))
        try engine.start()
        defer { engine.stop() }
        player.scheduleBuffer(buffer)
        player.play()

        let output = try #require(AVAudioPCMBuffer(pcmFormat: engine.manualRenderingFormat, frameCapacity: maxFrames))
        let peak = PeakMeter()
        let total = Int((Double(samples.count) * outputRate / 48_000).rounded(.up)) + Int(maxFrames) * 2
        var rendered = 0
        while rendered < total {
            let status = try engine.renderOffline(maxFrames, to: output)
            #expect(status == .success)
            guard status == .success, output.frameLength > 0 else { break }
            peak.record(output)
            rendered += Int(output.frameLength)
        }
        let gain = peak.value / (Float(level) / 32_768)
        #expect(abs(gain - 1) < 1e-3, "gain \(gain) into \(outputChannels) channel(s) at \(outputRate) Hz")
    }

    @Test func oneBufferHoldsAWholeMessage() throws {
        let message = patternSamples(mul: 7, add: 0, count: 21_600) + [Int16](repeating: 0, count: 9_600)
            + patternSamples(mul: 13, add: 1_000, count: 21_600)
        let buffer = try #require(AudioPlayer.makeBuffer(samples: message))
        #expect(buffer.frameLength == 52_800)
        let channels = try #require(buffer.floatChannelData)
        for channel in 0..<2 {
            #expect((21_600..<31_200).allSatisfy { channels[channel][$0] == 0 }, "channel \(channel)")
            #expect((0..<52_800).allSatisfy { channels[channel][$0] == Float(message[$0]) / 32_768 })
        }
    }

    @Test func lastErrorCanBeDismissed() {
        let player = AudioPlayer()
        player.clearLastError()  // nothing to clear: harmless
        #expect(player.lastError == nil)
    }

    @Test func aDeviceChangeWhileIdleIsNoInterruption() {
        let player = AudioPlayer()
        var interruptions = 0
        player.onInterruption = { interruptions += 1 }
        player.configurationChanged()
        #expect(interruptions == 0)
        #expect(player.lastError == nil)
        #expect(!player.isPlaying)
    }

    @Test func idleStopAndEmptyAudio() throws {
        let player = AudioPlayer()
        player.stop()
        #expect(!player.isPlaying)
        let empty = try TestWAV(samples: []).verify()
        var completed = false
        player.play(empty) { completed = true }
        #expect(completed)
        #expect(!player.isPlaying)
        #expect(player.lastError == nil)
    }
}

/// Real output through AVAudioEngine at volume 0. Opt-in (needs an audio device):
/// `AV_SOUND_AUDIO_TESTS=1 swift test ...`.
@Suite("AudioPlayer playback (AV_SOUND_AUDIO_TESTS=1)",
       .enabled(if: ProcessInfo.processInfo.environment["AV_SOUND_AUDIO_TESTS"] == "1"))
@MainActor
struct AudioPlaybackTests {
    @Test func playsAndCompletes() async throws {
        let player = AudioPlayer()
        player.volume = 0
        let audio = try TestWAV(samples: patternSamples(mul: 7, add: 0, count: 9_600)).verify()
        let done = AsyncStream<Void>.makeStream()
        player.play(audio) { done.continuation.yield() }
        #expect(player.lastError == nil)
        #expect(player.isPlaying)
        #expect(player.current == audio)
        let finished = await withTaskGroup(of: Bool.self) { group in
            group.addTask { for await _ in done.stream { return true }; return false }
            group.addTask { try? await Task.sleep(for: .seconds(5)); return false }
            let first = await group.next() ?? false
            group.cancelAll()
            return first
        }
        #expect(finished)
        #expect(!player.isPlaying)
        // Stopping mid-sound does not run the completion.
        var stoppedCompletion = false
        player.play(audio) { stoppedCompletion = true }
        player.stop()
        try await Task.sleep(for: .milliseconds(400))
        #expect(!stoppedCompletion)
        #expect(!player.isPlaying)
    }

    /// The engine runs only while a sound plays: a running engine keeps the output device
    /// active (and the Mac from idle sleep). It is paused at the end of a sound and by
    /// `stop()`, and the next `play` starts it again.
    @Test func theEngineRunsOnlyWhileASoundPlays() async throws {
        let player = AudioPlayer()
        player.volume = 0
        let audio = try TestWAV(samples: patternSamples(mul: 7, add: 0, count: 9_600)).verify()
        #expect(!player.isEngineRunning)
        for round in 0..<2 {
            let done = AsyncStream<Void>.makeStream()
            player.play(audio) { done.continuation.yield() }
            #expect(player.isPlaying, "round \(round)")
            #expect(player.isEngineRunning, "round \(round)")
            let finished = await withTaskGroup(of: Bool.self) { group in
                group.addTask { for await _ in done.stream { return true }; return false }
                group.addTask { try? await Task.sleep(for: .seconds(5)); return false }
                let first = await group.next() ?? false
                group.cancelAll()
                return first
            }
            #expect(finished, "round \(round)")
            #expect(!player.isEngineRunning, "round \(round)")
        }
        player.play(audio)
        #expect(player.isEngineRunning)
        player.stop()
        #expect(!player.isEngineRunning)
        #expect(player.lastError == nil)
    }

    /// The playhead position is what the listener hears: it starts at 0, never runs
    /// backwards, never passes the end of the sound, and is `nil` when idle.
    @Test func thePlaybackPositionFollowsTheSound() async throws {
        let player = AudioPlayer()
        player.volume = 0
        #expect(player.playbackPosition == nil)
        let audio = try TestWAV(samples: patternSamples(mul: 7, add: 0, count: 28_800)).verify()
        let duration = audio.durationSeconds
        var finished = false
        player.play(audio) { finished = true }
        let clock = ContinuousClock()
        let started = clock.now
        var positions: [TimeInterval] = []
        while !finished, clock.now - started < .seconds(5) {
            if let position = player.playbackPosition { positions.append(position) }
            try await Task.sleep(for: .milliseconds(5))
        }
        #expect(finished)
        #expect(player.playbackPosition == nil)
        #expect(positions.first == 0)
        #expect(zip(positions, positions.dropFirst()).allSatisfy { $0 <= $1 })
        #expect(positions.allSatisfy { $0 >= 0 && $0 <= duration + 0.05 })
        #expect((positions.last ?? 0) > duration / 2)
    }

    /// What reaches the output is `sample / 32768` at unity gain, whatever the output's
    /// sample rate. A tap on the main mixer reads a quiet DC plateau (328 / 32768, about
    /// -40 dBFS, with 100 ms raised-cosine ramps: nothing to hear) at the player's volume 1.
    /// A mono player gives 0.7071 here on a 44.1 kHz stereo output. Only this device is
    /// measured; `theOutputGetsUnityGainOffline` covers mono and stereo outputs.
    @Test func theMixerPlaysSamplesAtUnityGain() async throws {
        let level: Int16 = 328
        let ramp = 4_800
        let rise = (0..<ramp).map { i in
            Int16((Double(level) * (1 - cos(Double.pi * Double(i) / Double(ramp))) / 2).rounded())
        }
        let samples = rise + [Int16](repeating: level, count: 19_200) + rise.reversed()
        let audio = try TestWAV(samples: samples).verify()
        let player = AudioPlayer()
        #expect(player.volume == 1)
        let peak = PeakMeter()
        let done = AsyncStream<Void>.makeStream()
        player.play(audio) { done.continuation.yield() }
        let mixer = try #require(player.mainMixer)
        let outputRate = mixer.outputFormat(forBus: 0).sampleRate
        // The tap runs on the audio thread: the block must not be main-actor isolated.
        mixer.installTap(onBus: 0, bufferSize: 1_024, format: nil) { @Sendable buffer, _ in peak.record(buffer) }
        defer { mixer.removeTap(onBus: 0) }
        let finished = await withTaskGroup(of: Bool.self) { group in
            group.addTask { for await _ in done.stream { return true }; return false }
            group.addTask { try? await Task.sleep(for: .seconds(5)); return false }
            let first = await group.next() ?? false
            group.cancelAll()
            return first
        }
        #expect(finished)
        #expect(player.lastError == nil)
        let gain = peak.value / (Float(level) / 32_768)
        #expect(abs(gain - 1) < 1e-3, "mixer gain \(gain) at an output of \(outputRate) Hz")
    }

    /// An output device change mid-sound (AVAudioEngineConfigurationChange) stops the
    /// sound: its completion does not run, so `onInterruption` must.
    @Test func aDeviceChangeMidSoundIsReported() async throws {
        let player = AudioPlayer()
        player.volume = 0
        let audio = try TestWAV(samples: patternSamples(mul: 7, add: 0, count: 96_000)).verify()
        var interruptions = 0
        var completed = false
        player.onInterruption = { interruptions += 1 }
        player.play(audio) { completed = true }
        #expect(player.isPlaying)
        player.configurationChanged()
        #expect(interruptions == 1)
        #expect(!player.isPlaying)
        #expect(player.lastError == "The audio output changed; playback stopped.")
        try await Task.sleep(for: .milliseconds(300))
        #expect(!completed)
        // The notice stays until it is dismissed (or a later play succeeds).
        player.stop()
        #expect(player.lastError != nil)
        player.clearLastError()
        #expect(player.lastError == nil)
        // The engine is rebuilt on the next play; stop() is not an interruption.
        player.play(audio)
        player.stop()
        #expect(interruptions == 1)
    }
}

/// The largest absolute sample seen by an audio tap (any channel), across tap calls.
final class PeakMeter: Sendable {
    private let peak = Mutex<Float>(0)

    var value: Float { peak.withLock { $0 } }

    func record(_ buffer: AVAudioPCMBuffer) {
        guard let channels = buffer.floatChannelData else { return }
        var largest: Float = 0
        for channel in 0..<Int(buffer.format.channelCount) {
            for frame in 0..<Int(buffer.frameLength) {
                largest = max(largest, abs(channels[channel][frame]))
            }
        }
        peak.withLock { $0 = max($0, largest) }
    }
}
