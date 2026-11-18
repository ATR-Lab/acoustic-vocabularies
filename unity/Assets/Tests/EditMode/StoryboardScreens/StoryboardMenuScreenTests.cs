using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.Serialization;
using System.Text;
using AcousticVocab.Foundation;
using AcousticVocab.SelectionMenus;
using AcousticVocab.StudyAudio;
using AcousticVocab.Teaching;
using AcousticVocab.Theme;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.UI;

namespace AcousticVocab.StoryboardScreens.Tests
{
    // Study B menu screens (frames 10 and 11) through the real MenuSessionHost
    // view. Synthetic DEMO text, a generated image and synthetic tones only.
    public sealed class StoryboardMenuScreenTests
    {
        const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
        static readonly MenuPhase[] Visible = { MenuPhase.Instructions, MenuPhase.Audition, MenuPhase.Choice, MenuPhase.Selected };

        internal static PcmWave Wave(int samples, int tone)
        {
            using var stream = new MemoryStream(); using var writer = new BinaryWriter(stream);
            writer.Write(Encoding.ASCII.GetBytes("RIFF")); writer.Write(36 + samples * 2); writer.Write(Encoding.ASCII.GetBytes("WAVEfmt ")); writer.Write(16); writer.Write((short)1); writer.Write((short)1);
            writer.Write(48000); writer.Write(96000); writer.Write((short)2); writer.Write((short)16); writer.Write(Encoding.ASCII.GetBytes("data")); writer.Write(samples * 2);
            for (int i = 0; i < samples; i++) writer.Write((short)(i % 100 < 50 ? tone : -tone)); writer.Flush(); return PcmWave.ParseCanonical(stream.ToArray());
        }
        static object Make(Type type, params object[] args) => type.GetConstructors(Private | BindingFlags.Public).Single(c => c.GetParameters().Length == args.Length).Invoke(args);
        internal static MenuMaterial Material(bool profile, string choiceWording)
        {
            var texture = new Texture2D(4, 4, TextureFormat.RGBA32, false); byte[] png; try { png = texture.EncodeToPNG(); } finally { UnityEngine.Object.DestroyImmediate(texture); }
            var meaning = profile ? null : (TeachingDisplay)Make(typeof(TeachingDisplay), new JObject { ["meaning_display_id"] = "DEMO-meaning", ["definition"] = "DEMO meaning definition",
                ["action_words"] = "DEMO action", ["target_words"] = "DEMO target", ["image_id"] = "DEMO-image" }, png, PcmWave.Hash(png));
            var options = Enumerable.Range(1, 3).Select(i => (MenuOption)Make(typeof(MenuOption), "DEMO-option-" + i, Wave(profile ? 96000 : 21600, i * 1000))).ToArray();
            return (MenuMaterial)Make(typeof(MenuMaterial), profile ? "profile" : "K-a3", profile ? "DEMO-profile-display" : "DEMO-meaning", meaning, "DEMO compare the three options",
                choiceWording, new[] { "Option 1", "Option 2", "Option 3" }, options);
        }

        internal sealed class View : IDisposable
        {
            static readonly Type Host = typeof(MenuSessionHost);
            readonly GameObject holder, lease; internal readonly GameObject Presentation;
            internal readonly MenuSessionHost Menu; internal readonly Canvas Canvas;
            internal View()
            {
                holder = new GameObject("Inactive synthetic foundation"); holder.SetActive(false); Presentation = new GameObject("Synthetic presentation"); lease = new GameObject("Synthetic menu lease");
                var foundation = holder.AddComponent<FoundationBootstrap>();
                typeof(FoundationBootstrap).GetField("configuration", Private).SetValue(foundation, new JObject { ["observer_reference"] = new JObject { ["position_m"] = new JArray(0, 1.5, 1.45), ["rotation_xyzw"] = new JArray(0, 0, 0, 1) } });
                Menu = lease.AddComponent<MenuSessionHost>(); Menu.foundation = foundation; Menu.presentationParent = Presentation.transform; Menu.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
                Host.GetMethod("CreateView", Private).Invoke(Menu, null); Canvas = Presentation.GetComponentInChildren<Canvas>(true);
                // Marks the view installed without a live module or XR input; Prepare and Show check only that.
                Host.GetField("factory", Private).SetValue(Menu, FormatterServices.GetUninitializedObject(typeof(MenuContentFactory)));
                Host.GetProperty("InputAvailable").GetSetMethod(true).Invoke(Menu, new object[] { true });
            }
            internal MenuScreenStyle Style => (MenuScreenStyle)Host.GetField("style", Private).GetValue(Menu);
            public void Dispose()
            {
                Host.GetField("factory", Private).SetValue(Menu, null);
                foreach (Texture2D texture in ((IDictionary)Host.GetField("textures", Private).GetValue(Menu)).Values) UnityEngine.Object.DestroyImmediate(texture);
                UnityEngine.Object.DestroyImmediate(Presentation); UnityEngine.Object.DestroyImmediate(lease); UnityEngine.Object.DestroyImmediate(holder);
            }
        }

        static string Path(Transform node, Transform root) => node == root ? "" : Path(node.parent, root) + "/" + node.name + "#" + node.GetSiblingIndex();
        // Every node of the screen: visibility, layout, colour, text and edge.
        static string[] Structure(View view) => view.Canvas.GetComponentsInChildren<Transform>(true).Select(node =>
        {
            var rect = node as RectTransform; var graphic = node.GetComponent<Graphic>(); var text = node.GetComponent<Text>(); var edge = node.GetComponent<Outline>();
            return Path(node, view.Canvas.transform) + " active=" + node.gameObject.activeSelf + (rect == null ? "" : " at=" + rect.anchoredPosition + " size=" + rect.sizeDelta)
                + (graphic == null ? "" : " color=" + graphic.color + " raycast=" + graphic.raycastTarget) + (text == null ? "" : " text=" + text.text + " font=" + text.fontSize)
                + (edge == null ? "" : " edge=" + edge.effectColor);
        }).ToArray();

        [TestCase(true)][TestCase(false)]
        [Category("REQ_YOKE_04"), Category("REQ_MENU_02"), Category("REQ_YOKE_02"), Category("REQ_MENU_08")]
        public void AssignedScreenDiffersFromTheActiveScreenOnlyByTheLockBadge(bool profile)
        {
            // Same material for both roles, so any difference comes from the view.
            var material = Material(profile, "DEMO choose an option");
            using var active = new View(); using var assigned = new View();
            active.Menu.Prepare(material); assigned.Menu.Prepare(material);
            var badge = Path(assigned.Style.LockBadge.transform, assigned.Canvas.transform);
            foreach (var phase in Visible)
            {
                int? selected = phase == MenuPhase.Selected ? 2 : (int?)null;
                active.Menu.Show("DEMO-active", material, phase, selected, false); assigned.Menu.Show("DEMO-assigned", material, phase, selected, true);
                var a = Structure(active); var b = Structure(assigned);
                Assert.That(b.Length, Is.EqualTo(a.Length), phase + ": same nodes");
                var differing = a.Zip(b, (x, y) => (x, y)).Where(p => p.x != p.y).ToArray();
                Assert.That(differing.Length, Is.EqualTo(1), phase + ": " + string.Join("; ", differing.Select(p => p.x + " <> " + p.y)));
                Assert.That(differing[0].y, Does.StartWith(badge + " active=True"), phase + ": only the assigned lock badge differs");
                Assert.That(active.Style.LockBadge.activeSelf, Is.False); Assert.That(assigned.Style.LockBadge.activeSelf, Is.True);
                Assert.That(assigned.Menu.SimulationChoiceOwner, Is.Null, phase + ": the assigned role has no choice surface");
                // No countdown element exists on either screen (the station flag is a separate change).
                Assert.That(a.Concat(b).Any(x => x.IndexOf("countdown", StringComparison.OrdinalIgnoreCase) >= 0), Is.False);
            }
        }

        [Test, Category("REQ_MENU_03"), Category("REQ_YOKE_04"), Category("REQ_MENU_04")]
        public void EveryMenuTextSurfaceKeepsAaaContrastAndThePlayMarksAreIdenticalAndInert()
        {
            var material = Material(false, "DEMO choose an option");
            using var view = new View(); view.Menu.Prepare(material);
            foreach (var phase in Visible)
                foreach (bool readOnly in new[] { false, true })
                {
                    view.Menu.Show(readOnly ? "DEMO-assigned" : "DEMO-active", material, phase, phase == MenuPhase.Selected ? 1 : (int?)null, readOnly);
                    foreach (var text in view.Canvas.GetComponentsInChildren<Text>(true))
                    {
                        Assert.That(text.color, Is.EqualTo(StudyTheme.TextPrimary), text.name);
                        // The surface behind each text: its own card, or the backing named after it.
                        var surface = text.transform.parent.GetComponent<Image>();
                        if (surface == null) { var backing = view.Canvas.transform.Find(text.name + " backing"); surface = backing == null ? null : backing.GetComponent<Image>(); }
                        Assert.That(surface != null, Is.True, text.name + " has an opaque surface");
                        Assert.That(surface.color.a, Is.EqualTo(1f), text.name);
                        Assert.That(StudyTheme.ContrastRatio(text.color, surface.color), Is.GreaterThanOrEqualTo(StudyTheme.MinimumTextContrast), phase + " " + text.name);
                    }
                }
            var marks = view.Style.PlayMarks; Assert.That(marks.Length, Is.EqualTo(3));
            string Shape(GameObject mark) => string.Join(",", mark.GetComponentsInChildren<Image>(true).Select(i => ((RectTransform)i.transform).anchoredPosition + ":" + ((RectTransform)i.transform).sizeDelta + ":" + i.color));
            Assert.That(marks.Select(Shape).Distinct().Count(), Is.EqualTo(1), "The same mark under every option");
            Assert.That(marks[0].GetComponentsInChildren<Image>(true).Count(i => i.color == StudyTheme.TextPrimary), Is.EqualTo(MenuScreenStyle.PlaysPerOption), "Two play dots");
            // Marks and badge are inert: no raycast target and no collider beyond the three cards.
            Assert.That(view.Canvas.GetComponentsInChildren<Graphic>(true).All(g => !g.raycastTarget), Is.True);
            Assert.That(view.Canvas.GetComponentsInChildren<Collider>(true).Length, Is.EqualTo(3));
            // Marks sit below their card and never over a label.
            var cards = view.Canvas.GetComponentsInChildren<Transform>(true).Where(t => t.name.StartsWith("Candidate ", StringComparison.Ordinal)).Cast<RectTransform>().ToArray();
            for (int i = 0; i < 3; i++)
            {
                var mark = (RectTransform)marks[i].transform;
                Assert.That(mark.anchoredPosition.y + mark.sizeDelta.y / 2, Is.LessThan(cards[i].anchoredPosition.y - cards[i].sizeDelta.y / 2));
                Assert.That(mark.anchoredPosition.y - mark.sizeDelta.y / 2, Is.GreaterThanOrEqualTo(-((RectTransform)view.Canvas.transform).sizeDelta.y / 2));
            }
        }
    }
}
