import AVFoundation
import Foundation
import Observation

/// Plays `VerifiedAudio` (and only that) through `AVAudioEngine`.
///
/// The player node runs at 48 kHz Float32 with the same signal (`sample / 32768`) in each
/// channel, and has as many channels as the output needs for unity gain: two for a
/// stereo (or larger) output, one for a mono output. The main mixer then converts to the
/// output device at unity gain, so what is played is `sample / 32768` at any output
/// sample rate and channel count. (Other pairings are not: the mixer plays a mono input
/// 3 dB lower, by 1/sqrt(2), into a 44.1 kHz stereo output, and sums the two equal
/// channels of a stereo input 3 dB hot, by sqrt(2), into a mono output such as a
/// Bluetooth headset in hands-free mode.) The channel count is chosen when the engine is
/// built, and an output device change rebuilds the engine. A sound is scheduled as one
/// buffer, so a composed message keeps its exact 9,600-sample gap. Engine start failures
/// and output device changes never crash: they set `lastError` and end playback. A sound
/// that the player itself stops (an output device change) is reported through
/// `onInterruption`, and its `completion` does not run. On a device change the engine
/// stops itself first, which ends the scheduled buffer at once: its completion callback
/// fires tens of milliseconds before `AVAudioEngineConfigurationChange` is posted. A
/// buffer that ends while the engine is not running is that interruption, not the end
/// of the sound.
///
/// The engine runs only while a sound plays: it is paused when the sound ends or is
/// stopped (a running engine keeps the output device active, and macOS then keeps the
/// Mac from idle sleep), and `play` starts it again.
@MainActor
@Observable
public final class AudioPlayer {
    /// Channels of the player node for an output (the main mixer's output) with
    /// `outputChannels` channels: one for a mono output, two otherwise. Each channel
    /// carries the mono signal of the WAV.
    public nonisolated static func playerChannelCount(outputChannels: AVAudioChannelCount) -> AVAudioChannelCount {
        outputChannels == 1 ? 1 : 2
    }

    /// The player node format: 48 kHz Float32, non-interleaved, with `channels` (1 or 2)
    /// channels.
    public nonisolated static func makeFormat(channels: AVAudioChannelCount = 2) -> AVAudioFormat {
        guard (1...2).contains(channels),
            let format = AVAudioFormat(
                commonFormat: .pcmFormatFloat32, sampleRate: Double(VerifiedAudio.sampleRate),
                channels: channels, interleaved: false)
        else { fatalError("48 kHz one- or two-channel Float32 is always a valid format") }
        return format
    }

    /// Attaches `player` to `engine` and connects it to the main mixer in the format that
    /// plays at unity gain on the mixer's output (the output device's format, which the
    /// main mixer's output follows); returns the player's channel count.
    static func attach(_ player: AVAudioPlayerNode, to engine: AVAudioEngine) -> AVAudioChannelCount {
        let mixer = engine.mainMixerNode
        let channels = playerChannelCount(outputChannels: mixer.outputFormat(forBus: 0).channelCount)
        engine.attach(player)
        engine.connect(player, to: mixer, format: makeFormat(channels: channels))
        return channels
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
    /// changed, or the engine stopped for another reason). Not run for `stop()`, for a
    /// replacing `play`, or at the normal end (that runs the sound's `completion`).
    @ObservationIgnored public var onInterruption: (@MainActor () -> Void)?

    @ObservationIgnored private var engine: AVAudioEngine?
    @ObservationIgnored private var player: AVAudioPlayerNode?
    /// The channel count of `player` (see `attach`).
    @ObservationIgnored private var playerChannels: AVAudioChannelCount = 2
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

    /// Stops the engine as an output device change does, without the notification (for
    /// the interruption test).
    func stopEngineForTesting() {
        engine?.stop()
    }

    /// Plays `audio` from the start, replacing any sound that is playing. `completion` runs
    /// once the last sample has been played back (not when the sound is stopped,
    /// replaced, or cut off by an output device change).
    public func play(_ audio: VerifiedAudio, completion: (@MainActor () -> Void)? = nil) {
        stopPlayback()
        guard audio.nSamples > 0 else {
            completion?()
            return
        }
        do {
            let (engine, player) = try readyEngine()
            guard let buffer = Self.makeBuffer(samples: audio.samples, channels: playerChannels) else {
                lastError = "Could not allocate an audio buffer."
                return
            }
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

    /// One 48 kHz Float32 buffer holding all samples, `sample / 32768` in each of its
    /// `channels` (1 or 2) channels.
    public nonisolated static func makeBuffer(samples: [Int16], channels: AVAudioChannelCount = 2) -> AVAudioPCMBuffer? {
        guard !samples.isEmpty, (1...2).contains(channels),
            let buffer = AVAudioPCMBuffer(
                pcmFormat: makeFormat(channels: channels), frameCapacity: AVAudioFrameCount(samples.count)),
            let data = buffer.floatChannelData, buffer.format.channelCount == channels
        else { return nil }
        let first = data[0]
        samples.withUnsafeBufferPointer { source in
            for i in 0..<source.count {
                first[i] = Float(source[i]) / 32768
            }
        }
        for channel in 1..<Int(channels) {
            data[channel].update(from: first, count: samples.count)
        }
        buffer.frameLength = AVAudioFrameCount(samples.count)
        return buffer
    }

    // MARK: Engine

    private func readyEngine() throws -> (AVAudioEngine, AVAudioPlayerNode) {
        if let engine, let player { return (engine, player) }
        let engine = AVAudioEngine()
        let player = AVAudioPlayerNode()
        playerChannels = Self.attach(player, to: engine)
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

    /// The text of `lastError` after an interruption.
    static let interruptionMessage = "The audio output changed; playback stopped."

    /// The output device changed: the engine has stopped. End playback, tell
    /// `onInterruption` if a sound was cut off, and rebuild the engine on the next `play`.
    /// (Internal for tests: `AVAudioEngineConfigurationChange` calls it.) Usually the
    /// sound has already been reported by `finished(token:)`, which sees the engine
    /// stopped before this notification arrives; then this only tears the engine down.
    func configurationChanged() {
        interrupt(wasPlaying: isPlaying)
    }

    /// Ends playback after the engine stopped on its own, and rebuilds the engine on the
    /// next `play`. A sound that was playing is reported (`lastError`, `onInterruption`);
    /// its completion does not run.
    private func interrupt(wasPlaying: Bool) {
        if wasPlaying { lastError = Self.interruptionMessage }
        stopPlayback()
        tearDownEngine()
        if wasPlaying { onInterruption?() }
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

    /// The scheduled buffer of the sound `token` ended. Only `play` (which starts the
    /// engine) and this method (which pauses it) change whether the engine runs while
    /// that sound is current, so an engine that no longer runs stopped itself: an output
    /// device change, whose notification comes later. The buffer was cut off, not played
    /// to the end.
    private func finished(token: Int) {
        guard token == generation else { return }
        guard engine?.isRunning == true else {
            interrupt(wasPlaying: true)
            return
        }
        let pendingCompletion = completion
        completion = nil
        isPlaying = false
        current = nil
        pauseEngine()
        pendingCompletion?()
    }
}
