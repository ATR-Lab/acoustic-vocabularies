using System;
using System.Collections.Generic;
using UnityEngine;

namespace AcousticVocab.Theme
{
    public enum StoryIcon { None, Speaker, Tag, Box, Tray, Lock, Check, AddRing, RemoveRing, Card, Arrow, Scan }

    // Simple line icons drawn procedurally into small white alpha textures and
    // tinted by the RawImage colour. No image asset and no icon font is used.
    // Icons are decoration: every icon sits next to text that carries the meaning.
    public static class StoryIcons
    {
        public const int TexturePixels = 96;
        const float Stroke = .055f;
        static readonly Dictionary<StoryIcon, Texture2D> cache = new Dictionary<StoryIcon, Texture2D>();

        // Public action and target identifiers (ResponsePanel.PublicCommands) to
        // a matching glyph. Unknown identifiers get no icon.
        public static StoryIcon ForAction(string action) => action switch
        {
            "ADD_ONE" => StoryIcon.AddRing,
            "REMOVE_ONE" => StoryIcon.RemoveRing,
            "FLIP_CARD" => StoryIcon.Card,
            "ALIGN_ARROW" => StoryIcon.Arrow,
            "SCAN" => StoryIcon.Scan,
            "TAG" => StoryIcon.Tag,
            "CLOSE" => StoryIcon.Box,
            "QUARANTINE" => StoryIcon.Lock,
            _ => StoryIcon.None,
        };

        public static StoryIcon ForTarget(string target) => target switch
        {
            "A" or "B" or "C" or "D" => StoryIcon.Tray,
            "E" or "F" or "G" or "H" => StoryIcon.Box,
            _ => StoryIcon.None,
        };

        public static Texture2D Texture(StoryIcon icon)
        {
            if (icon == StoryIcon.None) return null;
            if (cache.TryGetValue(icon, out var existing) && existing != null) return existing;
            var texture = new Texture2D(TexturePixels, TexturePixels, TextureFormat.RGBA32, false)
            { name = "Story icon " + icon, wrapMode = TextureWrapMode.Clamp, filterMode = FilterMode.Bilinear, hideFlags = HideFlags.HideAndDontSave };
            var shape = Shape(icon);
            var pixels = new Color32[TexturePixels * TexturePixels];
            float pixel = 1f / TexturePixels;
            for (int y = 0; y < TexturePixels; y++)
                for (int x = 0; x < TexturePixels; x++)
                {
                    var p = new Vector2((x + .5f) * pixel, (y + .5f) * pixel);
                    float d = shape.Distance(p);
                    float a = Mathf.Clamp01((Stroke * .5f - d) / pixel + .5f);
                    if (shape.Fill != null && shape.Fill(p)) a = 1f;
                    pixels[y * TexturePixels + x] = new Color32(255, 255, 255, (byte)Mathf.RoundToInt(a * 255));
                }
            texture.SetPixels32(pixels);
            texture.Apply(false, true);
            cache[icon] = texture;
            return texture;
        }

        sealed class Glyph
        {
            readonly List<(Vector2 A, Vector2 B)> segments = new List<(Vector2, Vector2)>();
            readonly List<(Vector2 C, float R, float From, float To)> arcs = new List<(Vector2, float, float, float)>();
            public Func<Vector2, bool> Fill;
            public Glyph Line(params float[] xy)
            {
                for (int i = 0; i + 3 < xy.Length; i += 2) segments.Add((new Vector2(xy[i], xy[i + 1]), new Vector2(xy[i + 2], xy[i + 3])));
                return this;
            }
            public Glyph Box(float x0, float y0, float x1, float y1) => Line(x0, y0, x1, y0, x1, y1, x0, y1, x0, y0);
            public Glyph Arc(float cx, float cy, float r, float fromDeg = 0, float toDeg = 360)
            { arcs.Add((new Vector2(cx, cy), r, fromDeg, toDeg)); return this; }
            public float Distance(Vector2 p)
            {
                float best = float.MaxValue;
                foreach (var (a, b) in segments)
                {
                    var ab = b - a; float t = Mathf.Clamp01(Vector2.Dot(p - a, ab) / Mathf.Max(ab.sqrMagnitude, 1e-6f));
                    best = Mathf.Min(best, (a + ab * t - p).magnitude);
                }
                foreach (var (c, r, from, to) in arcs)
                {
                    var v = p - c; float angle = Mathf.Atan2(v.y, v.x) * Mathf.Rad2Deg;
                    if (angle < from) angle += 360;
                    if (angle >= from && angle <= to) best = Mathf.Min(best, Mathf.Abs(v.magnitude - r));
                    else
                    {
                        var e0 = c + r * new Vector2(Mathf.Cos(from * Mathf.Deg2Rad), Mathf.Sin(from * Mathf.Deg2Rad));
                        var e1 = c + r * new Vector2(Mathf.Cos(to * Mathf.Deg2Rad), Mathf.Sin(to * Mathf.Deg2Rad));
                        best = Mathf.Min(best, Mathf.Min((p - e0).magnitude, (p - e1).magnitude));
                    }
                }
                return best;
            }
        }

        static Glyph Shape(StoryIcon icon)
        {
            var g = new Glyph();
            switch (icon)
            {
                case StoryIcon.Speaker:
                    g.Line(.12f, .38f, .28f, .38f, .48f, .2f, .48f, .8f, .28f, .62f, .12f, .62f, .12f, .38f);
                    g.Arc(.5f, .5f, .17f, -50, 50).Arc(.5f, .5f, .32f, -50, 50); break;
                case StoryIcon.Tag:
                    g.Line(.12f, .5f, .38f, .8f, .86f, .8f, .86f, .2f, .38f, .2f, .12f, .5f).Arc(.4f, .5f, .06f); break;
                case StoryIcon.Box:
                    g.Box(.16f, .14f, .84f, .62f).Box(.1f, .62f, .9f, .8f).Line(.4f, .48f, .6f, .48f); break;
                case StoryIcon.Tray:
                    g.Line(.08f, .62f, .2f, .24f, .8f, .24f, .92f, .62f).Line(.3f, .44f, .7f, .44f); break;
                case StoryIcon.Lock:
                    g.Box(.24f, .12f, .76f, .54f).Arc(.5f, .54f, .17f, 0, 180).Line(.5f, .28f, .5f, .38f); break;
                case StoryIcon.Check:
                    g.Line(.18f, .5f, .4f, .27f, .82f, .73f); break;
                case StoryIcon.AddRing:
                    g.Arc(.36f, .5f, .2f).Line(.66f, .5f, .9f, .5f).Line(.78f, .38f, .78f, .62f); break;
                case StoryIcon.RemoveRing:
                    g.Arc(.36f, .5f, .2f).Line(.66f, .5f, .9f, .5f); break;
                case StoryIcon.Card:
                    g.Box(.28f, .14f, .72f, .86f).Line(.38f, .66f, .62f, .66f).Line(.38f, .5f, .62f, .5f); break;
                case StoryIcon.Arrow:
                    g.Line(.12f, .5f, .86f, .5f).Line(.64f, .7f, .86f, .5f, .64f, .3f); break;
                case StoryIcon.Scan:
                    g.Line(.12f, .3f, .12f, .12f, .3f, .12f).Line(.7f, .12f, .88f, .12f, .88f, .3f)
                     .Line(.88f, .7f, .88f, .88f, .7f, .88f).Line(.3f, .88f, .12f, .88f, .12f, .7f).Line(.22f, .5f, .78f, .5f); break;
            }
            return g;
        }
    }
}
