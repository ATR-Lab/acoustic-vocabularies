import AVSoundDemoCore
import SwiftUI

/// A labelled sample range drawn over a waveform: a motif event, a gap, or a message part.
struct WaveSpan: Identifiable, Hashable {
    enum Kind: Hashable { case event, gap }

    let id: String
    let range: Range<Int>
    let kind: Kind
    let title: String
    var detail: String?
    var color: Color = .accentColor
    /// Drawn with a red dashed outline (a short event).
    var warning = false
}

/// A min/max waveform with event spans, shaded gaps, a millisecond axis and an optional
/// playhead. `sampleCount` sets the time scale (it also works without a waveform).
struct WaveformView: View {
    let waveform: WaveformSummary?
    let sampleCount: Int
    var spans: [WaveSpan] = []
    var playhead: Double?
    var tint: Color = SpanPalette.waveform
    var height: CGFloat = 210

    private let labelBand: CGFloat = 34
    private let axisBand: CGFloat = 22

    var body: some View {
        Canvas { context, size in
            draw(in: &context, size: size)
        }
        .frame(height: height)
        .accessibilityElement()
        .accessibilityLabel(accessibilityText)
    }

    private var accessibilityText: String {
        let events = spans.filter { $0.kind == .event }.map(\.title).joined(separator: ", ")
        return "Waveform, \(Fmt.ms(samples: sampleCount)). \(events)"
    }

    private func draw(in context: inout GraphicsContext, size: CGSize) {
        let width = size.width
        let wave = CGRect(x: 0, y: labelBand, width: width, height: max(20, size.height - labelBand - axisBand))
        let n = max(sampleCount, 1)
        func x(_ sample: Int) -> CGFloat { CGFloat(sample) / CGFloat(n) * width }

        context.fill(Path(roundedRect: wave, cornerRadius: 4), with: .color(Color.secondary.opacity(0.07)))

        // Spans: tinted events and hatched gaps.
        func rect(of span: WaveSpan) -> CGRect {
            CGRect(
                x: x(span.range.lowerBound), y: wave.minY,
                width: max(1, x(span.range.upperBound) - x(span.range.lowerBound)), height: wave.height)
        }
        for span in spans {
            let rect = rect(of: span)
            switch span.kind {
            case .event:
                context.fill(Path(rect), with: .color(span.color.opacity(0.13)))
                context.fill(
                    Path(CGRect(x: rect.minX, y: wave.minY, width: rect.width, height: 3)),
                    with: .color(span.color.opacity(0.85)))
                if span.warning {
                    context.stroke(
                        Path(rect.insetBy(dx: 0.75, dy: 0.75)), with: .color(.red),
                        style: StrokeStyle(lineWidth: 1.5, dash: [5, 3]))
                }
            case .gap:
                context.fill(Path(rect), with: .color(Color.gray.opacity(0.16)))
                var hatch = Path()
                var hx = rect.minX - rect.height
                while hx < rect.maxX {
                    hatch.move(to: CGPoint(x: hx, y: rect.maxY))
                    hatch.addLine(to: CGPoint(x: hx + rect.height, y: rect.minY))
                    hx += 7
                }
                context.drawLayer { layer in
                    layer.clip(to: Path(rect))
                    layer.stroke(hatch, with: .color(Color.gray.opacity(0.35)), lineWidth: 1)
                }
            }
        }

        // Labels above: every event label, then the gap labels that do not overlap one.
        var occupied: [CGRect] = []
        for span in spans where span.kind == .event {
            occupied += drawLabel(span, over: rect(of: span), in: &context, width: width, avoiding: [])
        }
        for span in spans where span.kind == .gap {
            _ = drawLabel(span, over: rect(of: span), in: &context, width: width, avoiding: occupied)
        }

        // Zero line.
        var zero = Path()
        zero.move(to: CGPoint(x: 0, y: wave.midY))
        zero.addLine(to: CGPoint(x: width, y: wave.midY))
        context.stroke(zero, with: .color(Color.secondary.opacity(0.35)), lineWidth: 0.5)

        // Waveform: the band between the per-bucket maxima and minima.
        if let waveform, !waveform.isEmpty, waveform.sampleCount > 0 {
            let count = waveform.bucketCount
            let scale = CGFloat(waveform.sampleCount) / CGFloat(n)
            let amplitude = wave.height / 2 * 0.94
            func bx(_ i: Int) -> CGFloat { (CGFloat(i) + 0.5) / CGFloat(count) * width * scale }
            var band = Path()
            for i in 0..<count {
                let point = CGPoint(x: bx(i), y: wave.midY - CGFloat(waveform.maxs[i]) * amplitude)
                if i == 0 { band.move(to: point) } else { band.addLine(to: point) }
            }
            for i in stride(from: count - 1, through: 0, by: -1) {
                band.addLine(to: CGPoint(x: bx(i), y: wave.midY - CGFloat(waveform.mins[i]) * amplitude))
            }
            band.closeSubpath()
            context.fill(band, with: .color(tint))
            context.stroke(band, with: .color(tint), lineWidth: 0.5)
        }

        drawAxis(in: &context, wave: wave, width: width, samples: n)

        if let playhead {
            let px = CGFloat(min(max(playhead, 0), 1)) * width
            var line = Path()
            line.move(to: CGPoint(x: px, y: wave.minY - 4))
            line.addLine(to: CGPoint(x: px, y: wave.maxY + 4))
            context.stroke(line, with: .color(.orange), lineWidth: 2)
        }
    }

    /// Draws the title and detail of `span` centered over `rect` (clamped to the canvas)
    /// and returns the rectangles used. Gap labels are skipped when they do not fit their
    /// gap or would overlap `avoiding`; event labels are always drawn.
    private func drawLabel(
        _ span: WaveSpan, over rect: CGRect, in context: inout GraphicsContext, width: CGFloat, avoiding: [CGRect]
    ) -> [CGRect] {
        let color: Color = span.warning ? .red : (span.kind == .gap ? .secondary : span.color)
        let title = context.resolve(Text(verbatim: span.title).font(.caption.weight(.semibold)).foregroundStyle(color))
        let titleSize = title.measure(in: CGSize(width: 400, height: 40))
        func placed(_ size: CGSize, y: CGFloat) -> CGRect {
            let centerX = min(max(rect.midX, size.width / 2), width - size.width / 2)
            return CGRect(x: centerX - size.width / 2, y: y, width: size.width, height: size.height)
        }
        func blocked(_ frame: CGRect) -> Bool {
            span.kind == .gap && (frame.width > rect.width + 8 || avoiding.contains { $0.insetBy(dx: -3, dy: 0).intersects(frame) })
        }
        var used: [CGRect] = []
        let titleFrame = placed(titleSize, y: 1)
        guard !blocked(titleFrame) else { return [] }
        context.draw(title, at: CGPoint(x: titleFrame.midX, y: titleFrame.minY), anchor: .top)
        used.append(titleFrame)
        if let detail = span.detail {
            let text = context.resolve(
                Text(verbatim: detail).font(.caption2.monospacedDigit())
                    .foregroundStyle(span.warning ? Color.red : Color.secondary))
            let detailFrame = placed(text.measure(in: CGSize(width: 400, height: 40)), y: 16)
            if !blocked(detailFrame) {
                context.draw(text, at: CGPoint(x: detailFrame.midX, y: detailFrame.minY), anchor: .top)
                used.append(detailFrame)
            }
        }
        return used
    }

    private func drawAxis(in context: inout GraphicsContext, wave: CGRect, width: CGFloat, samples: Int) {
        let totalMs = Double(samples) / Double(Fmt.samplesPerMs)
        guard totalMs > 0 else { return }
        let maxTicks = max(2.0, Double(width / 80))
        let steps = [5, 10, 20, 25, 50, 100, 200, 250, 500, 1_000, 2_000, 5_000, 10_000]
        let step = steps.first { totalMs / Double($0) <= maxTicks } ?? 20_000
        var tick = 0
        while Double(tick) <= totalMs + 0.0001 {
            let tx = CGFloat(Double(tick) / totalMs) * width
            var mark = Path()
            mark.move(to: CGPoint(x: tx, y: wave.maxY))
            mark.addLine(to: CGPoint(x: tx, y: wave.maxY + 4))
            context.stroke(mark, with: .color(Color.secondary.opacity(0.6)), lineWidth: 1)
            let label = context.resolve(
                Text(tick == 0 ? "0" : "\(tick) ms").font(.caption2.monospacedDigit()).foregroundStyle(.secondary))
            let anchor: UnitPoint = tick == 0 ? .topLeading : (tx > width - 30 ? .topTrailing : .top)
            context.draw(label, at: CGPoint(x: tx, y: wave.maxY + 6), anchor: anchor)
            tick += step
        }
    }
}

/// A waveform for a clip, with a moving playhead while that clip plays (the player must
/// still be playing: a sound cut off by a device change shows no playhead). The playhead
/// follows what is heard: the player's position less the output latency.
struct ClipWaveform: View {
    @Environment(AppModel.self) private var app
    let clip: AudioClip?
    let sampleCount: Int
    var spans: [WaveSpan] = []
    var tint: Color = SpanPalette.waveform
    var height: CGFloat = 210

    var body: some View {
        if let clip, let nowPlaying = app.nowPlaying, nowPlaying.clipID == clip.id, app.player.isPlaying {
            TimelineView(.animation) { _ in
                let position = app.player.playbackPosition ?? 0
                WaveformView(
                    waveform: clip.waveform, sampleCount: sampleCount, spans: spans,
                    playhead: position / max(nowPlaying.duration, 0.001), tint: tint, height: height)
            }
        } else {
            WaveformView(waveform: clip?.waveform, sampleCount: sampleCount, spans: spans, tint: tint, height: height)
        }
    }
}

enum SpanPalette {
    static let waveform: Color = .primary.opacity(0.78)
    static let events: [Color] = [.blue, .teal, .indigo]
    static let action: Color = .blue
    static let referent: Color = .purple
}
