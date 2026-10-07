using System;
using UnityEngine;

namespace AcousticVocab.ResponsePanel
{
    public static class PanelTypography
    {
        // Legacy TextMesh renderer bounds include line spacing. Use the requested
        // glyph image extents, retaining TextMesh's measured advance-to-world scale.
        public static float LocalInkHeight(TextMesh text)
        {
            text.font.RequestCharactersInTexture(text.text, text.fontSize, text.fontStyle);
            int min = int.MaxValue, max = int.MinValue, advance = 0;
            foreach (char c in text.text)
            {
                if (!text.font.GetCharacterInfo(c, out CharacterInfo info, text.fontSize, text.fontStyle)) throw new InvalidOperationException("Requested glyph unavailable");
                advance += info.advance;
                if (!char.IsWhiteSpace(c)) { min = Math.Min(min, info.minY); max = Math.Max(max, info.maxY); }
            }
            if (advance <= 0 || max <= min) throw new InvalidOperationException("Visible glyph bounds unavailable");
            return text.GetComponent<MeshRenderer>().localBounds.size.x * (max - min) / advance;
        }
    }
}
