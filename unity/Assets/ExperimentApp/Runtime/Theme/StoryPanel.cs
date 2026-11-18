using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;

namespace AcousticVocab.Theme
{
    // The world-locked glass card behind a participant view: an opaque navy
    // body with rounded corners, a teal hairline edge and three soft glow layers
    // outside it. It is created as the first sibling so every existing text
    // backing, label and image renders on top of it. It never moves or resizes
    // another element and never intercepts input.
    [DisallowMultipleComponent]
    public sealed class StoryPanel : MonoBehaviour
    {
        readonly List<Image> glow = new List<Image>();
        public Image Body { get; private set; }
        public IReadOnlyList<Image> Glow => glow;
        public RectTransform Rect => (RectTransform)transform;
        public bool Lit { get; private set; }

        public static StoryPanel Create(Transform parent, string name, Vector2 position, Vector2 size)
        {
            var rect = StoryUi.Rect(parent, name, position, size);
            var panel = rect.gameObject.AddComponent<StoryPanel>();
            for (int i = StoryUi.GlowLayers.Length - 1; i >= 0; i--)
            {
                var (offset, _) = StoryUi.GlowLayers[i];
                var layer = StoryUi.Block(rect, "Glow " + (i + 1), Vector2.zero, size + 2 * offset * Vector2.one, Color.clear);
                layer.sprite = StoryUi.GlowSprite; layer.type = Image.Type.Sliced;
                layer.pixelsPerUnitMultiplier = 24f / Mathf.Max(offset * 2f, 1f);
                panel.glow.Insert(0, layer);
            }
            panel.Body = StoryUi.Surface(rect, "Glass", Vector2.zero, size, StudyTheme.PanelGlass, false);
            rect.SetAsFirstSibling();
            panel.SetLit(false);
            return panel;
        }

        // A lit panel gets the brighter edge and a stronger glow; colours only.
        public void SetLit(bool lit)
        {
            Lit = lit;
            StoryUi.Style(Body, StudyTheme.PanelGlass, lit);
            for (int i = 0; i < glow.Count; i++)
            {
                var c = StudyTheme.GlowSoft;
                c.a *= StoryUi.GlowLayers[i].Alpha * (lit ? 1.6f : 1f);
                glow[i].color = c;
            }
        }
    }
}
