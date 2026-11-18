using System.Linq;
using AcousticVocab.ResponsePanel;
using AcousticVocab.Theme;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.UI;

namespace AcousticVocab.StoryPanels.Tests
{
    public sealed class StoryComponentTests
    {
        GameObject root;
        Font font;

        [SetUp]
        public void SetUp()
        {
            root = new GameObject("Story canvas", typeof(RectTransform), typeof(Canvas));
            root.GetComponent<Canvas>().renderMode = RenderMode.WorldSpace;
            root.transform.localScale = Vector3.one * .001f;
            font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
        }

        [TearDown]
        public void TearDown() => Object.DestroyImmediate(root);

        internal static void AssertTextOnOpaqueAaaSurface(Text text, Image surface)
        {
            Assert.That(text.color, Is.EqualTo(Color.white), text.name);
            Assert.That(text.fontStyle, Is.EqualTo(FontStyle.Normal), text.name);
            Assert.That(surface.color.a, Is.EqualTo(1f), surface.name + " must be opaque");
            Assert.That(StudyTheme.ContrastRatio(text.color, surface.color), Is.GreaterThanOrEqualTo(StudyTheme.MinimumTextContrast), text.name);
        }

        internal static Rect Bounds(RectTransform rect)
        {
            var size = rect.sizeDelta; var p = rect.anchoredPosition;
            return new Rect(p.x - size.x * rect.pivot.x, p.y - size.y * rect.pivot.y, size.x, size.y);
        }

        [Test, Category("REQ_APP_07")]
        public void NewStoryTokensKeepTheThemeContrastRules()
        {
            Assert.That(StudyTheme.PanelGlass.a, Is.EqualTo(1f));
            Assert.That(StudyTheme.ContrastRatio(StudyTheme.TextPrimary, StudyTheme.PanelGlass), Is.GreaterThanOrEqualTo(StudyTheme.MinimumTextContrast));
            Assert.That(StudyTheme.GlowSoft.a, Is.LessThan(1f), "Glow is decorative and never sits behind text");
            // Non-text graphics (WCAG 1.4.11): a lit bar or playhead must stand out from the glass.
            Assert.That(StudyTheme.ContrastRatio(StudyTheme.WaveLit, StudyTheme.PanelGlass), Is.GreaterThanOrEqualTo(3));
            Assert.That(StudyTheme.ContrastRatio(StudyTheme.WaveLit, StudyTheme.WaveIdle), Is.GreaterThanOrEqualTo(1.5));
        }

        [Test, Category("REQ_APP_07")]
        public void PanelIsAnOpaqueFirstSiblingWithGlowOutsideAndNoInput()
        {
            var existing = StoryUi.Rect(root.transform, "Existing label", new Vector2(0, 50), new Vector2(100, 40));
            var panel = StoryPanel.Create(root.transform, "Card", Vector2.zero, new Vector2(600, 400));
            Assert.That(panel.transform.GetSiblingIndex(), Is.EqualTo(0), "Panel renders behind every existing element");
            Assert.That(existing.anchoredPosition, Is.EqualTo(new Vector2(0, 50)));
            Assert.That(existing.sizeDelta, Is.EqualTo(new Vector2(100, 40)));
            Assert.That(panel.Body.color.a, Is.EqualTo(1f));
            Assert.That(panel.Body.GetComponent<Outline>().effectColor, Is.EqualTo(StudyTheme.EdgeIdle));
            Assert.That(panel.Glow.Count, Is.EqualTo(StoryUi.GlowLayers.Length));
            foreach (var layer in panel.Glow)
            {
                Assert.That(layer.rectTransform.sizeDelta.x, Is.GreaterThan(600));
                Assert.That(layer.color.a, Is.LessThan(1f));
                Assert.That(layer.transform.GetSiblingIndex(), Is.LessThan(panel.Body.transform.GetSiblingIndex()), "Glow renders behind the glass");
            }
            panel.SetLit(true);
            Assert.That(panel.Body.GetComponent<Outline>().effectColor, Is.EqualTo(StudyTheme.EdgeLit));
            Assert.That(panel.Body.color, Is.EqualTo(StudyTheme.PanelGlass));
            Assert.That(root.GetComponentsInChildren<Graphic>(true).Any(x => x.raycastTarget), Is.False);
        }

        [Test, Category("REQ_APP_07"), Category("REQ_LES_02")]
        public void StepChipsMarkDoneLitAndIdleStepsOnOpaqueSurfaces()
        {
            var row = StoryStepChips.Create(root.transform, "Steps", Vector2.zero, font, 27, 190, "Listen", "Your turn", "Check");
            Assert.That(row.Labels.Select(x => x.text), Is.EqualTo(new[] { "Listen", "Your turn", "Check" }));
            Assert.That(row.Current, Is.EqualTo(-1));
            Assert.That(row.Checks.Any(x => x.enabled), Is.False);
            row.Set(1);
            Assert.That(row.Surfaces[0].color, Is.EqualTo(StudyTheme.SurfaceEnabled));
            Assert.That(row.Checks[0].enabled, Is.True);
            Assert.That(row.Surfaces[1].color, Is.EqualTo(StudyTheme.SurfaceSelected));
            Assert.That(row.Surfaces[1].GetComponent<Outline>().effectColor, Is.EqualTo(StudyTheme.EdgeLit));
            Assert.That(row.Surfaces[2].color, Is.EqualTo(StudyTheme.PanelFill));
            Assert.That(row.Checks[1].enabled || row.Checks[2].enabled, Is.False);
            for (int i = 0; i < 3; i++) AssertTextOnOpaqueAaaSurface(row.Labels[i], row.Surfaces[i]);
            // Each chip's text and surface share one rect; chips never overlap.
            for (int i = 0; i < 3; i++) Assert.That(row.Surfaces[i].rectTransform.anchoredPosition, Is.EqualTo(row.Labels[i].rectTransform.anchoredPosition));
            for (int i = 1; i < 3; i++) Assert.That(Bounds(row.Surfaces[i - 1].rectTransform).Overlaps(Bounds(row.Surfaces[i].rectTransform)), Is.False);
        }

        [Test, Category("REQ_APP_07")]
        public void StatusChipAndBannerKeepIconsClearOfText()
        {
            var chip = StoryStatusChip.Create(root.transform, "Status", Vector2.zero, new Vector2(170, 44), font, 24, "Correct", StoryIcon.Check);
            AssertTextOnOpaqueAaaSurface(chip.Label, chip.Surface);
            Assert.That(Bounds(chip.IconImage.rectTransform).Overlaps(Bounds(chip.Label.rectTransform)), Is.False);
            chip.Show(false); Assert.That(chip.Visible, Is.False);
            chip.Set("Heard once", StoryIcon.Speaker); Assert.That(chip.Label.text, Is.EqualTo("Heard once"));

            var banner = StoryBanner.Create(root.transform, "Banner", Vector2.zero, new Vector2(500, 44), font, 24, "Notice", true);
            AssertTextOnOpaqueAaaSurface(banner.Label, banner.Surface);
            Assert.That(banner.Locked, Is.True);
            Assert.That(Bounds(banner.LockBadge.rectTransform).Overlaps(Bounds(banner.Label.rectTransform)), Is.False);
            banner.Set("Notice", false); Assert.That(banner.Locked, Is.False);
        }

        [Test]
        public void ProgressBarShowsTheGivenFractionOnly()
        {
            var bar = StoryProgressBar.Create(root.transform, "Progress", Vector2.zero, new Vector2(400, 6));
            Assert.That(bar.Fraction, Is.EqualTo(0)); Assert.That(bar.Fill.enabled, Is.False);
            bar.Set(.25f); Assert.That(bar.Fill.rectTransform.sizeDelta.x, Is.EqualTo(100).Within(1e-3));
            bar.Set(3); Assert.That(bar.Fraction, Is.EqualTo(1));
            bar.Set(float.NaN); Assert.That(bar.Fraction, Is.EqualTo(0));
            Assert.That(((RectTransform)bar.transform).sizeDelta, Is.EqualTo(new Vector2(400, 6)), "Only the fill changes");
        }

        [Test]
        public void WaveformIsDeterministicDecorationWithAnOptionalPlayhead()
        {
            var a = StoryWaveform.Create(root.transform, "A", Vector2.zero, new Vector2(300, 40), 7);
            var b = StoryWaveform.Create(root.transform, "B", Vector2.zero, new Vector2(300, 40), 7);
            var c = StoryWaveform.Create(root.transform, "C", Vector2.zero, new Vector2(300, 40), 8);
            float[] Heights(StoryWaveform w) => w.Bars.Select(x => x.rectTransform.sizeDelta.y).ToArray();
            Assert.That(Heights(a), Is.EqualTo(Heights(b)));
            Assert.That(Heights(a), Is.Not.EqualTo(Heights(c)));
            Assert.That(Heights(a).All(h => h > 0 && h <= 40), Is.True);
            Assert.That(a.PlayheadVisible, Is.False);
            Assert.That(a.Bars.All(x => x.color == StudyTheme.WaveIdle), Is.True);
            a.SetPlayhead(.5f);
            Assert.That(a.PlayheadVisible, Is.True);
            Assert.That(a.Playhead.rectTransform.anchoredPosition.x, Is.EqualTo(0).Within(1e-3));
            Assert.That(a.Bars.Count(x => x.color == StudyTheme.WaveLit), Is.EqualTo(a.Bars.Count / 2));
            a.SetPlayhead(null);
            Assert.That(a.PlayheadVisible, Is.False);
            Assert.That(a.Bars.All(x => x.color == StudyTheme.WaveIdle), Is.True);
        }

        [Test, Category("REQ_ORI_02")]
        public void EveryPublicActionAndTargetHasAProceduralIcon()
        {
            foreach (var action in PublicCommands.Actions) Assert.That(StoryIcons.ForAction(action), Is.Not.EqualTo(StoryIcon.None), action);
            foreach (var target in PublicCommands.Targets) Assert.That(StoryIcons.ForTarget(target), Is.Not.EqualTo(StoryIcon.None), target);
            Assert.That(StoryIcons.ForAction("UNKNOWN"), Is.EqualTo(StoryIcon.None));
            Assert.That(StoryIcons.Texture(StoryIcon.None), Is.Null);
            foreach (StoryIcon icon in System.Enum.GetValues(typeof(StoryIcon)))
            {
                if (icon == StoryIcon.None) continue;
                var texture = StoryIcons.Texture(icon);
                Assert.That(texture.width, Is.EqualTo(StoryIcons.TexturePixels));
                var pixels = texture.GetPixels32();
                Assert.That(pixels.Count(p => p.a > 200), Is.GreaterThan(50), icon + " draws something");
                Assert.That(pixels[0].a, Is.EqualTo(0), icon + " keeps a transparent corner");
                Assert.That(StoryIcons.Texture(icon), Is.SameAs(texture), "Icons are cached");
            }
        }
    }
}
