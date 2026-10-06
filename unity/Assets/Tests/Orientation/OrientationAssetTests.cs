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
        string directory;SceneRegistry registry;byte[] neutral;JObject index;
        [SetUp] public void CreateExplicitSyntheticFixture()
        {
            directory=Path.Combine(FoundationBuild.RepositoryRoot,".local","synthetic-orientation-assets-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(directory);
            string[] names=Enumerable.Range(0,43).Select(i=>"synthetic_joint_"+i).ToArray();
            var obj=JObject.FromObject(new { position_m=new[]{0,0,0},rotation_xyzw=new[]{0,0,0,1},visible=true,enabled=true,collision_enabled=false,linear_velocity_m_s=new[]{0,0,0},angular_velocity_rad_s=new[]{0,0,0},state=new { card_face=0 } });
            var snapshot=JObject.FromObject(new { schema_version="1.0.0",scene_sha256=new string('a',64),fixed_steps=1,coordinate_frame="usd_world_rh_z_up_xyzw",state=new {
                robot=new { joint_names=names,joint_positions_rad=new double[43],joint_velocities_rad_s=new double[43],root_position_m=new[]{0,0,0},root_rotation_xyzw=new[]{0,0,0,1},root_linear_velocity_m_s=new[]{0,0,0},root_angular_velocity_rad_s=new[]{0,0,0} },
                objects=new Dictionary<string,JObject>{{"card",obj}},environment=new {materials=new{},lights=new{}},frames=Enumerable.Range(0,3).ToDictionary(i=>"link"+i,i=>new {position_m=new[]{0,0,0},rotation_xyzw=new[]{0,0,0,1}}) } });
            neutral=Encoding.UTF8.GetBytes(snapshot.ToString(Formatting.None));
            registry=new SceneRegistry("synthetic-station",new string('a',64),SceneRegistry.Hash(neutral),names,new Dictionary<string,string[]>{{"card",new[]{"card_face"}}},new[]{"card"});
            var text=new StringBuilder();var times=new List<ulong>();
            for(int i=0;i<300;i++)
            {
                ulong ns=(ulong)Math.Round(i*1e9/30);times.Add(ns);
                text.AppendLine(JObject.FromObject(new {version=2,kind="state",source_kind="live",station_id=registry.StationId,scene_sha256=registry.SceneHash,reset_snapshot_sha256=registry.SnapshotHash,session_id=new string('c',32),seq=i,host_monotonic_ns=ns.ToString(),sim_time=i/30d,sim_step=i,joint_names=names,joint_positions=new double[43],objects=new[]{new {id="card",position_m=new[]{0,0,0},rotation_xyzw=new[]{0,0,0,1},visible=true,enabled=true,state=new {card_face=i<150?0:1}}}}).ToString(Formatting.None));
            }
            byte[] trajectory=Encoding.UTF8.GetBytes(text.ToString());File.WriteAllBytes(Path.Combine(directory,"synthetic.ndjson"),trajectory);
            var intervals=times.Skip(1).Select((t,i)=>(t-times[i])/1e6).ToArray();var ordered=intervals.OrderBy(x=>x).ToArray();var rows=new JArray();
            foreach(string action in PublicCommands.Actions) rows.Add(JObject.FromObject(new {group="orientation",action,private_plan_key=action+"/"+(PublicCommands.ActionFamily(action)==0?"tray_A":"container_E"),target=PublicCommands.ActionFamily(action)==0?"tray_A":"container_E",
                capture=new {file="synthetic.ndjson",sha256=SceneRegistry.Hash(trajectory),frame_count=300,nominal_sample_hz=30,nominal_duration_seconds=10,measured_first_to_last_host_seconds=(times.Last()-times[0])/1e9,interval_ms=new {min=ordered[0],max=ordered.Last(),p95=ordered[(95*ordered.Length+99)/100-1],mean=intervals.Average()},capture_complete=true,timing_ok=true,actual_host_timestamps_preserved=true,time_compressed=false,timing_rule="synthetic unit fixture only"},execution=new{collision_reviewed=false,events=new object[0],execution_ok=true,failures=new object[0],grasp_contact_validated=false,nominal_duration_seconds=10,private_result=new{},robot_neutral_error_rad=0,sample_count=300,semantic_error=false},error=(string)null,reset_ok=true,replay_end_state_ok=true,expected_objects_sha256=new string('d',64)}));
            for(int i=0;i<32;i++)rows.Add(new JObject { ["group"]="execution" });
            index=JObject.FromObject(new {kind="actual_G1_kinematic_visualization",scene_sha256=registry.SceneHash,reset_snapshot_sha256=registry.SnapshotHash,planning_pairs=32,feasible_pairs=32,planning_reset_ok=true,protected_real_factory=new{before_sha256=new string('b',64),after_sha256=new string('b',64),factory_ran=false,rejected=32,unchanged=true},collision_reviewed=false,grasp_contact_validated=false,methodology_review_complete=false,recording_complete=true,rows,joint_names_sha256=SceneRegistry.Hash(Encoding.UTF8.GetBytes(string.Join("\n",names)+"\n")),station_id=registry.StationId,nominal_duration_seconds=10});
        }
        OrientationDemos Load(bool draft=true)
        { byte[] data=Encoding.UTF8.GetBytes(index.ToString(Formatting.None));File.WriteAllBytes(Path.Combine(directory,"index.private.json"),data);return OrientationDemos.Load(directory,SceneRegistry.Hash(data),registry,neutral,draft); }
        [Test] public void ValidSyntheticStreamsUseTheRealStateParserAndCommonDuration()
        { var library=Load();foreach(string action in PublicCommands.Actions){Assert.That(library[action].DurationSeconds,Is.EqualTo(10));Assert.That(library[action].FrameToleranceMs,Is.EqualTo(1000d/30));} }
        [Test] public void MissingDemoIndexFailsClosed()
        { Assert.Throws<OrientationFault>(()=>OrientationDemos.Load(Path.Combine(directory,"missing"),new string('a',64),registry,neutral,true)); }
        [Test] public void MixedDurationAndIncompleteOrCompressedCaptureAreRejected()
        {
            var capture=(JObject)index["rows"][0]["capture"];capture["nominal_duration_seconds"]=9;Assert.Throws<OrientationFault>(()=>Load());capture["nominal_duration_seconds"]=10;
            capture["capture_complete"]=false;Assert.Throws<OrientationFault>(()=>Load());capture["capture_complete"]=true;
            capture["time_compressed"]=true;Assert.Throws<OrientationFault>(()=>Load());
        }
        [Test] public void MetadataMustAgreeWithRetainedTimestamps()
        { index["rows"][0]["capture"]["measured_first_to_last_host_seconds"]=9;Assert.Throws<OrientationFault>(()=>Load()); }
        [Test] public void AudioPathsAreRejectedBeforeAnyAssetRead()
        { index["rows"][0]["capture"]["file"]="study.wav";var error=Assert.Throws<OrientationFault>(()=>Load());Assert.That(error.Message,Is.EqualTo("ORIENTATION_ASSET_PATH")); }
        [Test] public void ChangedStreamHashAndUnreviewedRealUseCannotRun()
        {
            Assert.Throws<OrientationFault>(()=>Load(false));
            File.AppendAllText(Path.Combine(directory,"synthetic.ndjson")," ");Assert.Throws<StateFault>(()=>Load());
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
            index["protected_real_factory"]["factory_ran"]=true;Assert.Throws<OrientationFault>(()=>Load());index["protected_real_factory"]["factory_ran"]=false;
            index["rows"][0]["private_plan_key"]="SCAN/container_E";Assert.Throws<OrientationFault>(()=>Load());index["rows"][0]["private_plan_key"]="ADD_ONE/tray_A";
            index["rows"][0]["expected_objects_sha256"]="not-a-hash";Assert.Throws<OrientationFault>(()=>Load());
        }
    }
}
