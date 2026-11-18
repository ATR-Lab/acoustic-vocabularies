using UnityEngine;
using UnityEngine.UI;

namespace AcousticVocab.Theme
{
    // A small status pill with an optional leading icon, e.g. a check and
    // "Correct", or a speaker and "Heard once". The caller decides when it is
    // shown; the chip never derives state from study data.
    [DisallowMultipleComponent]
    public sealed class StoryStatusChip : MonoBehaviour
    {
        public Text Label { get; private set; }
        public Image Surface { get; private set; }
        public RawImage IconImage { get; private set; }
        public bool Visible => gameObject.activeSelf;

        public static StoryStatusChip Create(Transform parent, string name, Vector2 position, Vector2 size, Font font, int fontSize, string text, StoryIcon icon, bool lit = true)
        {
            var rect = StoryUi.Rect(parent, name, position, size);
            var chip = rect.gameObject.AddComponent<StoryStatusChip>();
            chip.Surface = StoryUi.Surface(rect, "Chip", Vector2.zero, size, lit ? StudyTheme.SurfaceSelected : StudyTheme.SurfaceEnabled, lit);
            float iconSize = Mathf.Min(size.y - 12, 28);
            bool hasIcon = icon != StoryIcon.None;
            // With an icon, the text rect starts after it so the two never overlap.
            float textLeft = hasIcon ? iconSize + 14 : 0;
            chip.IconImage = StoryUi.Icon(rect, "Icon", icon, new Vector2(-size.x / 2 + 8 + iconSize / 2, 0), iconSize, StudyTheme.TextPrimary);
            chip.Label = StoryUi.PlainText(rect, "Text", new Vector2(textLeft / 2, 0), new Vector2(size.x - textLeft - 8, size.y), font, fontSize, text);
            return chip;
        }

        public void Show(bool visible) => gameObject.SetActive(visible);

        public void Set(string text, StoryIcon icon)
        {
            Label.text = text ?? "";
            StoryUi.SetIcon(IconImage, icon);
        }
    }
}
