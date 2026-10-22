using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using AcousticVocab.Foundation;
using AcousticVocab.ResponsePanel;
using AcousticVocab.StateSources;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.Orientation
{
    public sealed class OrientationSetup
    {
        public OrientationPlan Plan { get; private set; }
        public string PlanHash { get; private set; }
        public string DemoDirectory { get; private set; }
        public string DemoIndexHash { get; private set; }
        public static OrientationSetup Load(string directory,string protocol)
        {
            var v=StationConfig.ParseStrict(Utf8(Read(Path.Combine(directory,"orientation.local.json"),65536)));
            OrientationPlan.Keys(v,"version","configuration_status","protocol_version","plan_file","plan_sha256","demo_directory","demo_index_sha256","allow_engineering_draft");
            OrientationPlan.Require(v["version"].Type==JTokenType.Integer && (int)v["version"]==1 && (string)v["configuration_status"]=="provisioned_engineering" && (string)v["protocol_version"]==protocol,"ORIENTATION_CONFIG_IDENTITY");
            string planFile=Basename(v["plan_file"],"json"),planHash=OrientationPlan.Text(v["plan_sha256"],64),indexHash=OrientationPlan.Text(v["demo_index_sha256"],64);
            OrientationPlan.Require(OrientationPlan.Hash(planHash)&&OrientationPlan.Hash(indexHash)&&v["allow_engineering_draft"].Type==JTokenType.Boolean,"ORIENTATION_CONFIG_HASH");
            var bytes=Read(Path.Combine(directory,planFile),65536);OrientationPlan.Require(SceneRegistry.Hash(bytes)==planHash,"ORIENTATION_PLAN_HASH");
            var plan=OrientationPlan.Parse(Utf8(bytes),protocol);
            OrientationPlan.Require(!plan.EngineeringDraft || (bool)v["allow_engineering_draft"],"ORIENTATION_CONTENT_REVIEW_REQUIRED");
            string folder=OrientationPlan.Id(v["demo_directory"]);OrientationPlan.Require(!folder.Contains(".."),"ORIENTATION_ASSET_PATH");
            return new OrientationSetup { Plan=plan,PlanHash=planHash,DemoIndexHash=indexHash,DemoDirectory=Path.Combine(directory,folder) };
        }
        internal static string Basename(JToken token,string extension)
        { string name=OrientationPlan.Text(token,110);OrientationPlan.Require(Regex.IsMatch(name,@"\A[A-Za-z0-9][A-Za-z0-9._-]{0,95}\."+extension+@"\z")&&!name.Contains(".."),"ORIENTATION_ASSET_PATH");return name; }
        internal static byte[] Read(string path,long maximum)
        {
            var file=new FileInfo(path);OrientationPlan.Require(file.Exists && file.Length>0 && file.Length<=maximum,"ORIENTATION_ASSET_MISSING_OR_LARGE");
            for(FileSystemInfo item=file;item!=null;item=item is FileInfo f?f.Directory:((DirectoryInfo)item).Parent)
                OrientationPlan.Require((item.Attributes&FileAttributes.ReparsePoint)==0,"ORIENTATION_ASSET_REPARSE_POINT");
            return File.ReadAllBytes(path);
        }
        internal static string Utf8(byte[] bytes) { try{return new UTF8Encoding(false,true).GetString(bytes);}catch{throw new OrientationFault("ORIENTATION_ENCODING");} }
    }
    public sealed class OrientationDemo
    {
        readonly byte[] bytes;
        public string Action { get; }
        public string Target { get; }
        public string Sha256 { get; }
        public double DurationSeconds { get; }
        public double FrameToleranceMs { get; }
        public int SampleCount { get; }
        public int RecordedPhysicsSteps { get; }
        internal OrientationDemo(string action,string target,string hash,byte[] data,double duration,double hz,int samples,int steps)
        { Action=action;Target=target;Sha256=hash;bytes=(byte[])data.Clone();DurationSeconds=duration;FrameToleranceMs=1000/hz;SampleCount=samples;RecordedPhysicsSteps=steps; }
        public byte[] CopyBytes() => (byte[])bytes.Clone();
    }
    public sealed class OrientationDemos
    {
        readonly Dictionary<string,OrientationDemo> demos;
        public OrientationDemo this[string action] => demos.TryGetValue(action,out var result)?result:throw new OrientationFault("ORIENTATION_DEMO_MISSING");
        OrientationDemos(Dictionary<string,OrientationDemo> demos) { this.demos=demos; }
        // The loader is limited to the #56 index and hashed public-v2 NDJSON. It never opens a learner package or audio asset.
        public static OrientationDemos Load(string directory,string indexHash,SceneRegistry registry,byte[] neutral,bool engineeringDraft)
        {
            byte[] bytes=OrientationSetup.Read(Path.Combine(directory,"index.private.json"),1024*1024);
            OrientationPlan.Require(OrientationPlan.Hash(indexHash)&&SceneRegistry.Hash(bytes)==indexHash,"ORIENTATION_DEMO_INDEX_HASH");
            var index=StationConfig.ParseStrict(OrientationSetup.Utf8(bytes));
            OrientationPlan.Keys(index,"kind","scene_sha256","reset_snapshot_sha256","planning_pairs","feasible_pairs","planning_reset_ok","protected_real_factory","collision_reviewed","grasp_contact_validated","methodology_review_complete","recording_complete","rows","joint_names_sha256","station_id","nominal_duration_seconds");
            OrientationPlan.Require((string)index["kind"]=="actual_G1_kinematic_visualization" && (string)index["scene_sha256"]==registry.SceneHash && (string)index["reset_snapshot_sha256"]==registry.SnapshotHash && (string)index["station_id"]==registry.StationId,"ORIENTATION_DEMO_IDENTITY");
            string jointHash=SceneRegistry.Hash(Encoding.UTF8.GetBytes(string.Join("\n",registry.JointNames)+"\n"));
            OrientationPlan.Require((string)index["joint_names_sha256"]==jointHash,"ORIENTATION_DEMO_JOINTS");
            True(index["recording_complete"]);True(index["planning_reset_ok"]);
            var guard=index["protected_real_factory"] as JObject;
            OrientationPlan.Keys(guard,"before_sha256","after_sha256","factory_ran","rejected","unchanged");
            True(guard["unchanged"]);
            OrientationPlan.Require(guard["factory_ran"].Type==JTokenType.Boolean && !(bool)guard["factory_ran"] && OrientationPlan.Number(guard["rejected"])==32 && OrientationPlan.Hash((string)guard["before_sha256"]) && (string)guard["before_sha256"]==(string)guard["after_sha256"],"ORIENTATION_DEMO_PROTECTED_GUARD");
            OrientationPlan.Require(OrientationPlan.Number(index["planning_pairs"])==32 && OrientationPlan.Number(index["feasible_pairs"])==32,"ORIENTATION_DEMO_INCOMPLETE");
            if(!engineeringDraft) { True(index["collision_reviewed"]);True(index["methodology_review_complete"]); }
            OrientationPlan.Require(index["rows"] is JArray rows && rows.Count==40,"ORIENTATION_DEMO_INCOMPLETE");
            double duration=OrientationPlan.Number(index["nominal_duration_seconds"]);OrientationPlan.Require(duration==FixedStepSchedule.DurationSeconds,"ORIENTATION_DEMO_DURATION");
            // Suite-wide fixed sim-step duration (recording.validate_suite): all 8
            // orientation and 32 execution captures, before any trajectory is read.
            var steps=new HashSet<long>();
            foreach(var token in (JArray)index["rows"])
            {
                var row=token as JObject;
                OrientationPlan.Require(row!=null && (row["group"]?.Type==JTokenType.String) && ((string)row["group"]=="orientation" || (string)row["group"]=="execution"),"ORIENTATION_DEMO_INCOMPLETE");
                steps.Add(FixedStepCapture(row["capture"],duration));
            }
            OrientationPlan.Require(steps.Count==1,"ORIENTATION_DEMO_DURATION_MISMATCH");
            var results=new Dictionary<string,OrientationDemo>(StringComparer.Ordinal);long total=0;
            foreach(var row in ((JArray)index["rows"]).OfType<JObject>().Where(x=>(string)x["group"]=="orientation"))
            {
                OrientationPlan.Keys(row,"group","action","target","private_plan_key","capture","execution","error","reset_ok","replay_end_state_ok","expected_objects_sha256");
                string action=OrientationPlan.Id(row["action"]),target=OrientationPlan.Id(row["target"]);
                OrientationPlan.Require(PublicCommands.ActionFamily(action)>=0 && target==(PublicCommands.ActionFamily(action)==0?"tray_A":"container_E") && !results.ContainsKey(action) && row["error"].Type==JTokenType.Null,"ORIENTATION_DEMO_PAIR");
                OrientationPlan.Require((string)row["private_plan_key"]==action+"/"+target && OrientationPlan.Hash((string)row["expected_objects_sha256"]),"ORIENTATION_DEMO_PAIR");
                True(row["reset_ok"]);True(row["replay_end_state_ok"]);
                var execution=row["execution"] as JObject;
                OrientationPlan.Keys(execution,"collision_reviewed","events","execution_ok","failures","grasp_contact_validated","nominal_duration_seconds","private_result","robot_neutral_error_rad","sample_count","semantic_error");
                True(execution["execution_ok"]);
                OrientationPlan.Require(execution["semantic_error"].Type==JTokenType.Boolean && !(bool)execution["semantic_error"] && execution["failures"] is JArray failures && failures.Count==0 && execution["events"] is JArray && execution["private_result"] is JObject && OrientationPlan.Number(execution["sample_count"])==300 && OrientationPlan.Number(execution["nominal_duration_seconds"])==duration && OrientationPlan.Number(execution["robot_neutral_error_rad"])>=0 && OrientationPlan.Number(execution["robot_neutral_error_rad"])<=.001,"ORIENTATION_DEMO_EXECUTION_FAILED");
                OrientationPlan.Require(execution["collision_reviewed"].Type==JTokenType.Boolean && execution["grasp_contact_validated"].Type==JTokenType.Boolean,"ORIENTATION_DEMO_EXECUTION_FLAGS");
                if(!engineeringDraft)True(execution["collision_reviewed"]);
                var capture=(JObject)row["capture"];
                string file=OrientationSetup.Basename(capture["file"],"ndjson"),hash=OrientationPlan.Text(capture["sha256"],64);
                byte[] data=OrientationSetup.Read(Path.Combine(directory,file),64*1024*1024);total+=data.Length;OrientationPlan.Require(total<=128*1024*1024,"ORIENTATION_DEMO_MEMORY_BUDGET");
                string[] lines=OrientationSetup.Utf8(data).Split('\n').Where(line=>!string.IsNullOrWhiteSpace(line)).ToArray();
                OrientationPlan.Require(lines.Length==FixedStepSchedule.SampleCount,"ORIENTATION_DEMO_FRAME_COUNT");
                var frames=lines.Select(line=>StateParser.Parse(line,registry)).ToArray();
                // Host stamps are retained capture provenance only. They must be
                // the recorder's and agree with its summary, but capture is unpaced:
                // their span and gaps neither pace playback nor qualify a demo.
                var intervals=new List<double>();
                for(int i=1;i<frames.Length;i++) { OrientationPlan.Require(frames[i].PublishedNs>frames[i-1].PublishedNs,"ORIENTATION_DEMO_TIMESTAMPS");intervals.Add((frames[i].PublishedNs-frames[i-1].PublishedNs)/1e6); }
                double span=OrientationPlan.Number(capture["capture_host_seconds"]);
                OrientationPlan.Require(span>0 && Math.Abs((frames[frames.Length-1].PublishedNs-frames[0].PublishedNs)/1e9-span)<=1e-9,"ORIENTATION_DEMO_TIMING_METADATA");
                var stats=capture["capture_interval_ms"] as JObject;OrientationPlan.Keys(stats,"min","max","p95","mean");var ordered=intervals.OrderBy(x=>x).ToArray();
                var expected=new[]{ordered[0],ordered[ordered.Length-1],ordered[(95*ordered.Length+99)/100-1],intervals.Average()};
                int stat=0;foreach(string key in new[]{"min","max","p95","mean"})OrientationPlan.Require(Math.Abs(OrientationPlan.Number(stats[key])-expected[stat++])<=1e-6,"ORIENTATION_DEMO_TIMING_METADATA");
                // The real #62 player re-checks every frame, the fixed sim-step
                // schedule (sim_step 2..600, matching the 600 recorded steps above)
                // and the exact neutral start before any meaning is shown.
                var source=new SnapshotSource(neutral,registry,0);source.PlayTrajectory(data,hash,0,duration);
                results.Add(action,new OrientationDemo(action,target,hash,data,duration,FixedStepSchedule.SampleHz,FixedStepSchedule.SampleCount,FixedStepSchedule.TotalPhysicsSteps));
            }
            OrientationPlan.Require(results.Count==8 && PublicCommands.Actions.All(results.ContainsKey),"ORIENTATION_DEMO_MISSING");
            return new OrientationDemos(results);
        }
        static void True(JToken token) => OrientationPlan.Require(token!=null&&token.Type==JTokenType.Boolean&&(bool)token,"ORIENTATION_DEMO_UNQUALIFIED");
        // Per-row capture record written by isaac/demos/recording.py TrajectoryWriter.close.
        static readonly string[] CaptureFields={"file","sha256","frame_count","nominal_sample_hz","nominal_duration_seconds","schedule","recorded_physics_steps","recorded_duration_seconds","capture_complete","schedule_ok","capture_host_seconds","capture_interval_ms","actual_host_timestamps_preserved","time_compressed","playback_clock","timing_rule"};
        static readonly string[] ScheduleFields={"kind","sample_count","sample_hz","physics_steps_per_sample","physics_dt_seconds","total_physics_steps","recorded_duration_seconds"};
        // The pre-fixed-step capture screened host spans and was paced by host stamps.
        static readonly string[] LegacyCaptureFields={"measured_first_to_last_host_seconds","interval_ms","timing_ok"};
        static long Count(JToken token) { OrientationPlan.Require(token!=null && token.Type==JTokenType.Integer,"ORIENTATION_DEMO_SCHEDULE");return (long)token; }
        // Returns the recorded physics steps after checking the exact fixed-step contract.
        static long FixedStepCapture(JToken token,double duration)
        {
            var capture=token as JObject;
            OrientationPlan.Require(capture!=null,"ORIENTATION_FIELDS");
            OrientationPlan.Require(capture.Property("schedule")!=null || !LegacyCaptureFields.Any(key=>capture.Property(key)!=null),"ORIENTATION_DEMO_LEGACY_CAPTURE_FORMAT");
            OrientationPlan.Keys(capture,CaptureFields);
            True(capture["capture_complete"]);True(capture["schedule_ok"]);True(capture["actual_host_timestamps_preserved"]);
            OrientationPlan.Require(capture["time_compressed"].Type==JTokenType.Boolean && !(bool)capture["time_compressed"],"ORIENTATION_DEMO_DURATION");
            OrientationPlan.Require(OrientationPlan.Text(capture["playback_clock"],80)==FixedStepSchedule.PlaybackClock,"ORIENTATION_DEMO_PLAYBACK_CLOCK");
            OrientationPlan.Text(capture["timing_rule"],1000);
            var schedule=capture["schedule"] as JObject;
            OrientationPlan.Keys(schedule,ScheduleFields);
            OrientationPlan.Require(OrientationPlan.Text(schedule["kind"],80)==FixedStepSchedule.Kind && Count(schedule["sample_count"])==FixedStepSchedule.SampleCount &&
                Count(schedule["sample_hz"])==FixedStepSchedule.SampleHz && Count(schedule["physics_steps_per_sample"])==FixedStepSchedule.PhysicsStepsPerSample &&
                Count(schedule["total_physics_steps"])==FixedStepSchedule.TotalPhysicsSteps &&
                Math.Abs(OrientationPlan.Number(schedule["physics_dt_seconds"])-FixedStepSchedule.PhysicsDtSeconds)<=1e-12 &&
                OrientationPlan.Number(schedule["recorded_duration_seconds"])==duration,"ORIENTATION_DEMO_SCHEDULE");
            long steps=Count(capture["recorded_physics_steps"]);
            OrientationPlan.Require(steps==FixedStepSchedule.TotalPhysicsSteps && Math.Abs(OrientationPlan.Number(capture["recorded_duration_seconds"])-duration)<=1e-9 &&
                Count(capture["frame_count"])==FixedStepSchedule.SampleCount && OrientationPlan.Number(capture["nominal_sample_hz"])==FixedStepSchedule.SampleHz &&
                OrientationPlan.Number(capture["nominal_duration_seconds"])==duration,"ORIENTATION_DEMO_DURATION");
            return steps;
        }
    }
}
