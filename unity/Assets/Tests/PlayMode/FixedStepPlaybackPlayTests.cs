using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.StateSources;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace AcousticVocab.PlayModeTests
{
    // The #56 recorder's synthetic fixed-step fixture played over actual Unity
    // frames. The host clock is injected and advances once per real frame at a
    // headset-like rate; this is not an elapsed-time or headset measurement.
    public sealed class FixedStepPlaybackPlayTests
    {
        static string Fixture => Path.GetFullPath(Path.Combine(Application.dataPath,"..","..","tests","isaac","fixtures","fixed-step-demo"));
        IEnumerator Play(double refreshHz,double start)
        {
            byte[] neutral=File.ReadAllBytes(Path.Combine(Fixture,"neutral.json"));
            byte[] trajectory=File.ReadAllBytes(Path.Combine(Fixture,"000.ndjson"));
            var manifest=JObject.Parse(File.ReadAllText(Path.Combine(Fixture,"manifest.json"),Encoding.UTF8));
            var registry=new SceneRegistry((string)manifest["station_id"],(string)manifest["scene_sha256"],SceneRegistry.Hash(neutral),
                manifest["joint_names"].Select(x=>(string)x),new Dictionary<string,string[]>{{"card",new[]{"card_face"}}},new string[0]);
            var source=new SnapshotSource(neutral,registry,start);var completed=new List<SourceEvent>();
            source.Event+=e=>{ if(e.Code=="TRAJECTORY_COMPLETED") completed.Add(e); };
            source.PlayTrajectory(trajectory,SceneRegistry.Hash(trajectory),start);
            var shown=new List<long>();int frame=0;
            while(completed.Count==0)
            {
                yield return null;
                double elapsed=++frame/refreshHz;
                var rendered=source.Render(start+elapsed);
                Assert.That(rendered.Sequence,Is.EqualTo((long)(FixedStepSchedule.PlaybackIndex(elapsed)??FixedStepSchedule.SampleCount-1)),"frame "+frame);
                shown.Add(rendered.Sequence);
                Assert.That(frame,Is.LessThan(refreshHz*11),"playback did not end at the fixed boundary");
            }
            // Every sample index was reached in order, nothing was skipped at this
            // refresh rate, and the end is the common 10 s within one display frame.
            Assert.That(shown.Zip(shown.Skip(1),(a,b)=>b>=a).All(x=>x),Is.True);
            Assert.That(shown.Distinct().Count(),Is.EqualTo(FixedStepSchedule.SampleCount));
            double observed=completed.Single().DurationSeconds.Value;
            Assert.That(observed,Is.GreaterThanOrEqualTo(FixedStepSchedule.DurationSeconds));
            Assert.That(observed-FixedStepSchedule.DurationSeconds,Is.LessThan(1/refreshHz+1e-9));
            Assert.That(source.PlaybackFinished,Is.True);Assert.That(source.ResetConfirmed,Is.False);
        }
        [UnityTest] public IEnumerator Quest72Hz() => Play(72,3.25);
        [UnityTest] public IEnumerator Quest90Hz() => Play(90,1000);
        [UnityTest] public IEnumerator Display59_94Hz() => Play(59.94,0);
    }
}
