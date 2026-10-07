using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.ViewCapture
{
    public sealed class PlanPose
    {
        public string Id{get;}public HeadPose Pose{get;}
        internal PlanPose(string id,HeadPose pose){Id=id;Pose=pose;}
    }

    // C# mirror of tools/view_leakage.py load_plan. The offline verifier stays
    // authoritative; this refuses a plan it would refuse before any capture.
    public sealed class ViewCapturePlan
    {
        public const double MaxHeadPositionToleranceM=.001;
        public static readonly double MaxHeadOrientationToleranceRad=Math.PI/180*.5;
        static readonly string[] PoseIds={"center","yaw_left","yaw_right","pitch_up","pitch_down"};
        static readonly (string Key,double Max)[] StateBounds={("joint_rad",Math.PI/180*.5),("position_m",.001),("orientation_rad",Math.PI/180*.5),
            ("linear_velocity_m_s",1e-5),("angular_velocity_rad_s",1e-5),("environment_absolute",1e-7)};
        public string Sha256{get;private set;}public string StationId{get;private set;}public string SourceKind{get;private set;}public string CaptureSurface{get;private set;}
        public string BuildSha256{get;private set;}public string SceneSha256{get;private set;}public string PanelStateSha256{get;private set;}public string SnapshotSha256{get;private set;}
        public int Width{get;private set;}public int Height{get;private set;}
        public double HeadPositionToleranceM{get;private set;}public double HeadOrientationToleranceRad{get;private set;}public double MaxStateArrivalAgeMs{get;private set;}
        public IReadOnlyList<PlanPose> Poses{get;private set;}
        public int CapturesRequired=>Poses.Count*LegalPairs.All.Count;
        ViewCapturePlan(){}

        static readonly Regex Hash=new Regex(@"\A[0-9a-f]{64}\z"),Id=new Regex(@"\A[A-Za-z0-9][A-Za-z0-9._-]{0,79}\z");
        static void Need(bool ok,string code)=>ViewCaptureFault.Require(ok,code);
        static bool Integer(JToken t)=>t!=null&&t.Type==JTokenType.Integer;
        static double Finite(JToken t,double max,string code)
        {
            Need(t!=null&&(t.Type==JTokenType.Integer||t.Type==JTokenType.Float),code);double v=(double)t;
            Need(!double.IsNaN(v)&&!double.IsInfinity(v)&&v>=0&&v<=max,code);return v;
        }
        static double[] Vector(JToken t,int n,string code)
        {
            Need(t is JArray a&&a.Count==n&&a.All(x=>x.Type==JTokenType.Integer||x.Type==JTokenType.Float),code);
            var v=((JArray)t).Select(x=>(double)x).ToArray();Need(v.All(x=>!double.IsNaN(x)&&!double.IsInfinity(x)),code);return v;
        }
        static string ReadHash(JToken t,string code){Need(t!=null&&t.Type==JTokenType.String&&Hash.IsMatch((string)t),code);return(string)t;}

        // Reads the independently pinned plan from a private local file. The
        // snapshot reference is resolved next to the plan, never elsewhere.
        public static ViewCapturePlan Load(string path,string rawPin)
        {
            Need(rawPin!=null&&Hash.IsMatch(rawPin),"PLAN_SHA256_REQUIRED");
            Need(path!=null&&Path.IsPathRooted(path)&&!path.StartsWith("\\\\")&&!path.StartsWith("//"),"PLAN_LOCAL_ABSOLUTE_PATH");
            CaptureJson.NoLinks(path);var info=new FileInfo(path);Need(info.Exists&&info.Length>0&&info.Length<=65536,"PLAN_FILE_BOUND");
            byte[] raw=File.ReadAllBytes(path);Need(CaptureJson.Hash(raw)==rawPin,"PLAN_FILE_HASH");
            return Parse(raw,rawPin,name=>
            {
                Need(name!=null&&name.Length>0&&name.IndexOfAny(new[]{'\\',':'})<0&&!Path.IsPathRooted(name)&&!name.Split('/').Contains(".."),"PLAN_RELATIVE_REFERENCE");
                string at=Path.Combine(Path.GetDirectoryName(Path.GetFullPath(path)),name.Replace('/',Path.DirectorySeparatorChar));
                CaptureJson.NoLinks(at);var file=new FileInfo(at);Need(file.Exists&&file.Length>0&&file.Length<=2*1024*1024,"PLAN_SNAPSHOT_BOUND");
                return File.ReadAllBytes(at);
            });
        }

        internal static ViewCapturePlan Parse(byte[] raw,string rawPin,Func<string,byte[]> reference)
        {
            Need(CaptureJson.Hash(raw)==rawPin,"PLAN_FILE_HASH");
            var p=CaptureJson.ParseStrictObject(raw,65536,"PLAN_JSON_INVALID");
            CaptureJson.Keys(p,"PLAN_CLOSED_FIELDS","version","scope","participants","source_kind","capture_surface","station_id","build_sha256","scene_sha256","snapshot","image","poses",
                "head_position_tolerance_m","head_orientation_tolerance_rad","state_tolerances","panel_state_sha256","max_state_arrival_age_ms");
            Need(Integer(p["version"])&&(long)p["version"]==1&&p["scope"].Type==JTokenType.String&&(string)p["scope"]=="engineering_provisional"&&
                p["participants"].Type==JTokenType.Boolean&&!(bool)p["participants"],"PLAN_SCOPE");
            Need(p["source_kind"].Type==JTokenType.String&&p["capture_surface"].Type==JTokenType.String,"CAPTURE_SOURCE");
            var plan=new ViewCapturePlan{Sha256=rawPin,SourceKind=(string)p["source_kind"],CaptureSurface=(string)p["capture_surface"]};
            Need((plan.SourceKind=="live"||plan.SourceKind=="snapshot")&&(plan.CaptureSurface=="headset"||plan.CaptureSurface=="desktop_engineering"),"CAPTURE_SOURCE");
            Need(p["station_id"].Type==JTokenType.String&&Id.IsMatch((string)p["station_id"]),"STATION_ID");plan.StationId=(string)p["station_id"];
            plan.BuildSha256=ReadHash(p["build_sha256"],"PLAN_HASH");plan.SceneSha256=ReadHash(p["scene_sha256"],"PLAN_HASH");plan.PanelStateSha256=ReadHash(p["panel_state_sha256"],"PLAN_HASH");
            foreach(var pin in new[]{plan.BuildSha256,plan.SceneSha256,plan.PanelStateSha256})Need(pin!=new string('0',64),"UNCONFIGURED_PLAN_HASH");
            var image=p["image"] as JObject;CaptureJson.Keys(image,"PLAN_CLOSED_FIELDS","width","height","max_channel_delta","minimum_unique_rgb_colors");
            foreach(string k in new[]{"width","height"})Need(Integer(image[k])&&(long)image[k]>0&&(long)image[k]<=8192,"IMAGE_DIMENSION");
            plan.Width=(int)image["width"];plan.Height=(int)image["height"];Need((long)plan.Width*plan.Height<=4_194_304,"IMAGE_DIMENSION");
            Need(Integer(image["max_channel_delta"])&&(long)image["max_channel_delta"]==0,"ZERO_PIXEL_TOLERANCE_REQUIRED");
            Need(Integer(image["minimum_unique_rgb_colors"])&&(long)image["minimum_unique_rgb_colors"]>=2&&(long)image["minimum_unique_rgb_colors"]<=256,"NONBLANK_SCREEN");
            plan.HeadPositionToleranceM=Finite(p["head_position_tolerance_m"],MaxHeadPositionToleranceM,"FINITE_RANGE");
            plan.HeadOrientationToleranceRad=Finite(p["head_orientation_tolerance_rad"],MaxHeadOrientationToleranceRad,"FINITE_RANGE");
            plan.MaxStateArrivalAgeMs=Finite(p["max_state_arrival_age_ms"],250,"FINITE_RANGE");
            var tolerances=p["state_tolerances"] as JObject;CaptureJson.Keys(tolerances,"PLAN_CLOSED_FIELDS",StateBounds.Select(x=>x.Key).ToArray());
            foreach(var (key,max) in StateBounds)Finite(tolerances[key],max,"FINITE_RANGE");
            Need(p["poses"] is JArray poses&&poses.Count==5,"FIVE_POSES_REQUIRED");
            var list=new List<PlanPose>();
            foreach(var item in (JArray)p["poses"])
            {
                var o=item as JObject;CaptureJson.Keys(o,"PLAN_CLOSED_FIELDS","id","head_pose");var head=o["head_pose"] as JObject;CaptureJson.Keys(head,"PLAN_CLOSED_FIELDS","position_m","rotation_xyzw");
                Need(o["id"].Type==JTokenType.String&&Id.IsMatch((string)o["id"])&&list.All(x=>x.Id!=(string)o["id"]),"POSE_ID");
                HeadPose pose;try{pose=new HeadPose(Vector(head["position_m"],3,"HEAD_POSE_INVALID"),Vector(head["rotation_xyzw"],4,"HEAD_POSE_INVALID"));}catch(ViewCaptureFault){throw new ViewCaptureFault("HEAD_POSE_INVALID");}
                list.Add(new PlanPose((string)o["id"],pose));
            }
            Need(new HashSet<string>(list.Select(x=>x.Id)).SetEquals(PoseIds),"FIVE_POSE_IDS_REQUIRED");
            for(int i=0;i<list.Count;i++)for(int j=i+1;j<list.Count;j++)
                Need(list[i].Pose.DistanceTo(list[j].Pose)>plan.HeadPositionToleranceM||list[i].Pose.AngleTo(list[j].Pose)>2*plan.HeadOrientationToleranceRad,"POSES_NOT_DISTINCT");
            plan.Poses=list.AsReadOnly();
            var snapshotRef=p["snapshot"] as JObject;CaptureJson.Keys(snapshotRef,"PLAN_CLOSED_FIELDS","path","sha256");
            Need(snapshotRef["path"].Type==JTokenType.String,"PLAN_RELATIVE_REFERENCE");plan.SnapshotSha256=ReadHash(snapshotRef["sha256"],"SHA256_REQUIRED");
            byte[] snapshotBytes=reference((string)snapshotRef["path"]);Need(CaptureJson.Hash(snapshotBytes)==plan.SnapshotSha256,"FILE_HASH");
            var snapshot=CaptureJson.ParseStrictObject(snapshotBytes,65536,"SNAPSHOT_JSON_INVALID");
            CaptureJson.Keys(snapshot,"SNAPSHOT_FIELDS","schema_version","scene_sha256","state","fixed_steps","coordinate_frame");
            Need(snapshot["scene_sha256"].Type==JTokenType.String&&(string)snapshot["scene_sha256"]==plan.SceneSha256,"SCENE_BINDING");
            return plan;
        }
    }
}
