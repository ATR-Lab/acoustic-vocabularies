using UnityEngine;
using UnityEngine.UI;

namespace AcousticVocab.Theme
{
    // A thin decorative progress bar: a rounded track and a teal fill whose width
    // is the given fraction. It shows progress the caller already knows (for
    // example cards seen); it owns no clock and never advances by itself.
    [DisallowMultipleComponent]
    public sealed class StoryProgressBar : MonoBehaviour
    {
        public Image TrackImage { get; private set; }
        public Image Fill { get; private set; }
        public float Fraction { get; private set; }
        float width;

        public static StoryProgressBar Create(Transform parent, string name, Vector2 position, Vector2 size)
        {
            var rect = StoryUi.Rect(parent, name, position, size);
            var bar = rect.gameObject.AddComponent<StoryProgressBar>();
            bar.width = size.x;
            bar.TrackImage = StoryUi.Block(rect, "Track", Vector2.zero, size, StudyTheme.Track);
            StoryUi.Round(bar.TrackImage);
            bar.Fill = StoryUi.Block(rect, "Fill", Vector2.zero, size, StudyTheme.WaveLit);
            StoryUi.Round(bar.Fill);
            var fill = bar.Fill.rectTransform;
            fill.anchorMin = fill.anchorMax = new Vector2(0, .5f); fill.pivot = new Vector2(0, .5f);
            fill.anchoredPosition = Vector2.zero;
            bar.Set(0);
            return bar;
        }

        public void Set(float fraction)
        {
            Fraction = Mathf.Clamp01(float.IsNaN(fraction) ? 0 : fraction);
            Fill.rectTransform.sizeDelta = new Vector2(width * Fraction, ((RectTransform)transform).sizeDelta.y);
            Fill.enabled = Fraction > 0;
        }
    }
}
