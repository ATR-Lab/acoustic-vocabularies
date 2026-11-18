using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;

namespace AcousticVocab.Theme
{
    // A row of step chips joined by short connectors, e.g. "Listen", "Your turn",
    // "Check". Steps before the current one show a check mark, the current one is
    // lit (selected surface and lit edge), later ones are idle. Every chip label
    // sits on its own opaque surface. Current = -1 shows no lit step.
    [DisallowMultipleComponent]
    public sealed class StoryStepChips : MonoBehaviour
    {
        public const float ChipHeight = 44f, Gap = 28f, IconSize = 24f;
        readonly List<Text> labels = new List<Text>();
        readonly List<Image> surfaces = new List<Image>();
        readonly List<RawImage> checks = new List<RawImage>();
        readonly List<Image> connectors = new List<Image>();
        public IReadOnlyList<Text> Labels => labels;
        public IReadOnlyList<Image> Surfaces => surfaces;
        public IReadOnlyList<RawImage> Checks => checks;
        public int Current { get; private set; } = -1;

        public static StoryStepChips Create(Transform parent, string name, Vector2 position, Font font, int fontSize, float chipWidth, params string[] steps)
        {
            float total = steps.Length * chipWidth + (steps.Length - 1) * Gap;
            var rect = StoryUi.Rect(parent, name, position, new Vector2(total, ChipHeight));
            var row = rect.gameObject.AddComponent<StoryStepChips>();
            for (int i = 0; i < steps.Length; i++)
            {
                float x = -total / 2 + chipWidth / 2 + i * (chipWidth + Gap);
                if (i > 0) row.connectors.Add(StoryUi.Block(rect, "Connector " + i, new Vector2(x - chipWidth / 2 - Gap / 2, 0), new Vector2(Gap - 6, 2), StudyTheme.EdgeIdle));
                var label = StoryUi.Label(rect, "Step " + (i + 1), new Vector2(x, 0), new Vector2(chipWidth, ChipHeight), font, fontSize, steps[i], StudyTheme.PanelFill, false);
                row.labels.Add(label);
                row.surfaces.Add(rect.Find("Step " + (i + 1) + " surface").GetComponent<Image>());
                // The check sits inside the chip's left padding, clear of centred text.
                row.checks.Add(StoryUi.Icon(rect, "Step " + (i + 1) + " done", StoryIcon.Check, new Vector2(x - chipWidth / 2 + IconSize * .5f + 8, 0), IconSize, StudyTheme.EdgeLit));
            }
            row.Set(-1);
            return row;
        }

        public void Set(int current)
        {
            Current = current;
            for (int i = 0; i < labels.Count; i++)
            {
                bool lit = i == current, done = current >= 0 && i < current;
                StoryUi.Style(surfaces[i], lit ? StudyTheme.SurfaceSelected : done ? StudyTheme.SurfaceEnabled : StudyTheme.PanelFill, lit);
                checks[i].enabled = done;
            }
            for (int i = 0; i < connectors.Count; i++) connectors[i].color = current > i ? StudyTheme.EdgeLit : StudyTheme.EdgeIdle;
        }
    }
}
