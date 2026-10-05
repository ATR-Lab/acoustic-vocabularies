using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using AcousticVocab.Foundation;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.FrameBudget
{
    public sealed class FrameSetup
    {
        public int RefreshHz{get;}public string Control{get;}public int WatchdogPollMs{get;}public bool StallHook{get;}public string Sha256{get;}
        internal FrameSetup(int hz,string control,int poll,bool hook,string hash){RefreshHz=hz;Control=control;WatchdogPollMs=poll;StallHook=hook;Sha256=hash;}
        public static FrameSetup Load(byte[] bytes,JObject validatedStation)
        {
            try
            {
                Check.That(bytes!=null&&bytes.Length>0&&bytes.Length<=4096&&validatedStation!=null,"FRAME_CONFIG_INVALID");
                var p=StationConfig.ParseStrict(new UTF8Encoding(false,true).GetString(bytes));
                var keys=new[]{"version","protocol_version","station_id","refresh_control","watchdog_poll_ms","engineering_stall_hook"};
                Check.That(p.Properties().Select(x=>x.Name).OrderBy(x=>x).SequenceEqual(keys.OrderBy(x=>x))&&p["version"].Type==JTokenType.Integer&&(int)p["version"]==1&&
                    p["protocol_version"].Type==JTokenType.String&&(string)p["protocol_version"]==(string)validatedStation["protocol_version"]&&
                    p["station_id"].Type==JTokenType.String&&(string)p["station_id"]==(string)validatedStation["station_id"]&&
                    p["refresh_control"].Type==JTokenType.String&&new[]{"request","require_current"}.Contains((string)p["refresh_control"])&&
                    p["watchdog_poll_ms"].Type==JTokenType.Integer&&(int)p["watchdog_poll_ms"]>=2&&(int)p["watchdog_poll_ms"]<=50&&
                    p["engineering_stall_hook"].Type==JTokenType.Boolean&&validatedStation["refresh_hz"].Type==JTokenType.Integer,"FRAME_CONFIG_INVALID");
                int hz=(int)validatedStation["refresh_hz"];Check.That(new[]{72,80,90,120}.Contains(hz),"FRAME_CONFIG_INVALID");
                return new FrameSetup(hz,(string)p["refresh_control"],(int)p["watchdog_poll_ms"],(bool)p["engineering_stall_hook"],Hash(bytes));
            }
            catch(FrameFault){throw;}catch{throw new FrameFault("FRAME_CONFIG_INVALID");}
        }
        internal static string Hash(byte[] bytes){using var sha=SHA256.Create();return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-","").ToLowerInvariant();}
    }
    public interface IDisplayRate
    {bool Running{get;}bool TryOffered(out double[] rates);bool TryCurrent(out double hz);bool Request(double hz);}
    public sealed class RefreshPin
    {
        readonly FrameSetup setup;readonly IDisplayRate runtime;bool started,failed;int confirmations;
        public bool Ready=>started&&!failed&&confirmations>=3;
        public bool RequestAccepted{get;private set;}public double? CurrentHz{get;private set;}
        public IReadOnlyList<double> Offered{get;private set;}=Array.AsReadOnly(Array.Empty<double>());
        public RefreshPin(FrameSetup setup,IDisplayRate runtime){this.setup=setup??throw new ArgumentNullException(nameof(setup));this.runtime=runtime??throw new ArgumentNullException(nameof(runtime));}
        void Need(bool yes,string code){if(!yes){failed=true;throw new FrameFault(code);}}
        public void Begin()
        {
            Need(!started&&!failed&&runtime.Running,"FRAME_REFRESH_UNAVAILABLE");started=true;
            bool available=runtime.TryOffered(out var offered);
            if(available){Need(offered!=null&&offered.Length>0&&offered.Length<=64&&offered.All(x=>Check.Finite(x)&&x>0),"FRAME_REFRESH_INVALID");Offered=Array.AsReadOnly((double[])offered.Clone());}
            if(setup.Control=="request")
            {Need(available&&offered.Any(x=>Math.Abs(x-setup.RefreshHz)<.01),"FRAME_REFRESH_NOT_OFFERED");RequestAccepted=runtime.Request(setup.RefreshHz);Need(RequestAccepted,"FRAME_REFRESH_REQUEST_REJECTED");}
        }
        public void Observe()
        {
            Need(started&&!failed&&runtime.Running,"FRAME_REFRESH_UNAVAILABLE");
            Need(runtime.TryCurrent(out double hz)&&Check.Finite(hz)&&hz>0,"FRAME_REFRESH_UNAVAILABLE");CurrentHz=hz;
            if(Math.Abs(hz-setup.RefreshHz)<.01)confirmations=Math.Min(confirmations+1,3);
            else{if(confirmations>=3||setup.Control=="require_current")Need(false,"FRAME_REFRESH_CHANGED");confirmations=0;}
        }
    }
    // Raw frame rows are buffered (not one fsync per frame); summaries and faults
    // flush the raw prefix to disk before their typed records leave this sink.
    // Crashed/torn directories are retained and never reopened as a fresh capture.
    public sealed class FrameCsvEvidence : IFrameEvidence,IDisposable
    {
        readonly object sync=new object();readonly string directory;readonly FileStream frameFile,eventFile;readonly StreamWriter frames,events;bool failed,closed;
        public FrameCsvEvidence(string freshDirectory,JObject metadata)
        {
            directory=Path.GetFullPath(freshDirectory);Check.That(!Directory.Exists(directory),"FRAME_OUTPUT_EXISTS");
            for(var p=new DirectoryInfo(Path.GetDirectoryName(directory));p!=null;p=p.Parent)if(p.Exists)Check.That((p.Attributes&FileAttributes.ReparsePoint)==0,"FRAME_OUTPUT_LINK");
            Directory.CreateDirectory(directory);WriteNew(Path.Combine(directory,"metadata.json"),Encoding.UTF8.GetBytes(metadata.ToString(Formatting.Indented)+"\n"));
            frameFile=new FileStream(Path.Combine(directory,"frames.csv"),FileMode.CreateNew,FileAccess.Write,FileShare.Read);eventFile=new FileStream(Path.Combine(directory,"events.jsonl"),FileMode.CreateNew,FileAccess.Write,FileShare.Read);
            frames=new StreamWriter(frameFile,new UTF8Encoding(false),65536,true){NewLine="\n"};events=new StreamWriter(eventFile,new UTF8Encoding(false),4096,true){NewLine="\n"};
            frames.WriteLine("attempt_id,opportunity_id,window_id,window_kind,frame_index,from_mono_ms,to_mono_ms,render_interval_ms,overlap_ms,cpu_ms_delayed,gpu_ms_delayed,runtime_refresh_hz,unity_fixed_step_ms,physics_steps,presented_count_reported,dropped_count_reported");Flush();
        }
        static void WriteNew(string path,byte[] bytes){using var f=new FileStream(path,FileMode.CreateNew,FileAccess.Write,FileShare.Read);f.Write(bytes,0,bytes.Length);f.Flush(true);}
        static string N(object value)=>value==null?"":Convert.ToString(value,CultureInfo.InvariantCulture);
        void Need()=>Check.That(!failed&&!closed,"FRAME_LOG_UNAVAILABLE");
        void Flush(){frames.Flush();events.Flush();frameFile.Flush(true);eventFile.Flush(true);}
        void Do(Action action){lock(sync){Need();try{action();}catch{failed=true;throw new FrameFault("FRAME_LOG_FAILED");}}}
        public void Interval(FrameInterval x)=>Do(()=>{var s=x.Sample;frames.WriteLine(string.Join(",",new object[]{x.Attempt.AttemptId,x.Attempt.OpportunityId,x.Window.Id,x.Window.Kind,s.FrameIndex,x.FromMs,x.ToMs,x.IntervalMs,x.OverlapMs,s.CpuMs,s.GpuMs,s.RuntimeRefreshHz,s.UnityFixedStepMs,s.PhysicsSteps,s.PresentedCount,s.DroppedCount}.Select(N)));});
        public void Fault(FrameFaultRecord x)=>Do(()=>{events.WriteLine(new JObject{["kind"]="fault",["attempt_id"]=x.Attempt.AttemptId,["opportunity_id"]=x.Attempt.OpportunityId,["observed_mono_ms"]=x.ObservedMs,["technical_fault_code"]=x.Code,["render_gap_ms"]=x.GapMs,["watchdog"]=x.Watchdog}.ToString(Formatting.None));Flush();});
        public void Summary(FrameSummary x)=>Do(()=>{events.WriteLine(new JObject{["kind"]="summary",["attempt_id"]=x.Attempt.AttemptId,["opportunity_id"]=x.Attempt.OpportunityId,["observed_mono_ms"]=x.ObservedMs,["frame_freeze_ms"]=x.MaximumMs,["frame_count"]=x.FrameCount,["within_1_5x_count"]=x.WithinBudgetCount,["capture_complete"]=x.Complete}.ToString(Formatting.None));Flush();});
        public void Rate(JObject report)=>Do(()=>WriteNew(Path.Combine(directory,"runtime-rate.json"),Encoding.UTF8.GetBytes(report.ToString(Formatting.Indented)+"\n")));
        public void Dispose()
        {
            lock(sync)
            {
                if(closed)return;
                try{if(!failed)Flush();}finally{closed=true;frames.Dispose();events.Dispose();frameFile.Dispose();eventFile.Dispose();}
                if(!failed)
                {
                    var files=new JArray();foreach(string name in new[]{"metadata.json","frames.csv","events.jsonl","runtime-rate.json"}.Where(n=>File.Exists(Path.Combine(directory,n)))){using var stream=File.OpenRead(Path.Combine(directory,name));using var hash=SHA256.Create();files.Add(new JObject{["path"]=name,["bytes"]=stream.Length,["sha256"]=BitConverter.ToString(hash.ComputeHash(stream)).Replace("-","").ToLowerInvariant()});}
                    WriteNew(Path.Combine(directory,"manifest.json"),Encoding.UTF8.GetBytes(new JObject{["version"]=1,["capture_kind"]="application_render_callbacks_not_photon_timestamps",["files"]=files}.ToString(Formatting.Indented)+"\n"));
                }
            }
        }
    }
}
