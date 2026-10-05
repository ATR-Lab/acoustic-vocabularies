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
        internal static byte[] Read(string path,long maximum) { var file=new FileInfo(path);OrientationPlan.Require(file.Exists && file.Length>0 && file.Length<=maximum,"ORIENTATION_ASSET_MISSING_OR_LARGE");return File.ReadAllBytes(path); }
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
        internal OrientationDemo(string action,string target,string hash,byte[] data,double duration,double hz)
        { Action=action;Target=target;Sha256=hash;bytes=(byte[])data.Clone();DurationSeconds=duration;FrameToleranceMs=1000/hz; }
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
            OrientationPlan.Keys(index,"kind","scene_sha256","reset_snapshot_sha256","planning_pairs","feasible_pairs","planning_reset_ok","collision_reviewed","grasp_contact_validated","methodology_review_complete","recording_complete","rows","joint_names_sha256","station_id","nominal_duration_seconds");
            OrientationPlan.Require((string)index["kind"]=="actual_G1_kinematic_visualization" && (string)index["scene_sha256"]==registry.SceneHash && (string)index["reset_snapshot_sha256"]==registry.SnapshotHash && (string)index["station_id"]==registry.StationId,"ORIENTATION_DEMO_IDENTITY");
            string jointHash=SceneRegistry.Hash(Encoding.UTF8.GetBytes(string.Join("\n",registry.JointNames)+"\n"));
            OrientationPlan.Require((string)index["joint_names_sha256"]==jointHash,"ORIENTATION_DEMO_JOINTS");
            True(index["recording_complete"]);True(index["planning_reset_ok"]);
            OrientationPlan.Require(OrientationPlan.Number(index["planning_pairs"])==32 && OrientationPlan.Number(index["feasible_pairs"])==32,"ORIENTATION_DEMO_INCOMPLETE");
            if(!engineeringDraft) { True(index["collision_reviewed"]);True(index["methodology_review_complete"]); }
            OrientationPlan.Require(index["rows"] is JArray rows && rows.Count==40,"ORIENTATION_DEMO_INCOMPLETE");
            double duration=OrientationPlan.Number(index["nominal_duration_seconds"]);OrientationPlan.Require(duration==10,"ORIENTATION_DEMO_DURATION");
            var results=new Dictionary<string,OrientationDemo>(StringComparer.Ordinal);long total=0;
            foreach(var row in ((JArray)index["rows"]).OfType<JObject>().Where(x=>(string)x["group"]=="orientation"))
            {
                OrientationPlan.Keys(row,"group","action","target","capture","execution","error","reset_ok","replay_end_state_ok","expected_objects_sha256");
                string action=OrientationPlan.Id(row["action"]),target=OrientationPlan.Id(row["target"]);
                OrientationPlan.Require(PublicCommands.ActionFamily(action)>=0 && target==(PublicCommands.ActionFamily(action)==0?"tray_A":"container_E") && !results.ContainsKey(action) && row["error"].Type==JTokenType.Null,"ORIENTATION_DEMO_PAIR");
                True(row["reset_ok"]);True(row["replay_end_state_ok"]);
                var capture=row["capture"] as JObject;
                OrientationPlan.Keys(capture,"file","sha256","frame_count","nominal_sample_hz","nominal_duration_seconds","measured_first_to_last_host_seconds","interval_ms","capture_complete","timing_ok","actual_host_timestamps_preserved","time_compressed","timing_rule");
                True(capture["capture_complete"]);True(capture["timing_ok"]);True(capture["actual_host_timestamps_preserved"]);
                OrientationPlan.Require(capture["time_compressed"].Type==JTokenType.Boolean && !(bool)capture["time_compressed"] && OrientationPlan.Number(capture["frame_count"])==300 && OrientationPlan.Number(capture["nominal_sample_hz"])==30 && OrientationPlan.Number(capture["nominal_duration_seconds"])==duration,"ORIENTATION_DEMO_DURATION");
                double span=OrientationPlan.Number(capture["measured_first_to_last_host_seconds"]);OrientationPlan.Require(span>0 && span<=duration,"ORIENTATION_DEMO_DURATION");
                string file=OrientationSetup.Basename(capture["file"],"ndjson"),hash=OrientationPlan.Text(capture["sha256"],64);
                byte[] data=OrientationSetup.Read(Path.Combine(directory,file),64*1024*1024);total+=data.Length;OrientationPlan.Require(total<=128*1024*1024,"ORIENTATION_DEMO_MEMORY_BUDGET");
                OrientationPlan.Require(OrientationSetup.Utf8(data).Split('\n').Count(line=>!string.IsNullOrWhiteSpace(line))==300,"ORIENTATION_DEMO_FRAME_COUNT");
                // Validate every retained timestamp/frame against the real #62 parser and exact neutral before showing any meaning.
                var source=new SnapshotSource(neutral,registry,0);source.PlayTrajectory(data,hash,0,duration);
                results.Add(action,new OrientationDemo(action,target,hash,data,duration,30));
            }
            OrientationPlan.Require(results.Count==8 && PublicCommands.Actions.All(results.ContainsKey),"ORIENTATION_DEMO_MISSING");
            return new OrientationDemos(results);
        }
        static void True(JToken token) => OrientationPlan.Require(token!=null&&token.Type==JTokenType.Boolean&&(bool)token,"ORIENTATION_DEMO_UNQUALIFIED");
    }
}
