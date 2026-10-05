using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using AcousticVocab.Foundation;
using AcousticVocab.StateSources;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace AcousticVocab.Soak
{
    public sealed class SoakFault:Exception { public SoakFault(string code):base(code){} }
    public sealed class SoakPlan
    {
        public string Sha256{get;}public string StationId{get;}public string SceneSha256{get;}public string SnapshotSha256{get;}
        public string ScheduleSha256{get;}public string BuildId{get;}public string ClientKind{get;}public double Seconds{get;}
        readonly JObject value;public JObject Json=>(JObject)value.DeepClone();
        internal static void Need(bool yes,string code){if(!yes)throw new SoakFault(code);}
        public static string Hash(byte[] bytes){using var h=SHA256.Create();return BitConverter.ToString(h.ComputeHash(bytes)).Replace("-","").ToLowerInvariant();}
        internal static bool HashOk(string x)=>x!=null&&System.Text.RegularExpressions.Regex.IsMatch(x,@"\A[0-9a-f]{64}\z");
        internal static bool Id(string x)=>x!=null&&System.Text.RegularExpressions.Regex.IsMatch(x,@"\A[A-Za-z0-9][A-Za-z0-9._-]{0,95}\z");
        internal static bool Finite(double x)=>!double.IsNaN(x)&&!double.IsInfinity(x)&&x>=0;
        SoakPlan(JObject p,string hash){value=(JObject)p.DeepClone();Sha256=hash;StationId=(string)p["station_id"];SceneSha256=(string)p["scene_sha256"];SnapshotSha256=(string)p["snapshot_sha256"];ScheduleSha256=(string)p["schedule_sha256"];BuildId=(string)p["build_id"];ClientKind=(string)p["client_kind"];Seconds=(double)p["seconds"];}
        public static SoakPlan Load(byte[] raw,string pin)
        {
            Need(raw!=null&&raw.Length>0&&raw.Length<=16384&&HashOk(pin)&&Hash(raw)==pin,"SOAK_PLAN_PIN");
            var p=StationConfig.ParseStrict(new UTF8Encoding(false,true).GetString(raw));
            string[] keys={"version","scope","participants","station_id","build_id","scene_sha256","snapshot_sha256","schedule_sha256","source_kind","seconds","client_kind","substitute_justification"};
            Need(p.Properties().Select(x=>x.Name).OrderBy(x=>x).SequenceEqual(keys.OrderBy(x=>x)),"SOAK_PLAN_FIELDS");
            Need(p["version"].Type==JTokenType.Integer&&(int)p["version"]==1&&(string)p["scope"]=="synthetic_nonstudy"&&p["participants"].Type==JTokenType.Boolean&&!(bool)p["participants"]&&(string)p["source_kind"]=="live","SOAK_PLAN_SCOPE");
            foreach(string key in new[]{"station_id","build_id"})Need(p[key].Type==JTokenType.String&&Id((string)p[key]),"SOAK_PLAN_ID");
            foreach(string key in new[]{"scene_sha256","snapshot_sha256","schedule_sha256"})Need(p[key].Type==JTokenType.String&&HashOk((string)p[key]),"SOAK_PLAN_BINDING");
            Need((p["seconds"].Type==JTokenType.Float||p["seconds"].Type==JTokenType.Integer)&&Finite((double)p["seconds"])&&(double)p["seconds"]>0&&(double)p["seconds"]<=36000,"SOAK_PLAN_DURATION");
            Need(new[]{"headset","headset_equivalent"}.Contains((string)p["client_kind"]),"SOAK_PLAN_CLIENT");
            Need(p["substitute_justification"].Type==JTokenType.String&&((string)p["substitute_justification"]).Length<=1024&&((string)p["client_kind"]=="headset"||!string.IsNullOrWhiteSpace((string)p["substitute_justification"])),"SOAK_PLAN_SUBSTITUTE");
            return new SoakPlan(p,pin);
        }
    }
    public sealed class SoakContext
    {
        public string Block{get;}public string BlockId{get;}public string EngineState{get;}public string TrialId{get;}
        public SoakContext(string block,string blockId,string engineState,string trialId)
        {
            SoakPlan.Need(new[]{"teaching","protected","paused"}.Contains(block)&&SoakPlan.Id(blockId)&&SoakPlan.Id(engineState)&&(trialId==null||SoakPlan.Id(trialId)),"SOAK_CONTEXT");
            Block=block;BlockId=blockId;EngineState=engineState;TrialId=trialId;
        }
        internal JObject Json()=>new JObject{["block"]=Block,["block_id"]=BlockId,["engine_state"]=EngineState,["trial_id"]=TrialId};
    }
    // All times are the same Unity-process Stopwatch seconds. Neither publisher
    // timestamps nor assumed cross-host offsets enter a coverage calculation.
    public sealed class ContinuousSoakMonitor
    {
        readonly object sync=new object();readonly Action<string,double,JObject> sink;readonly double start;
        double lastClock,lastRender,lastArrival,nextHeartbeat;long lastFrame=-1,mirrored;long? sequence;string session;
        double? staleStart,freezeStart;string staleBlock,freezeBlock;bool closed;SoakContext context;
        public string Fault{get;private set;}
        public bool Healthy{get{lock(sync)return !closed&&Fault==null;}}
        public ContinuousSoakMonitor(double now,SoakContext initial,Action<string,double,JObject> sink)
        {
            SoakPlan.Need(SoakPlan.Finite(now)&&initial!=null&&sink!=null,"SOAK_MONITOR_INPUT");
            start=lastClock=lastRender=lastArrival=nextHeartbeat=now;context=initial;this.sink=sink;
        }
        void Clock(double now){SoakPlan.Need(!closed&&SoakPlan.Finite(now)&&now>=lastClock,"SOAK_CLOCK");lastClock=now;}
        void Write(string kind,double now,JObject p){try{sink(kind,now,p);}catch{Fault="SOAK_LOG_FAILED";throw;}}
        public void Context(SoakContext value,double now){lock(sync){Clock(now);SoakPlan.Need(value!=null,"SOAK_CONTEXT");context=value;Write("context",now,value.Json());}}
        public void Source(string sourceSession,long sourceSequence,double arrival,double now)
        {
            lock(sync)
            {
                Clock(now);SoakPlan.Need(sourceSequence>=0&&System.Text.RegularExpressions.Regex.IsMatch(sourceSession??"",@"\A[0-9a-f]{32}\z")&&SoakPlan.Finite(arrival)&&arrival<=now&&(session==null||arrival>=lastArrival),"SOAK_SOURCE_CLOCK");Gaps(now);
                // Renderer interpolation may repeat an applied sequence. Count
                // only a distinct progressing applied frame; this is a lower
                // bound on received packets, never a fabricated frame count.
                if(session==sourceSession&&sequence.HasValue)SoakPlan.Need(sourceSequence>=sequence.Value,"SOAK_SOURCE_REGRESSION");
                if(session!=sourceSession&&session!=null)Write("source_restart",now,new JObject{["previous_session"]=session,["session"]=sourceSession});
                if(session!=sourceSession||!sequence.HasValue||sourceSequence>sequence.Value)mirrored++;
                session=sourceSession;sequence=sourceSequence;lastArrival=arrival;
                Gaps(now);
            }
        }
        public void Render(long frame,double now){lock(sync){Clock(now);if(frame==lastFrame)return;SoakPlan.Need(frame>lastFrame,"SOAK_RENDER_REGRESSION");Gaps(now);lastRender=now;lastFrame=frame;Gaps(now);}}
        void Gaps(double now)
        {
            Gap("stale_gap",now,lastArrival,ref staleStart,ref staleBlock);
            Gap("frame_freeze",now,lastRender,ref freezeStart,ref freezeBlock);
        }
        void Gap(string kind,double now,double last,ref double? began,ref string block)
        {
            bool overdue=now-last>.25;
            if(overdue&&!began.HasValue){began=last;block=context.Block;Write(kind+"_started",now,new JObject{["start_s"]=last,["block"]=block,["block_id"]=context.BlockId,["duration_ms"]=(now-last)*1000});if(block=="protected")Fault=kind=="stale_gap"?"SOAK_PROTECTED_STALE":"SOAK_PROTECTED_FREEZE";}
            if(began.HasValue&&context.Block=="protected"){block="protected";Fault=kind=="stale_gap"?"SOAK_PROTECTED_STALE":"SOAK_PROTECTED_FREEZE";}
            if(!overdue&&began.HasValue){Write(kind,now,new JObject{["block"]=block,["duration_ms"]=(last-began.Value)*1000,["start_s"]=began.Value});began=null;block=null;}
        }
        public void Tick(double now)
        {
            lock(sync)
            {
                Clock(now);Gaps(now);
                if(now>=nextHeartbeat)
                {
                    var p=context.Json();p["state_age_ms"]=(now-lastArrival)*1000;p["frame_age_ms"]=(now-lastRender)*1000;p["mirrored_frames"]=mirrored;p["applied_session"]=session;p["applied_sequence"]=sequence.HasValue?(JToken)sequence.Value:JValue.CreateNull();p["render_frame_index"]=lastFrame;
                    Write("heartbeat",now,p);nextHeartbeat=now+.25;
                }
            }
        }
        public void Close(double now){lock(sync){if(closed)return;Clock(now);Gaps(now);foreach(var gap in new[]{(staleStart,staleBlock,"stale_gap"),(freezeStart,freezeBlock,"frame_freeze")})if(gap.Item1.HasValue)Write(gap.Item3,now,new JObject{["block"]=gap.Item2,["duration_ms"]=(now-gap.Item1.Value)*1000,["open_at_end"]=true});closed=true;}}
    }
    public sealed class SoakNativeJournal:IDisposable
    {
        readonly object sync=new object();readonly FileStream stream;readonly string epoch;readonly long frequency;long sequence;string previous=new string('0',64);bool closed;
        public SoakNativeJournal(string freshDirectory,string epoch,long frequency)
        {
            SoakPlan.Need(!Directory.Exists(freshDirectory)&&frequency>0&&System.Text.RegularExpressions.Regex.IsMatch(epoch??"",@"\A[0-9a-f]{32}\z"),"SOAK_OUTPUT");
            for(var p=new DirectoryInfo(Path.GetFullPath(freshDirectory));p!=null;p=p.Parent)if(p.Exists)SoakPlan.Need((p.Attributes&FileAttributes.ReparsePoint)==0,"SOAK_OUTPUT_LINK");
            Directory.CreateDirectory(freshDirectory);stream=new FileStream(Path.Combine(freshDirectory,"soak-native.jsonl"),FileMode.CreateNew,FileAccess.Write,FileShare.Read);this.epoch=epoch;this.frequency=frequency;
        }
        static byte[] Bytes(JObject p)=>new UTF8Encoding(false,true).GetBytes(p.ToString(Formatting.None)+"\n");
        public void Write(string kind,double now,JObject data)
        {
            lock(sync)
            {
                SoakPlan.Need(!closed&&SoakPlan.Finite(now)&&SoakPlan.Id(kind)&&data!=null,"SOAK_LOG_STATE");
                var row=new JObject{["version"]=1,["seq"]=sequence,["clock_epoch"]=epoch,["clock_domain"]="unity_stopwatch_seconds",["stopwatch_frequency_hz"]=frequency,["t_s"]=now,["kind"]=kind,["payload"]=data.DeepClone(),["previous_sha256"]=previous};
                string hash=SoakPlan.Hash(Bytes(row));row["sha256"]=hash;var bytes=Bytes(row);SoakPlan.Need(bytes.Length<=65536&&stream.Length+bytes.Length<=512L*1024*1024,"SOAK_LOG_LIMIT");stream.Write(bytes,0,bytes.Length);stream.Flush(true);previous=hash;sequence++;
            }
        }
        public void Dispose(){lock(sync){if(closed)return;closed=true;try{stream.Flush(true);}finally{stream.Dispose();}}}
    }
    // Every cleanup stage runs, even when the terminal record or an earlier
    // resource close fails. Preserve the first error for a truthful fault latch.
    public static class SoakCleanup
    {
        public static Exception Run(Action terminal,params Action[] cleanup)
        {
            Exception first=null;
            try{terminal?.Invoke();}catch(Exception e){first=e;}
            foreach(var action in cleanup)try{action?.Invoke();}catch(Exception e){if(first==null)first=e;}
            return first;
        }
    }
    [DefaultExecutionOrder(-31000)]
    public sealed class SoakCaptureHost:MonoBehaviour
    {
        readonly object sync=new object();SoakPlan plan;SoakNativeJournal journal;ContinuousSoakMonitor monitor;Func<SoakContext> context;Action<string> faultPause;Timer watchdog;
        double started;string lastContext,failure,outputDirectory,lastProbe;bool installed,closed,notified;long lastFrame=-1;
        public bool Healthy=>installed&&!closed&&failure==null&&monitor!=null&&monitor.Healthy;
        public static double Now=>Stopwatch.GetTimestamp()/(double)Stopwatch.Frequency;
        public void Install(byte[] rawPlan,string rawSha256,string freshOutput,string actualStationId,string actualBuildId,string actualSceneSha256,string actualSnapshotSha256,string actualScheduleSha256,Func<SoakContext> context,Action<string> faultPause)
        {
            SoakPlan.Need(!installed&&!closed&&context!=null&&faultPause!=null,"SOAK_INSTALL");plan=SoakPlan.Load(rawPlan,rawSha256);
            SoakPlan.Need(plan.StationId==actualStationId&&plan.BuildId==actualBuildId&&plan.SceneSha256==actualSceneSha256&&plan.SnapshotSha256==actualSnapshotSha256&&plan.ScheduleSha256==actualScheduleSha256,"SOAK_RUNTIME_BINDING");this.context=context;this.faultPause=faultPause;
            try
            {
                var initial=context();SoakPlan.Need(initial!=null,"SOAK_CONTEXT");
                outputDirectory=Path.GetFullPath(freshOutput);journal=new SoakNativeJournal(outputDirectory,Guid.NewGuid().ToString("N"),Stopwatch.Frequency);started=Now;
                journal.Write("session_start",started,new JObject{["plan"]=plan.Json,["plan_sha256"]=plan.Sha256,["monitor"]="continuous_receiver_stale_and_freeze_detector",["arrival_age_basis"]="latest_accepted_sample_local_arrival",["mirror_count_basis"]="distinct_applied_frame_session_sequence",["render_basis"]="application_onBeforeRender_not_photons"});
                monitor=new ContinuousSoakMonitor(started,initial,journal.Write);Application.onBeforeRender+=Rendered;
                watchdog=new Timer(_=>{lock(sync){if(!installed||closed)return;try{monitor.Tick(Now);}catch{failure=failure??"SOAK_LOG_OR_CLOCK_FAILED";}}},null,10,10);
                installed=true;
            }
            catch
            {
                failure=failure??"SOAK_INSTALL_FAILED";closed=true;
                SoakCleanup.Run(null,()=>Application.onBeforeRender-=Rendered,()=>watchdog?.Dispose(),()=>journal?.Dispose());
                throw;
            }
        }
        // Subscribe only to StateSourceHost's post-Apply, visibly live event.
        // Arrival is explicitly the latest accepted sample's local receipt time.
        public void SourceApplied(SceneFrame frame,double arrivalSeconds)
        {
            lock(sync){if(closed)return;SoakPlan.Need(installed&&frame!=null&&frame.Provenance=="live","SOAK_APPLIED_BINDING");monitor.Source(frame.SessionId,frame.Sequence,arrivalSeconds,Now);}
        }
        void Rendered(){lock(sync){if(!installed||closed||lastFrame==Time.frameCount)return;try{lastFrame=Time.frameCount;monitor.Render(lastFrame,Now);}catch{failure="SOAK_RENDER_LOG_FAILED";}}}
        void Update()
        {
            lock(sync)
            {
                if(!installed||closed)return;
                try{var c=context();string key=c.Json().ToString(Formatting.None);if(key!=lastContext){monitor.Context(c,Now);lastContext=key;}Probe();if(Now-started>=plan.Seconds)Finish();}
                catch{failure="SOAK_CONTEXT_FAILED";}
                string fault=failure??monitor.Fault;if(fault!=null&&!notified){notified=true;faultPause(fault);}
            }
        }
        // Observation-only causal clock handshake. It never dispatches a command
        // or assumes Unity and the external coordinator share an epoch.
        void Probe()
        {
            string path=Path.Combine(outputDirectory,"coordinator-probe.json");if(!File.Exists(path))return;
            var info=new FileInfo(path);SoakPlan.Need(info.Length>0&&info.Length<=4096&&(info.Attributes&FileAttributes.ReparsePoint)==0,"SOAK_PROBE_FILE");
            var p=StationConfig.ParseStrict(File.ReadAllText(path,new UTF8Encoding(false,true)));
            string[] keys={"version","request_id","coordinator_clock_id","sent_ns"};
            SoakPlan.Need(p.Properties().Select(x=>x.Name).OrderBy(x=>x).SequenceEqual(keys.OrderBy(x=>x))&&p["version"].Type==JTokenType.Integer&&(int)p["version"]==1&&
                System.Text.RegularExpressions.Regex.IsMatch((string)p["request_id"]??"",@"\A[0-9a-f]{32}\z")&&SoakPlan.Id((string)p["coordinator_clock_id"])&&
                p["sent_ns"].Type==JTokenType.String&&System.Text.RegularExpressions.Regex.IsMatch((string)p["sent_ns"],@"\A[0-9]{1,20}\z"),"SOAK_PROBE_FIELDS");
            string hash=SoakPlan.Hash(Encoding.UTF8.GetBytes(p.ToString(Formatting.None)));if(hash==lastProbe)return;lastProbe=hash;journal.Write("coordinator_probe",Now,p);
        }
        public void Observe(string kind,JObject exactNativeObservation)
        {
            lock(sync){SoakPlan.Need(installed&&!closed&&new[]{"reset_receipt","lock_probe_receipt","fault_injection","operator_resume","durable_record","cue_observation"}.Contains(kind),"SOAK_OBSERVATION");journal.Write(kind,Now,exactNativeObservation);}
        }
        public void Finish()
        {
            lock(sync)
            {
                if(closed)return;closed=true;
                var error=SoakCleanup.Run(()=>
                {
                    if(!installed)return;
                    double now=Now;monitor.Close(now);
                    journal.Write("session_end",now,new JObject{["completed"]=failure==null&&now-started>=plan.Seconds,["requested_seconds"]=plan.Seconds,["elapsed_seconds"]=now-started,["monitor_fault"]=failure??monitor.Fault,["g2_signed"]=false});
                },()=>Application.onBeforeRender-=Rendered,()=>watchdog?.Dispose(),()=>journal?.Dispose());
                if(error!=null)failure=failure??"SOAK_CLOSE_FAILED";
            }
        }
        void OnDisable()=>Finish();void OnDestroy()=>Finish();
    }
}
