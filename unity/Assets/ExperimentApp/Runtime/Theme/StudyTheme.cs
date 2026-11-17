using System;
using UnityEngine;

namespace AcousticVocab.Theme
{
    // Visual tokens for participant-facing panels: deep navy glass, a soft teal
    // edge, white sans-serif text and teal selection. Colours only. Geometry,
    // glyph size, font and timing stay with each view's reviewed configuration,
    // so applying the theme never moves, resizes or rescales anything.
    // Every surface that carries text is opaque: the workcell behind a panel
    // must never change text contrast.
    public static class StudyTheme
    {
        // Panel and text backing fill (navy glass, opaque behind text).
        public static readonly Color PanelFill = Srgb(0x0D, 0x22, 0x33);
        // Soft teal edge on an idle panel or button.
        public static readonly Color EdgeIdle = Srgb(0x2A, 0x8C, 0x86);
        // Brighter teal edge on a selected or lit element.
        public static readonly Color EdgeLit = Srgb(0x3D, 0xD9, 0xC5);
        // Buttons and cards.
        public static readonly Color SurfaceEnabled = Srgb(0x13, 0x32, 0x47);
        public static readonly Color SurfaceDisabled = Srgb(0x0A, 0x18, 0x24);
        public static readonly Color SurfaceSelected = Srgb(0x0B, 0x61, 0x58);
        // Fill behind a label while it is lit in an aligned lesson.
        public static readonly Color HighlightFill = Srgb(0x08, 0x47, 0x3F);
        // Pointer ray or tip.
        public static readonly Color Pointer = EdgeLit;

        // Type tokens. Text colour and style only; sizes come from the reviewed
        // panel and view geometry (#49/#74), never from the theme.
        public static readonly Color TextPrimary = Color.white;
        public const FontStyle TextStyle = FontStyle.Normal;

        // Visual edge drawn outside a UI backing, in canvas units (1 unit = 1 mm
        // on the 0.001-scaled world canvases). It never changes a RectTransform.
        public const float EdgeWidthCanvasUnits = 1.5f;

        // Minimum text contrast for every text-bearing surface (WCAG 2.x AAA).
        public const double MinimumTextContrast = 7.0;

        public static Color SurfaceFor(bool enabled, bool selected) =>
            selected ? SurfaceSelected : enabled ? SurfaceEnabled : SurfaceDisabled;

        public static Color EdgeFor(bool lit) => lit ? EdgeLit : EdgeIdle;

        // WCAG 2.x relative luminance of an sRGB colour (alpha ignored).
        public static double RelativeLuminance(Color c) =>
            0.2126 * Linear(c.r) + 0.7152 * Linear(c.g) + 0.0722 * Linear(c.b);

        public static double ContrastRatio(Color a, Color b)
        {
            double la = RelativeLuminance(a), lb = RelativeLuminance(b);
            return (Math.Max(la, lb) + 0.05) / (Math.Min(la, lb) + 0.05);
        }

        static double Linear(float channel)
        {
            double c = channel;
            return c <= 0.04045 ? c / 12.92 : Math.Pow((c + 0.055) / 1.055, 2.4);
        }

        static Color Srgb(byte r, byte g, byte b) => new Color32(r, g, b, 255);
    }
}
