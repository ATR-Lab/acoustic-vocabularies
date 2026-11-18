using System;
using System.Linq;
using AcousticVocab.StudyAudio;
using AcousticVocab.StudyAudio.Editor;
using AcousticVocab.Theme;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.UI;

namespace AcousticVocab.StoryboardScreens.Tests
{
    // The comfortable-level screen (frame 1) built by the real calibration
    // view builder on a bare canvas. No audio, configuration or XR input.
    public sealed class StoryboardCalibrationScreenTests
    {
        sealed class Screen : IDisposable
        {
            internal readonly Canvas Canvas; internal readonly AudioCalibrationHost Host; readonly GameObject hostObject;
            internal Screen()
            {
                var root = new GameObject("Synthetic comfort screen", typeof(RectTransform), typeof(Canvas));
                Canvas = root.GetComponent<Canvas>(); Canvas.renderMode = RenderMode.WorldSpace; root.GetComponent<RectTransform>().sizeDelta = new Vector2(900, 620);
                hostObject = new GameObject("Synthetic calibration host"); Host = hostObject.AddComponent<AudioCalibrationHost>(); Host.canvas = Canvas;
                AudioBuild.BuildView(Canvas, Host);
            }
            public void Dispose() { UnityEngine.Object.DestroyImmediate(Canvas.gameObject); UnityEngine.Object.DestroyImmediate(hostObject); }
        }
        static Rect Bounds(RectTransform rect) => new Rect(rect.anchoredPosition - rect.sizeDelta / 2, rect.sizeDelta);

        [Test, Category("REQ_AUD_05")]
        public void ReviewedLayoutAndLabelSizesAreUnchanged()
        {
            using var screen = new Screen(); var host = screen.Host;
            foreach (var (button, label, at, size) in new[] { (host.quieterButton, "Quieter", new Vector2(-255, 60), new Vector2(180, 60)), (host.louderButton, "Louder", new Vector2(255, 60), new Vector2(180, 60)),
                (host.listenButton, "Listen twice", new Vector2(0, -30), new Vector2(350, 70)), (host.yesButton, "Yes, comfortable", new Vector2(-205, -125), new Vector2(340, 70)),
                (host.noButton, "No", new Vector2(205, -125), new Vector2(340, 70)), (host.stopButton, "Stop", new Vector2(0, -235), new Vector2(220, 60)) })
            {
                var rect = (RectTransform)button.transform;
                Assert.That(rect.anchoredPosition, Is.EqualTo(at), label); Assert.That(rect.sizeDelta, Is.EqualTo(size), label);
                var text = button.GetComponentInChildren<Text>(); Assert.That(text.text, Is.EqualTo(label)); Assert.That(text.fontSize, Is.EqualTo(28), label);
                Assert.That(button.interactable, Is.False, label + " starts disabled");
            }
            foreach (var (text, at, size, font) in new[] { (screen.Canvas.transform.Find("Title").GetComponent<Text>(), new Vector2(0, 245), new Vector2(840, 60), 38),
                (host.statusText, new Vector2(0, 160), new Vector2(800, 100), 28), (host.gainText, new Vector2(0, 60), new Vector2(300, 50), 32) })
            {
                Assert.That(((RectTransform)text.transform).anchoredPosition, Is.EqualTo(at), text.name); Assert.That(((RectTransform)text.transform).sizeDelta, Is.EqualTo(size), text.name);
                Assert.That(text.fontSize, Is.EqualTo(font), text.name);
            }
            // Only the component types the calibration scene check allows.
            var allowed = new[] { typeof(Transform), typeof(RectTransform), typeof(Canvas), typeof(CanvasRenderer), typeof(Image), typeof(Text), typeof(Button) };
            Assert.That(screen.Canvas.GetComponentsInChildren<Component>(true).Select(c => c.GetType()).Except(allowed), Is.Empty);
        }

        [Test, Category("REQ_AUD_05")]
        public void EveryTextSurfaceAndButtonStateKeepsAaaContrast()
        {
            using var screen = new Screen(); var host = screen.Host;
            var background = screen.Canvas.transform.Find("Background").GetComponent<Image>();
            Assert.That(background.color, Is.EqualTo(StudyTheme.PanelFill));
            foreach (var text in screen.Canvas.GetComponentsInChildren<Text>(true))
            {
                Assert.That(text.color, Is.EqualTo(StudyTheme.TextPrimary), text.name); Assert.That(text.fontStyle, Is.EqualTo(StudyTheme.TextStyle));
                Assert.That(StudyTheme.ContrastRatio(text.color, background.color), Is.GreaterThanOrEqualTo(StudyTheme.MinimumTextContrast));
            }
            foreach (var button in new[] { host.listenButton, host.quieterButton, host.louderButton, host.yesButton, host.noButton, host.stopButton })
            {
                var graphic = (Image)button.targetGraphic; var colors = button.colors;
                Assert.That(graphic.color, Is.EqualTo(Color.white), "State tints apply exactly"); Assert.That(colors.colorMultiplier, Is.EqualTo(1f));
                foreach (var state in new[] { colors.normalColor, colors.highlightedColor, colors.pressedColor, colors.selectedColor, colors.disabledColor })
                {
                    Assert.That(state.a, Is.EqualTo(1f), button.name);
                    Assert.That(StudyTheme.ContrastRatio(StudyTheme.TextPrimary, state), Is.GreaterThanOrEqualTo(StudyTheme.MinimumTextContrast), button.name);
                }
                Assert.That(colors.normalColor, Is.EqualTo(StudyTheme.SurfaceEnabled)); Assert.That(colors.disabledColor, Is.EqualTo(StudyTheme.SurfaceDisabled));
                Assert.That(colors.highlightedColor, Is.EqualTo(StudyTheme.SurfaceSelected)); Assert.That(colors.pressedColor, Is.EqualTo(StudyTheme.SurfaceSelected));
            }
        }

        [Test, Category("REQ_AUD_05")]
        public void LevelBarOnlyDisplaysTheGainTheLevelLogicSet()
        {
            using var screen = new Screen(); var host = screen.Host;
            Assert.That(host.levelFill, Is.Not.Null); Assert.That(host.levelKnob, Is.Not.Null);
            var track = (RectTransform)host.levelFill.parent;
            Assert.That(track.GetComponentsInChildren<Selectable>(true), Is.Empty, "The bar takes no input");
            Assert.That(track.GetComponentsInChildren<Graphic>(true).All(g => !g.raycastTarget), Is.True);
            foreach (float gain in new[] { .01f, ComfortableGainStore.InitialGain, .55f, 1f })
            {
                CalibrationLevelView.Show(host.levelFill, host.levelKnob, gain);
                Assert.That(host.levelFill.anchorMax.x, Is.EqualTo(gain).Within(1e-6)); Assert.That(host.levelKnob.anchorMin.x, Is.EqualTo(gain).Within(1e-6));
            }
            // The view never extends the domain: out-of-range or invalid values are drawn at its ends.
            Assert.That(CalibrationLevelView.Fraction(2f), Is.EqualTo(1f)); Assert.That(CalibrationLevelView.Fraction(float.NaN), Is.EqualTo(0f));
            Assert.That(CalibrationLevelView.Fraction(-1f), Is.EqualTo(0f));
            // The bar and knob sit between the level row and the listen button.
            var band = Bounds(track); band.yMin = Mathf.Min(band.yMin, track.anchoredPosition.y - host.levelKnob.sizeDelta.y / 2); band.yMax = Mathf.Max(band.yMax, track.anchoredPosition.y + host.levelKnob.sizeDelta.y / 2);
            foreach (var other in new Selectable[] { host.quieterButton, host.louderButton, host.listenButton })
                Assert.That(band.Overlaps(Bounds((RectTransform)other.transform)), Is.False, other.name);
            Assert.That(band.Overlaps(Bounds((RectTransform)host.gainText.transform)), Is.False);
        }
    }
}
