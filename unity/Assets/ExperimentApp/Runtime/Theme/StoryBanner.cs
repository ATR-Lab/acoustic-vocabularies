using UnityEngine;
using UnityEngine.UI;

namespace AcousticVocab.Theme
{
    // A one-line banner on an opaque surface with an optional lock badge on
    // its left, e.g. an honest "assigned" notice on a read-only view.
    [DisallowMultipleComponent]
    public sealed class StoryBanner : MonoBehaviour
    {
        public Text Label { get; private set; }
        public Image Surface { get; private set; }
        public RawImage LockBadge { get; private set; }
        public bool Locked => LockBadge.enabled;

        public static StoryBanner Create(Transform parent, string name, Vector2 position, Vector2 size, Font font, int fontSize, string text, bool locked)
        {
            var rect = StoryUi.Rect(parent, name, position, size);
            var banner = rect.gameObject.AddComponent<StoryBanner>();
            banner.Surface = StoryUi.Surface(rect, "Banner", Vector2.zero, size, StudyTheme.PanelFill, false);
            float badge = Mathf.Min(size.y - 12, 28), inset = badge + 20;
            banner.LockBadge = StoryUi.Icon(rect, "Lock", StoryIcon.Lock, new Vector2(-size.x / 2 + 10 + badge / 2, 0), badge, StudyTheme.EdgeLit);
            // The text rect leaves room for the badge on both sides so it stays centred.
            banner.Label = StoryUi.PlainText(rect, "Text", Vector2.zero, new Vector2(size.x - 2 * inset, size.y), font, fontSize, text);
            banner.Set(text, locked);
            return banner;
        }

        public void Set(string text, bool locked)
        {
            Label.text = text ?? "";
            LockBadge.enabled = locked;
        }
    }
}
