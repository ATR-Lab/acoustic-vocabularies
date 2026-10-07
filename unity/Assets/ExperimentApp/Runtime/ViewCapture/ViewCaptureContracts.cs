using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.ViewCapture
{
    // Bounded, public refusal codes only. Never a path, endpoint, hidden pair
    // or exception message from a lower layer.
    public sealed class ViewCaptureFault : Exception
    {
        public string Code{get;}
        public ViewCaptureFault(string code):base(code!=null&&Regex.IsMatch(code,@"\A[A-Z][A-Z0-9_]{0,79}\z")?code:"VIEW_CAPTURE_FAULT"){Code=Message;}
        internal static void Require(bool condition,string code){if(!condition)throw new ViewCaptureFault(code);}
    }

    // Absolute Unity left-handed, Y-up world pose. Quaternions are XYZW.
    public readonly struct HeadPose
    {
        public readonly double[] Position,Rotation;
        public HeadPose(double[] position,double[] rotation)
        {
            ViewCaptureFault.Require(position!=null&&position.Length==3&&rotation!=null&&rotation.Length==4&&
                position.Concat(rotation).All(x=>!double.IsNaN(x)&&!double.IsInfinity(x)),"HEAD_POSE_INVALID");
            ViewCaptureFault.Require(Math.Abs(rotation.Sum(x=>x*x)-1)<=1e-5,"HEAD_POSE_NOT_UNIT");
            Position=(double[])position.Clone();Rotation=(double[])rotation.Clone();
        }
        public double DistanceTo(HeadPose other)
        {double sum=0;for(int i=0;i<3;i++){double d=Position[i]-other.Position[i];sum+=d*d;}return Math.Sqrt(sum);}
        // Same definition as isaac.reset.manager.angle: 2*acos(|<a,b>|).
        public double AngleTo(HeadPose other)
        {
            double dot=0;for(int i=0;i<4;i++)dot+=Rotation[i]*other.Rotation[i];
            return 2*Math.Acos(Math.Min(1,Math.Abs(dot)));
        }
        public bool Matches(HeadPose other,double positionTolerance,double orientationTolerance)=>
            DistanceTo(other)<=positionTolerance&&AngleTo(other)<=orientationTolerance;
        public JObject ToJson()=>new JObject{["position_m"]=new JArray(Position.Cast<object>().ToArray()),["rotation_xyzw"]=new JArray(Rotation.Cast<object>().ToArray())};
    }

    public readonly struct LegalPair
    {
        public readonly string Action,Target;
        internal LegalPair(string action,string target){Action=action;Target=target;}
    }

    public static class LegalPairs
    {
        // Same order as tools/view_leakage.py PAIRS: the existing public command
        // API names. These are capture labels, never renderer or audio input.
        public static readonly IReadOnlyList<LegalPair> All=Array.AsReadOnly(
            new[]{"ADD_ONE","REMOVE_ONE","FLIP_CARD","ALIGN_ARROW"}.SelectMany(a=>"ABCD".Select(t=>new LegalPair(a,"tray_"+t)))
            .Concat(new[]{"SCAN","TAG","CLOSE","QUARANTINE"}.SelectMany(a=>"EFGH".Select(t=>new LegalPair(a,"container_"+t)))).ToArray());
    }

    // Client-side monotonic nanoseconds. All capture timestamps in one run use
    // this single clock; remote Isaac timestamps are never mixed into it.
    public interface IViewCaptureClock{long NowNs{get;}}
    public sealed class StopwatchCaptureClock:IViewCaptureClock
    {
        public long NowNs=>ToNs(Stopwatch.GetTimestamp());
        public static long ToNs(long ticks)=>Stopwatch.Frequency==1_000_000_000?ticks:(long)((decimal)ticks*1_000_000_000m/Stopwatch.Frequency);
        // Existing Unity components report the same Stopwatch clock in ms or s.
        public static long FromMilliseconds(double ms)=>(long)Math.Floor(ms*1_000_000d);
        public static long FromSeconds(double seconds)=>(long)Math.Floor(seconds*1_000_000_000d);
    }

    // The #55 private control session in protected test mode. Only reset and
    // set_mode exist on this interface; no target-bearing command can be sent.
    public interface IViewCaptureControl
    {
        string RequiredMode{get;}
        bool ModeAcknowledged{get;}
        string ControlSessionId{get;}
        string FaultCode{get;}
        string RequestReset();
        // True only for the exact accepted reset reply that is already durable
        // and followed by a current post-reset health observation.
        bool TryResetAcknowledged(string requestId,out byte[] rawReply,out long receivedNs);
    }

    public interface IHeadPoseRig
    {
        // Desktop engineering surface can place the participant camera; a
        // tracked headset/simulator can only be read back and refused.
        bool CanSetPose{get;}
        void SetPose(HeadPose pose);
        HeadPose ReadPose();
    }

    public interface IFrameGrabber
    {
        // Actual 8-bit noninterlaced RGB PNG bytes of the participant camera.
        byte[] CapturePng(int width,int height);
        string Method{get;}
    }

    public sealed class StateReading
    {
        // Complete #53 readback shape, or null when it must be joined offline
        // from the Isaac owner-thread observation journal.
        public JObject State{get;}
        public long SampleNs{get;}
        // Visibly applied projection and frame identity, null when not exposed.
        public JObject Applied{get;}
        public StateReading(JObject state,long sampleNs,JObject applied){State=state;SampleNs=sampleNs;Applied=applied;}
    }
    public interface IStateReadback
    {
        string Origin{get;}
        // Returns false while no post-reset, currently neutral sample exists.
        bool TryRead(long notBeforeNs,out StateReading reading);
    }

    public interface IAudioRouting{JObject Read();}

    public sealed class ViewReading
    {
        public JObject View{get;}public JObject Inventory{get;}
        public ViewReading(JObject view,JObject inventory){View=view;Inventory=inventory;}
    }
    public interface IViewInventory{ViewReading Read();}

    // The only component that receives the hidden pair. Implementations must
    // not route it to rendering, panel, text or audio before the cue request.
    public interface IProtectedTrialBoundary
    {
        string Kind{get;}
        void Load(LegalPair pair);
        long RequestCue();
        void Unload();
    }

    public static class CaptureJson
    {
        public static string Hash(byte[] bytes)
        {using var sha=SHA256.Create();return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-","").ToLowerInvariant();}
        static JToken Sorted(JToken value)
        {
            switch(value)
            {
                case JObject o:
                    var result=new JObject();foreach(var p in o.Properties().OrderBy(p=>p.Name,StringComparer.Ordinal))result.Add(p.Name,Sorted(p.Value));return result;
                case JArray a:return new JArray(a.Select(Sorted));
                case JValue v:
                    ViewCaptureFault.Require(v.Type is JTokenType.Null or JTokenType.Boolean or JTokenType.Integer or JTokenType.Float or JTokenType.String,"CAPTURE_JSON_TYPE");
                    if(v.Type==JTokenType.Float){double d=(double)v;ViewCaptureFault.Require(!double.IsNaN(d)&&!double.IsInfinity(d),"CAPTURE_JSON_NONFINITE");}
                    if(v.Type==JTokenType.String)ViewCaptureFault.Require(((string)v).All(c=>c>=32&&c<127),"CAPTURE_JSON_NON_ASCII");
                    return v.DeepClone();
                default:throw new ViewCaptureFault("CAPTURE_JSON_TYPE");
            }
        }
        // Sorted keys, compact separators, ASCII and a terminal newline. For
        // the ASCII string/bool/integer values used by the panel descriptor
        // this equals Python json.dumps(sort_keys=True,separators=(',',':')).
        public static byte[] Canonical(JToken value)=>Encoding.ASCII.GetBytes(JsonConvert.SerializeObject(Sorted(value),Formatting.None)+"\n");
        internal static JObject ParseStrictObject(byte[] bytes,int maximum,string code)
        {
            ViewCaptureFault.Require(bytes!=null&&bytes.Length>0&&bytes.Length<=maximum,code);
            try{return Foundation.StationConfig.ParseStrict(new UTF8Encoding(false,true).GetString(bytes));}
            catch(Exception){throw new ViewCaptureFault(code);}
        }
        internal static void Keys(JObject value,string code,params string[] keys)=>
            ViewCaptureFault.Require(value!=null&&value.Properties().Select(x=>x.Name).OrderBy(x=>x,StringComparer.Ordinal).SequenceEqual(keys.OrderBy(x=>x,StringComparer.Ordinal)),code);
        internal static void NoLinks(string path)
        {
            for(string at=Path.GetFullPath(path);at!=null;at=Path.GetDirectoryName(at))
            {
                try{ViewCaptureFault.Require((File.GetAttributes(at)&FileAttributes.ReparsePoint)==0,"CAPTURE_LINK_FORBIDDEN");}
                catch(FileNotFoundException){}catch(DirectoryNotFoundException){}
            }
        }
    }
}
