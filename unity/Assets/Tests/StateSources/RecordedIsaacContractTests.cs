using System;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.StateSources;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Tests
{
    public sealed class RecordedIsaacContractTests
    {
        [Test]
        public void ActualIsaacSnapshotAndWireSamplesMatchUnityRegistry()
        {
            string root=Environment.GetEnvironmentVariable("STATE_SOURCE_EVIDENCE");
            if(string.IsNullOrWhiteSpace(root)) Assert.Ignore("Set private STATE_SOURCE_EVIDENCE to actual Isaac evidence directory");
            string layout=File.ReadAllText(Path.Combine(root,"layout.json"));
            byte[] neutral=File.ReadAllBytes(Path.Combine(root,"reset-check/neutral_v1.json"));
            var snapshot=JObject.Parse(Encoding.UTF8.GetString(neutral));
            string scene=(string)snapshot["scene_sha256"],hash=SceneRegistry.Hash(neutral);
            var config=JObject.FromObject(new { version=1,scene_sha256=scene,layout_sha256=SceneRegistry.Hash(Encoding.UTF8.GetBytes(layout)),neutral_sha256=hash,
                neutral_file="neutral_v1.json",interpolation_delay_s=2d/30,
                clock=new { drift_bound_ppm=(double?)null,max_echo_age_s=2,evidence_sha256=(string)null } });
            var setup=StateSourceConfiguration.Load(config.ToString());
            var names=((JArray)snapshot["state"]["robot"]["joint_names"]).Select(x=>(string)x);
            var registry=setup.Registry("station-01",layout,names);
            var source=new SnapshotSource(neutral,registry,0);
            Assert.That(source.Neutral.Joints.Count,Is.EqualTo(43));
            Assert.That(source.Neutral.Objects.Count,Is.EqualTo(60));
            foreach(string file in new[]{"sample-first.json","sample-last.json"})
            {
                var live=StateParser.Parse(File.ReadAllText(Path.Combine(root,"publisher-check",file)),registry);
                Assert.That(live.Joints.Count,Is.EqualTo(43));
                Assert.That(live.Objects.Select(x=>x.Id),Is.EqualTo(source.Neutral.Objects.Select(x=>x.Id)));
                // This rate run is unprotected; it does not establish neutral parity.
                Assert.That(live.Provenance,Is.EqualTo("live"));
            }
        }
    }
}
