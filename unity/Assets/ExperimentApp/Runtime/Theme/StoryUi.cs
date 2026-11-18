using UnityEngine;
using UnityEngine.UI;

namespace AcousticVocab.Theme
{
    // Shared uGUI building blocks for the storyboard panel layer. Everything is
    // created in code on the existing world-space canvases (1 canvas unit = 1 mm
    // at the 0.001 canvas scale). Nothing here intercepts input, schedules work
    // or reads study content: callers pass text and state in.
    public static class StoryUi
    {
        // Rounded corner radius of panels, chips and backings, in canvas units.
        public const float CornerRadius = 10f;
        // Outer glow layers around a StoryPanel: offset in canvas units and alpha scale.
        public static readonly (float Offset, float Alpha)[] GlowLayers = { (3f, .55f), (7f, .28f), (12f, .12f) };

        const int SpritePixels = 64, SpriteBorder = 24;
        static Sprite rounded, glow;

        // Solid rounded rectangle, nine-sliced so its corners keep their radius.
        public static Sprite RoundedSprite => rounded != null ? rounded : rounded = MakeSprite("Story rounded", false);
        // Soft-edged rounded rectangle for the glow layers.
        public static Sprite GlowSprite => glow != null ? glow : glow = MakeSprite("Story glow", true);

        public static RectTransform Rect(Transform parent, string name, Vector2 position, Vector2 size)
        {
            var node = new GameObject(name, typeof(RectTransform));
            node.transform.SetParent(parent, false);
            var rect = (RectTransform)node.transform;
            rect.anchoredPosition = position; rect.sizeDelta = size;
            return rect;
        }

        // An opaque rounded surface with the theme edge (StudyTheme tokens).
        public static Image Surface(Transform parent, string name, Vector2 position, Vector2 size, Color fill, bool lit)
        {
            var rect = Rect(parent, name, position, size);
            var image = rect.gameObject.AddComponent<Image>();
            image.raycastTarget = false;
            Round(image);
            Style(image, fill, lit);
            return image;
        }

        // Colour-only styling: opaque fill plus a hairline edge outside the rect.
        public static void Style(Image image, Color fill, bool lit)
        {
            fill.a = 1f; image.color = fill;
            var edge = image.GetComponent<Outline>();
            if (edge == null) edge = image.gameObject.AddComponent<Outline>();
            edge.effectColor = StudyTheme.EdgeFor(lit);
            edge.effectDistance = new Vector2(StudyTheme.EdgeWidthCanvasUnits, -StudyTheme.EdgeWidthCanvasUnits);
            edge.useGraphicAlpha = false;
        }

        // Gives an Image rounded corners. Changes only the sprite, never its rect.
        public static void Round(Image image)
        {
            image.sprite = RoundedSprite; image.type = Image.Type.Sliced;
            image.pixelsPerUnitMultiplier = SpriteBorder / CornerRadius;
        }

        // White text over an opaque surface. The surface is the label's own
        // backing (same rect, rendered first), so contrast never depends on the scene.
        public static Text Label(Transform parent, string name, Vector2 position, Vector2 size, Font font, int fontSize, string value, Color surface, bool lit)
        {
            Surface(parent, name + " backing", position, size, surface, lit);
            return PlainText(parent, name, position, size, font, fontSize, value);
        }

        // Bare text. Callers must place it on an opaque surface.
        public static Text PlainText(Transform parent, string name, Vector2 position, Vector2 size, Font font, int fontSize, string value)
        {
            var rect = Rect(parent, name, position, size);
            var text = rect.gameObject.AddComponent<Text>();
            text.font = font; text.fontSize = fontSize; text.alignment = TextAnchor.MiddleCenter;
            text.color = StudyTheme.TextPrimary; text.fontStyle = StudyTheme.TextStyle;
            text.supportRichText = false; text.raycastTarget = false;
            text.horizontalOverflow = HorizontalWrapMode.Wrap; text.verticalOverflow = VerticalWrapMode.Truncate;
            text.text = value ?? "";
            return text;
        }

        public static RawImage Icon(Transform parent, string name, StoryIcon icon, Vector2 position, float size, Color tint)
        {
            var rect = Rect(parent, name, position, new Vector2(size, size));
            var image = rect.gameObject.AddComponent<RawImage>();
            image.raycastTarget = false; image.texture = StoryIcons.Texture(icon); image.color = tint;
            image.enabled = icon != StoryIcon.None;
            return image;
        }

        public static void SetIcon(RawImage image, StoryIcon icon)
        {
            image.texture = StoryIcons.Texture(icon);
            image.enabled = icon != StoryIcon.None;
        }

        // A plain decorative rectangle (bars, tracks, playheads). Not a text surface.
        public static Image Block(Transform parent, string name, Vector2 position, Vector2 size, Color color)
        {
            var rect = Rect(parent, name, position, size);
            var image = rect.gameObject.AddComponent<Image>();
            image.raycastTarget = false; image.color = color;
            return image;
        }

        static Sprite MakeSprite(string name, bool soft)
        {
            var texture = new Texture2D(SpritePixels, SpritePixels, TextureFormat.RGBA32, false)
            { name = name, wrapMode = TextureWrapMode.Clamp, filterMode = FilterMode.Bilinear, hideFlags = HideFlags.HideAndDontSave };
            var pixels = new Color32[SpritePixels * SpritePixels];
            float radius = SpriteBorder, half = SpritePixels * .5f;
            for (int y = 0; y < SpritePixels; y++)
                for (int x = 0; x < SpritePixels; x++)
                {
                    // Signed distance to a rounded square filling the texture.
                    float px = Mathf.Abs(x + .5f - half) - (half - radius), py = Mathf.Abs(y + .5f - half) - (half - radius);
                    float outside = new Vector2(Mathf.Max(px, 0), Mathf.Max(py, 0)).magnitude + Mathf.Min(Mathf.Max(px, py), 0) - radius;
                    float a = soft ? Mathf.Clamp01(-outside / radius) : Mathf.Clamp01(.5f - outside);
                    if (soft) a *= a;
                    pixels[y * SpritePixels + x] = new Color32(255, 255, 255, (byte)Mathf.RoundToInt(a * 255));
                }
            texture.SetPixels32(pixels); texture.Apply(false, true);
            var sprite = Sprite.Create(texture, new Rect(0, 0, SpritePixels, SpritePixels), new Vector2(.5f, .5f), 100, 0,
                SpriteMeshType.FullRect, new Vector4(SpriteBorder, SpriteBorder, SpriteBorder, SpriteBorder));
            sprite.name = name; sprite.hideFlags = HideFlags.HideAndDontSave;
            return sprite;
        }
    }
}
