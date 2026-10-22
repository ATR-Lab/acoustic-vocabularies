using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.Foundation.Editor;
using AcousticVocab.StateSources;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Tests
{
    // #62 snapshot player against the #56 fixed sim-step recorder's own output
    // (python -m isaac.demos.unity_fixture). Synthetic identities only. The
    // fixture's retained host clock overruns 10 s and stalls 740 ms before sample
    // 150, so any pacing by capture stamps or sim_time would visibly disagree.
    public sealed class FixedStepPlaybackTests
    {
        static string Fixture => Path.Combine(FoundationBuild.RepositoryRoot,"tests","isaac","fixtures","fixed-step-demo");
        static JObject Json(string name) => JObject.Parse(File.ReadAllText(Path.Combine(Fixture,name),Encoding.UTF8));
        static byte[] Neutral => File.ReadAllBytes(Path.Combine(Fixture,"neutral.json"));
        static byte[] Trajectory => File.ReadAllBytes(Path.Combine(Fixture,"000.ndjson"));
        static SceneRegistry Registry()
        {
            var manifest=Json("manifest.json");
            return new SceneRegistry((string)manifest["station_id"],(string)manifest["scene_sha256"],SceneRegistry.Hash(Neutral),
                manifest["joint_names"].Select(x=>(string)x),new Dictionary<string,string[]>{{"card",new[]{"card_face"}}},new string[0]);
        }
        static List<JObject> Frames() => File.ReadAllLines(Path.Combine(Fixture,"000.ndjson")).Where(x=>x.Length>0).Select(JObject.Parse).ToList();
        static byte[] Encode(IEnumerable<JObject> frames) => Encoding.UTF8.GetBytes(string.Join("\n",frames.Select(f=>f.ToString(Formatting.None)))+"\n");
        static double X(SceneFrame frame) => frame.Objects.Single().Position.x;

        [Test] public void FixtureIsTheRecorderOutputDescribedByItsManifest()
        {
            var manifest=Json("manifest.json");
            Assert.That((bool)manifest["synthetic"],Is.True);Assert.That((bool)manifest["isaac_evidence"],Is.False);
            foreach(var file in (JObject)manifest["files"])
                Assert.That(SceneRegistry.Hash(File.ReadAllBytes(Path.Combine(Fixture,file.Key))),Is.EqualTo((string)file.Value),file.Key);
            var schedule=(JObject)manifest["schedule"];
            Assert.That((string)schedule["kind"],Is.EqualTo(FixedStepSchedule.Kind));
            Assert.That((int)schedule["sample_count"],Is.EqualTo(FixedStepSchedule.SampleCount));
            Assert.That((int)schedule["sample_hz"],Is.EqualTo(FixedStepSchedule.SampleHz));
            Assert.That((int)schedule["physics_steps_per_sample"],Is.EqualTo(FixedStepSchedule.PhysicsStepsPerSample));
            Assert.That((int)schedule["total_physics_steps"],Is.EqualTo(FixedStepSchedule.TotalPhysicsSteps));
            Assert.That((double)schedule["physics_dt_seconds"],Is.EqualTo(FixedStepSchedule.PhysicsDtSeconds));
            Assert.That((double)schedule["recorded_duration_seconds"],Is.EqualTo(FixedStepSchedule.DurationSeconds));
        }
        [Test] public void HostClockScheduleMatchesThePythonRecorderExactly()
        {
            var table=Json("playback.json");
            var offsets=((JArray)table["offsets_seconds"]).Select(x=>(double)x).ToArray();
            Assert.That(offsets.Length,Is.EqualTo(300));
            for(int i=0;i<300;i++) Assert.That(FixedStepSchedule.OffsetSeconds(i),Is.EqualTo(offsets[i]),"offset "+i);
            int checkedRows=0;
            foreach(JArray row in (JArray)table["index_at_elapsed_seconds"])
            {
                int? expected=row[1].Type==JTokenType.Null?(int?)null:(int)row[1];
                Assert.That(FixedStepSchedule.PlaybackIndex((double)row[0]),Is.EqualTo(expected),"elapsed "+((double)row[0]).ToString("R"));checkedRows++;
            }
            Assert.That(checkedRows,Is.GreaterThan(600));
            foreach(double bad in new[]{-1e-9,double.NaN,double.PositiveInfinity}) Assert.Throws<StateFault>(()=>FixedStepSchedule.PlaybackIndex(bad));
            Assert.Throws<StateFault>(()=>FixedStepSchedule.OffsetSeconds(300));Assert.Throws<StateFault>(()=>FixedStepSchedule.OffsetSeconds(-1));
        }
        [Test] public void SampleIndexOnTheHostClockPacesPlaybackNotCaptureStampsOrSimTime()
        {
            var source=new SnapshotSource(Neutral,Registry(),0);var frames=Frames();var events=new List<SourceEvent>();source.Event+=events.Add;
            source.PlayTrajectory(Trajectory,SceneRegistry.Hash(Trajectory),0);
            foreach(int i in new[]{0,1,149,150,151,152,298,299})
            {
                var shown=source.Render(i/30d);
                Assert.That(shown.Sequence,Is.EqualTo(i));Assert.That(shown.SimStep,Is.EqualTo(2*(i+1)));
                Assert.That(X(shown),Is.EqualTo((float)(double)frames[i]["objects"][0]["position_m"][0]).Within(1e-6));
            }
            // The capture stalled 740 ms before sample 150 and its stamps span more
            // than 10 s. Neither changes when a sample is shown or when play ends.
            ulong Stamp(int i) => ulong.Parse((string)frames[i]["host_monotonic_ns"]);
            Assert.That((Stamp(150)-Stamp(149))/1e6,Is.EqualTo(740).Within(1e-9));
            Assert.That((Stamp(299)-Stamp(0))/1e9,Is.GreaterThan(10));
            Assert.That(source.PlaybackFinished,Is.False);
            Assert.That(source.Render(9.99).Sequence,Is.EqualTo(299),"last sample held to the boundary");
            Assert.That(source.PlaybackFinished,Is.False);
            Assert.That(events.Any(e=>e.Code=="TRAJECTORY_COMPLETED"),Is.False);
            Assert.That(source.Render(10).Sequence,Is.EqualTo(299));
            Assert.That(source.PlaybackFinished,Is.True);
            Assert.That(events.Single(e=>e.Code=="TRAJECTORY_COMPLETED").DurationSeconds,Is.EqualTo(10));
            Assert.That(source.Render(12).Sequence,Is.EqualTo(299));
            Assert.That(events.Count(e=>e.Code=="TRAJECTORY_COMPLETED"),Is.EqualTo(1));
        }
        [Test] public void BetweenSamplesContinuousStateInterpolatesAndDiscreteStateWaits()
        {
            var source=new SnapshotSource(Neutral,Registry(),20);var frames=Frames();
            source.PlayTrajectory(Trajectory,SceneRegistry.Hash(Trajectory),20);
            var mid=source.Render(20+149.5/30);
            Assert.That(mid.Sequence,Is.EqualTo(149));
            double a=(double)frames[149]["objects"][0]["position_m"][0],b=(double)frames[150]["objects"][0]["position_m"][0];
            Assert.That(X(mid),Is.EqualTo((float)((a+b)/2)).Within(1e-5));
            Assert.That((int)mid.Objects.Single().VisualState["card_face"],Is.Zero);
            Assert.That((int)source.Render(20+150/30d+1e-6).Objects.Single().VisualState["card_face"],Is.EqualTo(1));
        }
        [Test] public void CompletedFixedStepPlaybackNeverAutoConfirmsReset()
        {
            var source=new SnapshotSource(Neutral,Registry(),10);
            source.PlayTrajectory(Trajectory,SceneRegistry.Hash(Trajectory),10,FixedStepSchedule.DurationSeconds);
            Assert.That(source.ResetConfirmed,Is.False);
            Assert.That(source.ConfirmReset(source.Neutral,15),Is.False);
            source.Render(20);Assert.That(source.PlaybackFinished,Is.True);
            Assert.That(source.ConfirmReset(source.Neutral,20),Is.False);
            source.RestoreNeutral(20);
            Assert.That(source.ConfirmReset(source.Neutral,20),Is.True);
        }
        [TestCase("legacy","TRAJECTORY_LEGACY_SCHEDULE")]
        [TestCase("skipped_step","TRAJECTORY_SIM_STEP_SCHEDULE")]
        [TestCase("extra_step","TRAJECTORY_SIM_STEP_SCHEDULE")]
        [TestCase("first_step","TRAJECTORY_SIM_STEP_SCHEDULE")]
        [TestCase("one_physics_step_of_time","TRAJECTORY_SIM_TIME_SCHEDULE")]
        [TestCase("three_physics_steps_of_time","TRAJECTORY_SIM_TIME_SCHEDULE")]
        [TestCase("short","TRAJECTORY_SAMPLE_COUNT")]
        [TestCase("long","TRAJECTORY_SAMPLE_COUNT")]
        [TestCase("host_regression","TRAJECTORY_NONPROGRESSING")]
        [TestCase("other_duration","TRAJECTORY_PLAYBACK_DURATION")]
        [TestCase("not_neutral","TRAJECTORY_START_NOT_NEUTRAL")]
        public void TrajectoriesOffTheFixedStepContractAreRefusedWithAnExplicitCode(string damage,string code)
        {
            var frames=Frames();double? duration=null;
            switch(damage)
            {
                case "legacy": for(int i=0;i<frames.Count;i++) frames[i]["sim_step"]=i+1; break;
                case "skipped_step": for(int i=40;i<frames.Count;i++) frames[i]["sim_step"]=2*(i+1)+1; break;
                case "extra_step": for(int i=40;i<frames.Count;i++) frames[i]["sim_step"]=2*(i+1)+2; break;
                case "first_step": frames[0]["sim_step"]=1; break;
                case "one_physics_step_of_time": for(int i=40;i<frames.Count;i++) frames[i]["sim_time"]=(double)frames[i]["sim_time"]-1d/60; break;
                case "three_physics_steps_of_time": for(int i=40;i<frames.Count;i++) frames[i]["sim_time"]=(double)frames[i]["sim_time"]+1d/60; break;
                case "short": frames.RemoveAt(frames.Count-1); break;
                case "long":
                    var extra=(JObject)frames[frames.Count-1].DeepClone();extra["seq"]=(long)extra["seq"]+1;extra["sim_step"]=602;
                    extra["sim_time"]=(double)extra["sim_time"]+2d/60;extra["host_monotonic_ns"]=(ulong.Parse((string)extra["host_monotonic_ns"])+40000000UL).ToString();
                    frames.Add(extra); break;
                case "host_regression": frames[40]["host_monotonic_ns"]=frames[39]["host_monotonic_ns"]; break;
                case "other_duration": duration=9.5; break;
                case "not_neutral": frames[0]["objects"][0]["position_m"][0]=.01; break;
            }
            byte[] data=Encode(frames);var source=new SnapshotSource(Neutral,Registry(),0);var events=new List<SourceEvent>();source.Event+=events.Add;
            Assert.That(Assert.Throws<StateFault>(()=>source.PlayTrajectory(data,SceneRegistry.Hash(data),0,duration)).Message,Is.EqualTo(code));
            Assert.That(events.Single().Code,Is.EqualTo(code));
            Assert.That(source.ResetConfirmed,Is.False);Assert.That(source.ConfirmReset(source.Neutral,0),Is.False);
        }
    }
}
