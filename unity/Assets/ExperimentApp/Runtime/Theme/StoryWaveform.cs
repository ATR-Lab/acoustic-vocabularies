using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;

namespace AcousticVocab.Theme
{
    // A decorative waveform strip: vertical bars whose heights come from a fixed
    // seed, never from study audio. An optional playhead (a thin bright line)
    // sits at a caller-given fraction; bars left of it are lit. With no fraction
    // the playhead is hidden and every bar is idle. The strip owns no clock.
    [DisallowMultipleComponent]
    public sealed class StoryWaveform : MonoBehaviour
    {
        public const float PlayheadWidth = 3f;
        readonly List<Image> bars = new List<Image>();
        readonly List<float> barX = new List<float>();
        public IReadOnlyList<Image> Bars => bars;
        public Image Playhead { get; private set; }
        public float? Fraction { get; private set; }
        public bool PlayheadVisible => Playhead.enabled;
        float width;

        public static StoryWaveform Create(Transform parent, string name, Vector2 position, Vector2 size, int seed, int count = 40)
        {
            var rect = StoryUi.Rect(parent, name, position, size);
            var strip = rect.gameObject.AddComponent<StoryWaveform>();
            strip.width = size.x;
            float pitch = size.x / count, barWidth = Mathf.Max(1.5f, pitch * .45f);
            uint state = (uint)seed * 2654435761u + 1013904223u;
            for (int i = 0; i < count; i++)
            {
                state = state * 1664525u + 1013904223u;
                float noise = (state >> 8) / 16777216f;
                // A soft envelope keeps the edges quiet, like a short motif.
                float t = (i + .5f) / count, envelope = Mathf.Sin(Mathf.PI * t);
                float h = Mathf.Max(3f, size.y * (.15f + .85f * envelope * (.35f + .65f * noise)));
                float x = -size.x / 2 + (i + .5f) * pitch;
                strip.bars.Add(StoryUi.Block(rect, "Bar " + (i + 1), new Vector2(x, 0), new Vector2(barWidth, h), StudyTheme.WaveIdle));
                strip.barX.Add(t);
            }
            strip.Playhead = StoryUi.Block(rect, "Playhead", Vector2.zero, new Vector2(PlayheadWidth, size.y + 10), StudyTheme.WaveLit);
            strip.SetPlayhead(null);
            return strip;
        }

        public void SetPlayhead(float? fraction)
        {
            if (fraction.HasValue && float.IsNaN(fraction.Value)) fraction = null;
            Fraction = fraction.HasValue ? Mathf.Clamp01(fraction.Value) : (float?)null;
            Playhead.enabled = Fraction.HasValue;
            if (Fraction.HasValue) Playhead.rectTransform.anchoredPosition = new Vector2(-width / 2 + width * Fraction.Value, 0);
            for (int i = 0; i < bars.Count; i++)
                bars[i].color = Fraction.HasValue && barX[i] <= Fraction.Value ? StudyTheme.WaveLit : StudyTheme.WaveIdle;
        }
    }
}
