using System;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.Foundation.Editor;
using AcousticVocab.ResponsePanel;
using AcousticVocab.StateSources;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Orientation.Tests
{
    // Real producers with an injected clock and synthetic demo observations.
    // The export is codec/sequence evidence, never a rendered screening visit.
    public sealed class OrientationScreeningExportTests
    {
        static byte[] Bytes(JObject value) => ReceiptCanonical.Bytes(value);
        static void Save(string path, byte[] bytes)
        {
            using (var file = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.Read))
            { file.Write(bytes, 0, bytes.Length); file.Flush(true); }
        }
        [TestCase("pass_first")]
        [TestCase("pass_second")]
        [TestCase("fail")]
        public void SealedActualProducersPreserveDeadlineAndDraftSemantics(string expected)
        {
            string configured = Environment.GetEnvironmentVariable("AV_ORIENTATION_EXPORT");
            string parent = string.IsNullOrEmpty(configured)
                ? Path.Combine(FoundationBuild.RepositoryRoot, ".local", "orientation-export-" + Guid.NewGuid().ToString("N"))
                : Path.GetFullPath(configured);
            string directory = Path.Combine(parent, expected);
            Assert.That(Directory.Exists(directory), Is.False, "Each export requires a fresh destination");
            Directory.CreateDirectory(directory);
            var document = JObject.Parse(File.ReadAllText(Path.Combine(FoundationBuild.RepositoryRoot, "apparatus/orientation/orientation-plan.example.json")));
            Assert.That((string)document["content_status"], Is.EqualTo("engineering_draft"));
            var plan = OrientationPlan.Parse(document.ToString(), (string)document["protocol_version"]);
            byte[] planBytes = Bytes(document), demoBytes = Bytes(new JObject { ["scope"] = "SYNTHETIC_LOGIC_ONLY", ["no_demo_files"] = true });
            Save(Path.Combine(directory, "plan.json"), planBytes); Save(Path.Combine(directory, "demo-index.json"), demoBytes);
            double now = 0;
            var build = new JObject { ["protocol_version"] = plan.ProtocolVersion, ["qualification"] = "synthetic_logic_only_no_native_visit" };
            using (var journal = new OrientationJournal(directory, build, "synthetic-station", SceneRegistry.Hash(planBytes), SceneRegistry.Hash(demoBytes), "SYNTHETIC-export"))
            {
                // Exact host seam shape; this is an explicit test input, not an asset/capture qualification.
                journal.Record(new JObject { ["event"] = "orientation_assets_validated", ["mono_ms"] = now, ["source_kind"] = "snapshot", ["demo_count"] = 8,
                    ["nominal_duration_seconds"] = 10, ["timing_tolerance"] = "one recorded sample period; provisional", ["study_package_access"] = false });
                var flow = new OrientationFlow(plan, () => now, journal.Record);
                flow.Start();
                for (int attempt = 1; attempt <= (expected == "pass_first" ? 1 : 2); attempt++)
                {
                    if (attempt == 2) flow.Next();
                    for (int i = 0; i < 8; i++)
                    {
                        now += 10000; flow.CompleteDemo(flow.CurrentCard.Id, 10000, 10000, 1000d / 30); flow.Next();
                    }
                    for (int i = 0; i < 8; i++) flow.Next();
                    for (int i = 0; i < 8; i++)
                    {
                        var item = flow.CurrentItem; var request = flow.OpenPractice();
                        var panel = new ResponseState(() => now, _ => { }); panel.Responded += flow.Respond; panel.Open(request);
                        Assert.That(panel.SelectTarget(item.Target), Is.True); Assert.That(panel.SelectAction(item.Action), Is.True);
                        bool timeout = expected != "pass_first" && attempt == 1 && i == 0;
                        bool dontKnow = expected == "fail" && attempt == 2 && i == 0;
                        now = request.DeadlineMonoMs - (timeout ? 0 : 0.001);
                        if (timeout)
                        {
                            Assert.That(panel.Commit(), Is.False); Assert.That(panel.Result.Code, Is.EqualTo(ResponseCode.Timeout));
                            Assert.That(panel.Result.ResponseMonoMs, Is.EqualTo(request.DeadlineMonoMs));
                        }
                        else if (dontKnow)
                        { Assert.That(panel.DontKnow(), Is.True); Assert.That(panel.Result.Code, Is.EqualTo(ResponseCode.DontKnow)); }
                        else
                        { Assert.That(panel.Commit(), Is.True); Assert.That(panel.Result.ResponseMonoMs, Is.LessThan(request.DeadlineMonoMs)); }
                        flow.Next();
                    }
                }
                Assert.That(flow.Stage, Is.EqualTo(OrientationStage.RecordedOutcome));
                Assert.That(flow.EligibleOutcomeRecorded, Is.False);
                var receipt = journal.SealOutcome("SYNTHETIC-export", flow.Outcome);
                Assert.That((string)receipt.Json["outcome"], Is.EqualTo(expected)); Assert.That(receipt.Eligible, Is.False);
                Save(Path.Combine(directory, "receipt.json"), Bytes(receipt.Json));
                byte[] raw = File.ReadAllBytes(journal.JournalPath);
                Assert.That((string)receipt.Json["journal_sha256"], Is.EqualTo(SceneRegistry.Hash(raw)));
                Assert.That(JObject.Parse(File.ReadLines(journal.JournalPath).Last())["event"].Value<string>(), Is.EqualTo("eligibility_outcome"));
                Assert.Throws<OrientationFault>(() => journal.Record(new JObject { ["event"] = "late" }));
                var inventory = new JArray(Directory.GetFiles(directory).OrderBy(x => x, StringComparer.Ordinal).Select(path => new JObject {
                    ["path"] = Path.GetFileName(path), ["sha256"] = SceneRegistry.Hash(File.ReadAllBytes(path)), ["bytes"] = new FileInfo(path).Length }));
                Save(Path.Combine(directory, "export.json"), Bytes(new JObject { ["version"] = 1, ["scope"] = "SYNTHETIC_LOGIC_ONLY", ["outcome"] = expected,
                    ["clock"] = "injected_test_clock", ["actual_csharp_producers"] = true, ["native_visit"] = false, ["participant_admission"] = false, ["files"] = inventory }));
            }
        }
    }
}
