import AVFoundation
import Foundation
import Observation

/// Plays `VerifiedAudio` (and only that) through `AVAudioEngine`.
///
/// The player node runs at 48 kHz Float32 with the same signal (`sample / 32768`) in two
/// channels; the main mixer converts to the output device at unity gain, so what is played
/// is `sample / 32768` at any output sample rate. (A mono connection is not: the mixer
/// plays mono input 3 dB lower, by 1/sqrt(2), on 44.1 kHz outputs, and at full level on
/// 48 kHz outputs.) A sound is scheduled as one buffer, so a composed message keeps its
/// exact 9,600-sample gap. Engine start failures and output device changes never crash:
/// they set `lastError` and end playback. A sound that the player itself stops (an output
/// device change) is reported through `onInterruption`, because its `completion` does not
/// run.
///
/// The engine runs only while a sound plays: it is paused when the sound ends or is
/// stopped (a running engine keeps the output device active, and macOS then keeps the
/// Mac from idle sleep), and `play` starts it again.
@MainActor
@Observable
public final class AudioPlayer {
    /// Channels of the player node: the mono signal of the WAV, the same in both.
    public nonisolated static let channelCount: AVAudioChannelCount = 2

    /// The player node format: 48 kHz two-channel Float32, non-interleaved.
    public nonisolated static func makeFormat() -> AVAudioFormat {
        guard let format = AVAudioFormat(
            commonFormat: .pcmFormatFloat32, sampleRate: Double(VerifiedAudio.sampleRate),
            channels: channelCount, interleaved: false)
        else { fatalError("48 kHz two-channel Float32 is always a valid format") }
        return format
    }

    /// True from `play` until the sound has been played back or `stop()` is called.
    public private(set) var isPlaying = false
    /// The sound being played (`nil` when idle).
    public private(set) var current: VerifiedAudio?
    /// The last engine problem, for the UI (cleared by a successful `play` and by
    /// `clearLastError()`).
    public private(set) var lastError: String?
    /// Output volume of the player node, 0...1.
    public var volume: Float = 1 {
        didSet { player?.volume = volume }
    }
    /// Runs when the player stops a sound on its own before the end (the output device
    /// changed). Not run for `stop()`, for a replacing `play`, or at the normal end (that
    /// runs the sound's `completion`).
    @ObservationIgnored public var onInterruption: (@MainActor () -> Void)?

    @ObservationIgnored private var engine: AVAudioEngine?
    @ObservationIgnored private var player: AVAudioPlayerNode?
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private var completion: (@MainActor () -> Void)?
    @ObservationIgnored nonisolated(unsafe) private var configurationObserver: (any NSObjectProtocol)?

    public init() {}

    deinit {
        if let configurationObserver {
            NotificationCenter.default.removeObserver(configurationObserver)
        }
    }

    /// Where playback is in the sound being played, in seconds from its first sample, as
    /// the listener hears it: the player's render position minus the output's
    /// presentation latency (a Bluetooth output adds about 200 ms). `nil` when idle, 0
    /// until the first sample has reached the output.
    public var playbackPosition: TimeInterval? {
        guard isPlaying, let engine, let player else { return nil }
        guard let renderTime = player.lastRenderTime, renderTime.isSampleTimeValid,
            let playerTime = player.playerTime(forNodeTime: renderTime), playerTime.sampleRate > 0
        else { return 0 }
        let rendered = Double(playerTime.sampleTime) / playerTime.sampleRate
        return max(0, rendered - engine.outputNode.presentationLatency)
    }

    /// Whether the engine is running (it runs only while a sound plays).
    var isEngineRunning: Bool { engine?.isRunning ?? false }

    /// The engine's main mixer, while the engine exists (for the gain test).
    var mainMixer: AVAudioMixerNode? { engine?.mainMixerNode }

    /// Plays `audio` from the start, replacing any sound that is playing. `completion` runs
    /// once the last sample has been played back (not when the sound is stopped or
    /// replaced).
    public func play(_ audio: VerifiedAudio, completion: (@MainActor () -> Void)? = nil) {
        stopPlayback()
        guard audio.nSamples > 0 else {
            completion?()
            return
        }
        guard let buffer = Self.makeBuffer(samples: audio.samples) else {
            lastError = "Could not allocate an audio buffer."
            return
        }
        do {
            let (engine, player) = try readyEngine()
            generation += 1
            let token = generation
            self.completion = completion
            player.scheduleBuffer(buffer, at: nil, options: [], completionCallbackType: .dataPlayedBack) {
                [weak self] _ in
                Task { @MainActor [weak self] in self?.finished(token: token) }
            }
            if !engine.isRunning { try engine.start() }
            player.play()
            current = audio
            isPlaying = true
            lastError = nil
        } catch {
            lastError = "Audio output failed: \(error.localizedDescription)"
            stopPlayback()
            tearDownEngine()
        }
    }

    /// Stops playback at once and pauses the engine. The completion of the stopped sound
    /// does not run.
    public func stop() {
        stopPlayback()
        pauseEngine()
    }

    /// Dismisses `lastError` (the UI's close button on the audio notice).
    public func clearLastError() {
        lastError = nil
    }

    /// The Float32 samples the player gets in each channel (`sample / 32768`).
    public nonisolated static func floatSamples(_ samples: [Int16]) -> [Float] {
        samples.map { Float($0) / 32768 }
    }

    /// One 48 kHz Float32 buffer holding all samples, `sample / 32768` in both channels.
    public nonisolated static func makeBuffer(samples: [Int16]) -> AVAudioPCMBuffer? {
        guard !samples.isEmpty,
            let buffer = AVAudioPCMBuffer(pcmFormat: makeFormat(), frameCapacity: AVAudioFrameCount(samples.count)),
            let channels = buffer.floatChannelData, buffer.format.channelCount == channelCount
        else { return nil }
        let left = channels[0]
        let right = channels[1]
        samples.withUnsafeBufferPointer { source in
            for i in 0..<source.count {
                let value = Float(source[i]) / 32768
                left[i] = value
                right[i] = value
            }
        }
        buffer.frameLength = AVAudioFrameCount(samples.count)
        return buffer
    }

    // MARK: Engine

    private func readyEngine() throws -> (AVAudioEngine, AVAudioPlayerNode) {
        if let engine, let player { return (engine, player) }
        let engine = AVAudioEngine()
        let player = AVAudioPlayerNode()
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: Self.makeFormat())
        player.volume = volume
        engine.prepare()
        configurationObserver = NotificationCenter.default.addObserver(
            forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.configurationChanged() }
        }
        self.engine = engine
        self.player = player
        return (engine, player)
    }

    /// The output device changed: the engine has stopped. End playback, tell
    /// `onInterruption` if a sound was cut off, and rebuild the engine on the next `play`.
    /// (Internal for tests: `AVAudioEngineConfigurationChange` calls it.)
    func configurationChanged() {
        let interrupted = isPlaying
        if interrupted { lastError = "The audio output changed; playback stopped." }
        stopPlayback()
        tearDownEngine()
        if interrupted { onInterruption?() }
    }

    private func tearDownEngine() {
        if let configurationObserver {
            NotificationCenter.default.removeObserver(configurationObserver)
        }
        configurationObserver = nil
        player?.stop()
        engine?.stop()
        player = nil
        engine = nil
    }

    /// Pauses the idle engine, which releases the output device (and the system's
    /// idle-sleep assertion for it). `play` starts the engine again.
    private func pauseEngine() {
        if let engine, engine.isRunning { engine.pause() }
    }

    private func stopPlayback() {
        generation += 1
        completion = nil
        player?.stop()
        isPlaying = false
        current = nil
    }

    private func finished(token: Int) {
        guard token == generation else { return }
        let pendingCompletion = completion
        completion = nil
        isPlaying = false
        current = nil
        pauseEngine()
        pendingCompletion?()
    }
}
