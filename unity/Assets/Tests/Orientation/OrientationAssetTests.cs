using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.Foundation.Editor;
using AcousticVocab.ResponsePanel;
using AcousticVocab.StateSources;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Orientation.Tests
{
    public sealed class OrientationAssetTests
    {
        // Written by the real #56 recorder: python -m isaac.demos.unity_fixture.
        // Synthetic identities only; its retained host clock overruns 10 s and
        // has a 740 ms stall, which is capture provenance and never a pace.
        static string Fixture => Path.Combine(FoundationBuild.RepositoryRoot,"tests","isaac","fixtures","fixed-step-demo");
        string directory;SceneRegistry registry;byte[] neutral;JObject index;
        static JObject Json(string name) => JObject.Parse(File.ReadAllText(Path.Combine(Fixture,name),Encoding.UTF8));
        [SetUp] public void CopyPythonRecorderFixture()
        {
            directory=Path.Combine(FoundationBuild.RepositoryRoot,".local","synthetic-orientation-assets-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(directory);
            var manifest=Json("manifest.json");
            Assert.That((bool)manifest["synthetic"] && !(bool)manifest["isaac_evidence"],Is.True);
            neutral=File.ReadAllBytes(Path.Combine(Fixture,"neutral.json"));
            registry=new SceneRegistry((string)manifest["station_id"],(string)manifest["scene_sha256"],SceneRegistry.Hash(neutral),
                manifest["joint_names"].Select(x=>(string)x),new Dictionary<string,string[]>{{"card",new[]{"card_face"}}},new string[0]);
            File.Copy(Path.Combine(Fixture,"000.ndjson"),Path.Combine(directory,"000.ndjson"));
            index=Json("index.private.json");
        }
        [TearDown] public void RemoveFixtureCopy() { try { Directory.Delete(directory,true); } catch(IOException) { } }
        JObject Capture(int row) => (JObject)index["rows"][row]["capture"];
        int FirstExecutionRow => ((JArray)index["rows"]).Select((row,i)=>(row,i)).First(x=>(string)x.row["group"]=="execution").i;
        OrientationDemos Load(bool draft=true)
        { byte[] data=Encoding.UTF8.GetBytes(index.ToString(Formatting.None));File.WriteAllBytes(Path.Combine(directory,"index.private.json"),data);return OrientationDemos.Load(directory,SceneRegistry.Hash(data),registry,neutral,draft); }
        string Code(Func<OrientationDemos> load) => Assert.Throws<OrientationFault>(()=>load()).Message;
        string Rewrite(string name,Func<int,JObject,JObject> change)
        {
            // Re-encode the recorder's frames (bytes differ only where changed) and
            // point the first orientation row at the result with its new hash.
            var lines=File.ReadAllLines(Path.Combine(Fixture,"000.ndjson")).Where(x=>x.Length>0).Select((x,i)=>change(i,JObject.Parse(x)).ToString(Formatting.None));
            byte[] bytes=Encoding.UTF8.GetBytes(string.Join("\n",lines)+"\n");File.WriteAllBytes(Path.Combine(directory,name),bytes);
            Capture(0)["file"]=name;Capture(0)["sha256"]=SceneRegistry.Hash(bytes);return name;
        }

        [Test] public void PythonRecorderLibraryLoadsWithOneFixedStepDuration()
        {
            var library=Load();
            foreach(string action in PublicCommands.Actions)
            {
                Assert.That(library[action].DurationSeconds,Is.EqualTo(10));Assert.That(library[action].FrameToleranceMs,Is.EqualTo(1000d/30));
                Assert.That(library[action].SampleCount,Is.EqualTo(300));Assert.That(library[action].RecordedPhysicsSteps,Is.EqualTo(600));
            }
            // The accepted capture overran 10 s of host time with a stall far above
            // one frame: provenance only, so it neither qualifies nor paces a demo.
            Assert.That((double)Capture(0)["capture_host_seconds"],Is.GreaterThan(10));
            Assert.That((double)Capture(0)["capture_interval_ms"]["max"],Is.GreaterThan(250));
        }
        [Test] public void EveryDemoCompletesAtTheCommonDurationOnTheHostClock()
        {
            var library=Load();
            foreach(string action in PublicCommands.Actions)
            {
                var demo=library[action];var source=new SnapshotSource(neutral,registry,50);var completed=new List<SourceEvent>();
                source.Event+=e=>{ if(e.Code=="TRAJECTORY_COMPLETED") completed.Add(e); };
                source.PlayTrajectory(demo.CopyBytes(),demo.Sha256,50,demo.DurationSeconds);
                double now=50;int frame=0;
                while(completed.Count==0) { now=50+(++frame)/72d;var shown=source.Render(now);Assert.That(shown.Sequence,Is.EqualTo(FixedStepSchedule.PlaybackIndex(now-50)??299)); }
                double observedMs=completed.Single().DurationSeconds.Value*1000;
                Assert.That(observedMs,Is.GreaterThanOrEqualTo(demo.DurationSeconds*1000));
                Assert.That(observedMs-demo.DurationSeconds*1000,Is.LessThanOrEqualTo(demo.FrameToleranceMs),action);
            }
        }
        [Test] public void MissingDemoIndexFailsClosed()
        { Assert.Throws<OrientationFault>(()=>OrientationDemos.Load(Path.Combine(directory,"missing"),new string('a',64),registry,neutral,true)); }
        [Test] public void LegacyHostPacedCaptureRecordIsRefusedExplicitly()
        {
            var legacy=Json("legacy-capture.json");
            index["rows"][0]["capture"]=legacy.DeepClone();
            Assert.That(Code(()=>Load()),Is.EqualTo("ORIENTATION_DEMO_LEGACY_CAPTURE_FORMAT"));
            index=Json("index.private.json");index["rows"][FirstExecutionRow]["capture"]=legacy;
            Assert.That(Code(()=>Load()),Is.EqualTo("ORIENTATION_DEMO_LEGACY_CAPTURE_FORMAT"));
        }
        [TestCase("extra")][TestCase("missing")][TestCase("mixed_legacy")][TestCase("schedule_extra")][TestCase("interval_missing")]
        public void CaptureFieldSetsAreExact(string damage)
        {
            var capture=Capture(0);
            if(damage=="extra") capture["unknown"]=true;
            if(damage=="missing") capture.Remove("playback_clock");
            if(damage=="mixed_legacy") capture["timing_ok"]=true;
            if(damage=="schedule_extra") capture["schedule"]["sim_time_clock"]=true;
            if(damage=="interval_missing") ((JObject)capture["capture_interval_ms"]).Remove("p95");
            Assert.That(Code(()=>Load()),Is.EqualTo("ORIENTATION_FIELDS"));
        }
        [TestCase("kind","sim_time")][TestCase("sample_count",299)][TestCase("sample_hz",60)][TestCase("physics_steps_per_sample",3)]
        [TestCase("physics_dt_seconds",.02)][TestCase("total_physics_steps",602)][TestCase("recorded_duration_seconds",9.0)][TestCase("total_physics_steps",600.0)]
        public void ScheduleOtherThanTheFixedSimStepContractIsRefused(string key,object value)
        { Capture(0)["schedule"][key]=JToken.FromObject(value);Assert.That(Code(()=>Load()),Is.EqualTo("ORIENTATION_DEMO_SCHEDULE")); }
        [TestCase(0,"recorded_physics_steps",598)][TestCase(-1,"recorded_physics_steps",602)][TestCase(0,"recorded_duration_seconds",9.9666)]
        [TestCase(0,"frame_count",299)][TestCase(-1,"frame_count",301)][TestCase(0,"nominal_duration_seconds",9)][TestCase(0,"nominal_sample_hz",60)][TestCase(0,"time_compressed",true)]
        public void AnyItemWithADifferentDurationIsRefused(int row,string key,object value)
        { Capture(row<0?FirstExecutionRow+20:row)[key]=JToken.FromObject(value);Assert.That(Code(()=>Load()),Is.EqualTo("ORIENTATION_DEMO_DURATION")); }
        [TestCase("capture_complete")][TestCase("schedule_ok")][TestCase("actual_host_timestamps_preserved")]
        public void IncompleteOrOffScheduleCaptureIsUnqualified(string key)
        {
            Capture(0)[key]=false;Assert.That(Code(()=>Load()),Is.EqualTo("ORIENTATION_DEMO_UNQUALIFIED"));
            index=Json("index.private.json");Capture(FirstExecutionRow+5)[key]=false;Assert.That(Code(()=>Load()),Is.EqualTo("ORIENTATION_DEMO_UNQUALIFIED"));
        }
        [Test] public void SimTimeOrCaptureStampsCannotBeThePlaybackClock()
        { Capture(0)["playback_clock"]="sim_time";Assert.That(Code(()=>Load()),Is.EqualTo("ORIENTATION_DEMO_PLAYBACK_CLOCK")); }
        [Test] public void MetadataMustAgreeWithRetainedTimestamps()
        {
            Capture(0)["capture_host_seconds"]=9;Assert.That(Code(()=>Load()),Is.EqualTo("ORIENTATION_DEMO_TIMING_METADATA"));
            index=Json("index.private.json");Capture(0)["capture_interval_ms"]["max"]=40;Assert.That(Code(()=>Load()),Is.EqualTo("ORIENTATION_DEMO_TIMING_METADATA"));
        }
        [Test] public void LegacyOrOffScheduleFramesAreRefusedByTheRealPlayer()
        {
            Rewrite("legacy.ndjson",(i,f)=>{f["sim_step"]=i+1;return f;});
            Assert.That(Assert.Throws<StateFault>(()=>Load()).Message,Is.EqualTo("TRAJECTORY_LEGACY_SCHEDULE"));
            index=Json("index.private.json");Rewrite("skipped.ndjson",(i,f)=>{if(i>=10)f["sim_step"]=2*(i+1)+1;return f;});
            Assert.That(Assert.Throws<StateFault>(()=>Load()).Message,Is.EqualTo("TRAJECTORY_SIM_STEP_SCHEDULE"));
            index=Json("index.private.json");Rewrite("dt.ndjson",(i,f)=>{if(i>=10)f["sim_time"]=(double)f["sim_time"]+1d/60;return f;});
            Assert.That(Assert.Throws<StateFault>(()=>Load()).Message,Is.EqualTo("TRAJECTORY_SIM_TIME_SCHEDULE"));
        }
        [Test] public void AudioPathsAreRejectedBeforeAnyAssetRead()
        { Capture(0)["file"]="study.wav";var error=Assert.Throws<OrientationFault>(()=>Load());Assert.That(error.Message,Is.EqualTo("ORIENTATION_ASSET_PATH")); }
        [Test] public void ChangedStreamHashAndUnreviewedRealUseCannotRun()
        {
            Assert.Throws<OrientationFault>(()=>Load(false));
            File.AppendAllText(Path.Combine(directory,"000.ndjson")," ");Assert.Throws<StateFault>(()=>Load());
        }
        [Test] public void EightDistinctActionsAreRequired()
        { index["rows"][1]["action"]=index["rows"][0]["action"];Assert.Throws<OrientationFault>(()=>Load()); }
        [Test] public void FailedExecutionCannotBeHiddenBehindSuccessfulCaptureFlags()
        {
            var execution=(JObject)index["rows"][0]["execution"];
            execution["execution_ok"]=false;Assert.Throws<OrientationFault>(()=>Load());execution["execution_ok"]=true;
            execution["semantic_error"]=true;Assert.Throws<OrientationFault>(()=>Load());execution["semantic_error"]=false;
            execution["failures"]=new JArray("synthetic failure");Assert.Throws<OrientationFault>(()=>Load());execution["failures"]=new JArray();
            execution["robot_neutral_error_rad"]=.00101;Assert.Throws<OrientationFault>(()=>Load());execution["robot_neutral_error_rad"]=0;
            execution["sample_count"]=299;Assert.Throws<OrientationFault>(()=>Load());
        }
        [Test] public void ProtectedGuardPlanIdentityAndExpectedStateHashAreRequired()
        {
            string key=(string)index["rows"][0]["private_plan_key"];
            index["protected_real_factory"]["factory_ran"]=true;Assert.Throws<OrientationFault>(()=>Load());index["protected_real_factory"]["factory_ran"]=false;
            index["rows"][0]["private_plan_key"]="SCAN/container_E";Assert.Throws<OrientationFault>(()=>Load());index["rows"][0]["private_plan_key"]=key;
            index["rows"][0]["expected_objects_sha256"]="not-a-hash";Assert.Throws<OrientationFault>(()=>Load());
        }
        [Test] public void IncompleteRecordingIsUnqualifiedBeforeAnyRowIsRead()
        {
            index["recording_complete"]=false;index["rows"][0]["capture"]=Json("legacy-capture.json");
            Assert.That(Code(()=>Load()),Is.EqualTo("ORIENTATION_DEMO_UNQUALIFIED"));
        }
    }
}
