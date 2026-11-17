using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using AcousticVocab.Foundation.Editor;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.Foundation.Tests
{
    // Requirement trace for the in-headset presentation, the fixed observer
    // reference and the absence of microphone decoding. Static source checks over
    // the experiment application plus the real origin solver.
    public sealed class RequirementTraceStationTests
    {
        static string App => Path.Combine(FoundationBuild.RepositoryRoot, "unity/Assets/ExperimentApp");
        static (string path, string text)[] Sources(string folder) =>
            Directory.GetFiles(Path.Combine(App, folder), "*.cs", SearchOption.AllDirectories).Select(p => (p, File.ReadAllText(p))).ToArray();

        [Test, Category("REQ_APP_03"), NUnit.Framework.Property("Requirement", "REQ-APP-03")]
        public void EveryCanvasIsWorldSpaceAndNothingRendersToAScreenOrExternalDisplay()
        {
            var sources = Sources("Runtime").Concat(Sources("Editor")).ToArray();
            var canvases = sources.SelectMany(s => Regex.Matches(s.text, @"\.renderMode\s*=\s*(RenderMode\.[A-Za-z]+)").Cast<Match>().Select(m => (s.path, mode: m.Groups[1].Value))).ToArray();
            Assert.That(canvases.Select(x => Path.GetFileName(x.path)), Is.SupersetOf(new[] { "TeachingSessionHost.cs", "MenuSessionHost.cs", "AudioBuild.cs" }));
            Assert.That(canvases.Where(x => x.mode != "RenderMode.WorldSpace").Select(x => x.path), Is.Empty);
            foreach (var (path, text) in Sources("Runtime"))
                Assert.That(Regex.IsMatch(text, @"ScreenSpace|targetDisplay|Display\.displays|Display\.main|\bOnGUI\s*\(|GUILayout\.|GUI\.(Label|Box|Button)"), Is.False, path);
            // Every participant-facing surface is anchored at the configured observer reference.
            foreach (string file in new[] { "Teaching/TeachingSessionHost.cs", "SelectionMenus/MenuSessionHost.cs", "ResponsePanel/ResponsePanelController.cs", "Orientation/OrientationHost.cs", "Assessment/AssessmentScreen.cs", "StudyAudio/AudioCalibrationHost.cs" })
                Assert.That(File.ReadAllText(Path.Combine(App, "Runtime", file)), Does.Contain("StationConfig.ReferencePose("), file);
        }

        [Test, Category("REQ_APP_04"), NUnit.Framework.Property("Requirement", "REQ-APP-04")]
        public void NoLocomotionExistsAndOnlyTheReferenceRestoreMovesTheObserverOrigin()
        {
            var runtime = Sources("Runtime");
            foreach (var (path, text) in runtime)
                Assert.That(Regex.IsMatch(text, @"CharacterController|Locomotion|Teleport|ContinuousMove|SnapTurn|ContinuousTurn|Rigidbody"), Is.False, path);
            var writes = runtime.SelectMany(s => Regex.Matches(s.text, @"seatedOrigin\s*\.\s*(SetPositionAndRotation|position\s*=|rotation\s*=|Translate|Rotate|localPosition\s*=|localRotation\s*=)").Cast<Match>().Select(m => Path.GetFileName(s.path))).ToArray();
            Assert.That(writes, Is.EqualTo(new[] { "FoundationBootstrap.cs" }), "One origin write exists");
            string bootstrap = File.ReadAllText(Path.Combine(App, "Runtime/FoundationBootstrap.cs"));
            Assert.That(bootstrap, Does.Contain("ObserverReference.ResolveOrigin(StationConfig.ReferencePose(configuration), headInOrigin)"));
            Assert.That(bootstrap, Does.Contain("boundary != \"startup\" && boundary != \"between_trials\" && boundary != \"operator_recovery\""));
            // Wherever the head has drifted, a restore returns it to the same configured reference pose.
            var reference = new Pose(new Vector3(.4f, 1.2f, -2f), Quaternion.Euler(0, 75, 0));
            foreach (var head in new[] { new Pose(Vector3.zero, Quaternion.identity), new Pose(new Vector3(.3f, .9f, -.4f), Quaternion.Euler(10, -60, 5)), new Pose(new Vector3(-.2f, 1.1f, .6f), Quaternion.Euler(-8, 170, 0)) })
            {
                var origin = ObserverReference.ResolveOrigin(reference, head);
                Assert.That(Vector3.Distance(origin.position + origin.rotation * head.position, reference.position), Is.LessThan(.00001f));
            }
        }

        [Test, Category("REQ_APP_06"), NUnit.Framework.Property("Requirement", "REQ-APP-06")]
        public void TheAppNeverCapturesOrDecodesMicrophoneAudio()
        {
            foreach (var (path, text) in Sources("Runtime").Concat(Sources("Editor")))
                Assert.That(Regex.IsMatch(text, @"\bMicrophone\b|KeywordRecognizer|DictationRecognizer|GrammarRecognizer|PhraseRecognition|Windows\.Speech|RECORD_AUDIO"), Is.False, path);
            string settings = File.ReadAllText(Path.Combine(FoundationBuild.RepositoryRoot, "unity/ProjectSettings/ProjectSettings.asset"));
            Assert.That(Regex.Match(settings, @"microphoneUsageDescription:[ \t]*(\S*)").Groups[1].Value, Is.Empty);
        }
    }
}
