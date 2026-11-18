using System;
using AcousticVocab.Foundation;
using AcousticVocab.Theme;
using UnityEngine;
using UnityEngine.UI;

namespace AcousticVocab.ResponsePanel
{
    // Storyboard presentation of the response panel: the shared glass card
    // (StoryPanel) behind the keys, a teal hairline edge behind each key and a
    // constant "Heard once" status chip in test trials. Presentation only. Nothing here creates, moves or
    // resizes a key, label, role label or collider, adds an input surface or
    // reads the request's command, the selection or the outcome.
    public sealed class ResponsePanelStyle
    {
        public const string HeardOnceText = "Heard once";
        // Edge width in metres: the theme hairline on a 0.001-scaled canvas.
        public const float EdgeMetres = StudyTheme.EdgeWidthCanvasUnits * .001f;
        const float CanvasScale = .001f;

        public GameObject Backplate { get; }
        public GameObject Chip { get; }
        public Text ChipText { get; }
        public Image ChipBacking { get; }

        // The chip depends on the panel mode only, never on the request or the
        // response, so every trial of a test block shows the same chip.
        public static bool ShowsHeardOnce(PanelMode mode) => mode == PanelMode.FullMessage || mode == PanelMode.AtomicProbe;

        public static MeshRenderer AddKeyEdge(Transform keyRoot, Vector3 boxScale, Func<Color, Material> material)
        {
            // Thin plate inside the key box except for a rim one edge wide;
            // its front face sits behind the key face, so only the rim shows.
            var plate = Plate(keyRoot, "Key edge", Vector3.zero, new Vector3(boxScale.x + 2 * EdgeMetres, boxScale.y + 2 * EdgeMetres, boxScale.z * .8f));
            var renderer = plate.GetComponent<MeshRenderer>(); renderer.sharedMaterial = material(StudyTheme.EdgeIdle); return renderer;
        }

        internal ResponsePanelStyle(Transform panel, PanelSettings settings, Font font, Vector3 observer)
        {
            float w = settings.ButtonWidth, h = settings.ButtonHeight, g = settings.Gap, row = h + g;
            // Glass card from the role-label row down to the Commit row, behind every key.
            float top = row + h / 2 + 2 * g, bottom = -3 * row - h / 2 - 2 * g, width = 4 * w + 5 * g;
            Backplate = WorldCanvas(panel, "Panel glass", new Vector3(0, (top + bottom) / 2, .012f), new Vector2(width, top - bottom) / CanvasScale);
            StoryPanel.Create(Backplate.transform, "Glass", Vector2.zero, new Vector2(width, top - bottom) / CanvasScale);

            // Chip above the role-label row, capitals at the configured visual angle.
            Chip = WorldCanvas(panel, "Heard once chip", new Vector3(0, 2 * row, -.012f), Vector2.zero);
            float distance = Vector3.Distance(observer, Chip.transform.position);
            float ink = 2 * distance * Mathf.Tan(settings.TextAngleDegrees * Mathf.Deg2Rad / 2) / CanvasScale;
            int fontSize = ChipFontSize(font, ink, out float advance);
            float height = Mathf.Max(3.2f * ink, 30), icon = Mathf.Min(height - 12, 28);
            var size = new Vector2(advance + icon + 22 + 2 * ink, height);
            ((RectTransform)Chip.transform).sizeDelta = size;
            var chip = StoryStatusChip.Create(Chip.transform, "Heard once", Vector2.zero, size, font, fontSize, HeardOnceText, StoryIcon.Speaker, false);
            ChipText = chip.Label; ChipBacking = chip.Surface;
            // The label is never wrapped or cut, whatever the configured angle.
            ChipText.horizontalOverflow = HorizontalWrapMode.Overflow; ChipText.verticalOverflow = VerticalWrapMode.Overflow;
            Chip.SetActive(false);
        }

        static GameObject WorldCanvas(Transform panel, string name, Vector3 at, Vector2 size)
        {
            var node = new GameObject(name, typeof(RectTransform), typeof(Canvas));
            node.transform.SetParent(panel, false); node.transform.localPosition = at; node.transform.localScale = Vector3.one * CanvasScale;
            node.GetComponent<Canvas>().renderMode = RenderMode.WorldSpace; ((RectTransform)node.transform).sizeDelta = size;
            return node;
        }

        public void Show(PanelMode mode) => Chip.SetActive(ShowsHeardOnce(mode));

        // Font size whose capital height is at least the configured ink height.
        // Glyph metrics of a dynamic font at size N are in canvas units on a
        // world-space canvas without a scaler (one font pixel per canvas unit).
        static int ChipFontSize(Font font, float need, out float advanceUnits)
        {
            const int probe = 100;
            int size = Mathf.Max(1, Mathf.CeilToInt(probe * need / CapHeight(font, probe)));
            // Glyph metrics are rounded per size; step up until the capital fits.
            while (CapHeight(font, size) < need) if (++size > 1000) throw new ConfigurationFault("panel_chip_glyph_unavailable");
            int advance = 0;
            foreach (char c in HeardOnceText)
            {
                if (!font.GetCharacterInfo(c, out CharacterInfo info, size, StudyTheme.TextStyle)) throw new ConfigurationFault("panel_chip_glyph_unavailable");
                advance += info.advance;
            }
            advanceUnits = advance;
            return size;
        }

        static int CapHeight(Font font, int size)
        {
            font.RequestCharactersInTexture("H" + HeardOnceText, size, StudyTheme.TextStyle);
            if (!font.GetCharacterInfo('H', out CharacterInfo cap, size, StudyTheme.TextStyle) || cap.maxY <= cap.minY) throw new ConfigurationFault("panel_chip_glyph_unavailable");
            return cap.maxY - cap.minY;
        }

        static GameObject Plate(Transform parent, string name, Vector3 position, Vector3 scale)
        {
            var plate = GameObject.CreatePrimitive(PrimitiveType.Cube); plate.name = name;
            var collider = plate.GetComponent<Collider>(); if (Application.isPlaying) UnityEngine.Object.Destroy(collider); else UnityEngine.Object.DestroyImmediate(collider);
            plate.transform.SetParent(parent, false); plate.transform.localPosition = position; plate.transform.localScale = scale;
            return plate;
        }
    }
}
