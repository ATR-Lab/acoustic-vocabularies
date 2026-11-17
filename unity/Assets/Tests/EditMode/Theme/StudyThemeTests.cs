using System.Linq;
using System.Reflection;
using AcousticVocab.Theme;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.Foundation.Tests
{
    public sealed class StudyThemeTests
    {
        static readonly (string Name, Color Value)[] TextSurfaces =
        {
            ("PanelFill", StudyTheme.PanelFill),
            ("SurfaceEnabled", StudyTheme.SurfaceEnabled),
            ("SurfaceDisabled", StudyTheme.SurfaceDisabled),
            ("SurfaceSelected", StudyTheme.SurfaceSelected),
            ("HighlightFill", StudyTheme.HighlightFill),
        };

        [Test]
        public void ContrastMatchesTheWcagDefinition()
        {
            Assert.That(StudyTheme.ContrastRatio(Color.white, Color.black), Is.EqualTo(21).Within(1e-9));
            Assert.That(StudyTheme.ContrastRatio(Color.black, Color.white), Is.EqualTo(21).Within(1e-9));
            Assert.That(StudyTheme.ContrastRatio(Color.white, Color.white), Is.EqualTo(1).Within(1e-9));
            // #777777 on white is the usual 4.48:1 reference value.
            Assert.That(StudyTheme.ContrastRatio(new Color32(0x77, 0x77, 0x77, 255), Color.white), Is.EqualTo(4.48).Within(0.01));
        }

        [Test]
        public void EveryTextSurfaceKeepsAaaContrastWithWhiteText()
        {
            Assert.That(StudyTheme.TextPrimary, Is.EqualTo(Color.white), "Text stays crisp white; the theme never dims it");
            foreach (var (name, value) in TextSurfaces)
                Assert.That(StudyTheme.ContrastRatio(StudyTheme.TextPrimary, value),
                    Is.GreaterThanOrEqualTo(StudyTheme.MinimumTextContrast), name);
        }

        [Test]
        public void EveryTextSurfaceIsOpaqueSoTheSceneBehindCannotChangeContrast()
        {
            foreach (var (name, value) in TextSurfaces)
                Assert.That(value.a, Is.EqualTo(1f), name);
        }

        [Test]
        public void SelectionAndHighlightStayDistinguishable()
        {
            double Luma(Color c) => StudyTheme.RelativeLuminance(c);
            double Ratio(Color a, Color b) => (System.Math.Max(Luma(a), Luma(b)) + .05) / (System.Math.Min(Luma(a), Luma(b)) + .05);
            // Previous engineering palette: selected/enabled ratio was about 1.75.
            Assert.That(Ratio(StudyTheme.SurfaceSelected, StudyTheme.SurfaceEnabled), Is.GreaterThanOrEqualTo(1.75));
            Assert.That(Ratio(StudyTheme.HighlightFill, StudyTheme.PanelFill), Is.GreaterThanOrEqualTo(1.5));
            // Non-text UI component contrast (WCAG 1.4.11) for the lit edge.
            Assert.That(StudyTheme.ContrastRatio(StudyTheme.EdgeLit, StudyTheme.PanelFill), Is.GreaterThanOrEqualTo(3));
            Assert.That(StudyTheme.ContrastRatio(StudyTheme.EdgeLit, StudyTheme.HighlightFill), Is.GreaterThanOrEqualTo(3));
            Assert.That(StudyTheme.EdgeFor(true), Is.EqualTo(StudyTheme.EdgeLit));
            Assert.That(StudyTheme.EdgeFor(false), Is.EqualTo(StudyTheme.EdgeIdle));
            Assert.That(StudyTheme.SurfaceFor(true, true), Is.EqualTo(StudyTheme.SurfaceSelected));
            Assert.That(StudyTheme.SurfaceFor(true, false), Is.EqualTo(StudyTheme.SurfaceEnabled));
            Assert.That(StudyTheme.SurfaceFor(false, false), Is.EqualTo(StudyTheme.SurfaceDisabled));
        }

        [Test]
        public void ThemeCarriesNoGeometryOrGlyphSize()
        {
            // Layout and label size belong to the reviewed panel/view geometry.
            var members = typeof(StudyTheme).GetMembers(BindingFlags.Public | BindingFlags.Static)
                .Select(m => m.Name.ToLowerInvariant()).ToArray();
            foreach (var forbidden in new[] { "size", "angle", "distance", "scale", "position", "font" })
                Assert.That(members.Where(m => m.Contains(forbidden) && m != "textstyle"), Is.Empty, forbidden);
            Assert.That(StudyTheme.TextStyle, Is.EqualTo(FontStyle.Normal));
            Assert.That(StudyTheme.EdgeWidthCanvasUnits, Is.InRange(0.5f, 2f), "Edge is a hairline drawn outside the rect");
        }
    }
}
