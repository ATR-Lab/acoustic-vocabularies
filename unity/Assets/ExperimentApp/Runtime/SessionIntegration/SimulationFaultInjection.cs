using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using AcousticVocab.DataLogging;
using AcousticVocab.Foundation;
using AcousticVocab.FrameBudget;
using AcousticVocab.ResponsePanel;
using AcousticVocab.SessionEngine;
using AcousticVocab.StateIntegration;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;
namespace AcousticVocab.SessionIntegration
{
    // One predeclared issue-81 fault case, in the exact tools/mock_visit/faults.py
    // plan format. Only a SIMULATION_TEST authority can load it; participant
    // builds cannot construct that authority, so every hook below is unreachable.
    public sealed class SimulationFaultPlan
    {
        public static readonly IReadOnlyDictionary<string,string> Hooks=new Dictionary<string,string>(StringComparer.Ordinal)
        {
            ["presentation_stall"]="frame_capture_inject_stall",["audio_underrun"]="audio_thread_stall",
            ["corrupt_file_hash"]="package_read_corruption",["missing_response_log"]="data_journal_refuse_panel_response",
            ["failed_reset"]="control_reset_reply_withheld",["input_loss"]="response_input_suppressed",
            ["headset_disconnect"]="application_focus_lost",
        };
        public string RawSha256{get;}public string CaseId{get;}public string Scenario{get;}public string RunId{get;}
        public string BuildManifestSha256{get;}public string ConfigSha256{get;}public string ScheduleSha256{get;}
        public IReadOnlyList<string> ExpectedNativeCodes{get;}public string PlannedOpportunityId{get;}public double MinimumObservationMs{get;}
        public string Hook=>Hooks[Scenario];
        SimulationFaultPlan(string raw,JObject p)
        {
            RawSha256=raw;CaseId=(string)p["case_id"];Scenario=(string)p["scenario"];RunId=(string)p["run_id"];BuildManifestSha256=(string)p["build_manifest_sha256"];
            ConfigSha256=(string)p["config_sha256"];ScheduleSha256=(string)p["schedule_sha256"];ExpectedNativeCodes=p["expected_native_codes"].Select(x=>(string)x).ToList().AsReadOnly();
            PlannedOpportunityId=p["planned_opportunity_id"].Type==JTokenType.Null?null:(string)p["planned_opportunity_id"];MinimumObservationMs=(double)p["minimum_observation_ms"];
        }
        static void Need(bool okay){if(!okay)throw new SessionFault("JOIN_SIMULATION_FAULT_PLAN_INVALID");}
        static bool Id(JToken v)=>v?.Type==JTokenType.String&&Regex.IsMatch((string)v,@"\A[A-Za-z0-9][A-Za-z0-9._-]{0,79}\z");
        static bool Hash(JToken v)=>v?.Type==JTokenType.String&&Regex.IsMatch((string)v,@"\A[0-9a-f]{64}\z");
        internal static string Sha256(byte[] bytes){using var sha=SHA256.Create();return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-","").ToLowerInvariant();}
        public static SimulationFaultPlan Load(SimulationTestAuthority authority,string path,string rawPin,string configSha256,string scheduleSha256,IEnumerable<string> opportunities)
        {
            Need(authority!=null&&SimulationTestAuthority.CompiledCapability&&path!=null&&Path.IsPathRooted(path)&&!path.StartsWith("\\\\")&&!path.StartsWith("//"));
            for(string at=Path.GetFullPath(path);at!=null;at=Path.GetDirectoryName(at))
                try{Need((File.GetAttributes(at)&FileAttributes.ReparsePoint)==0);}catch(FileNotFoundException){}catch(DirectoryNotFoundException){}
            var info=new FileInfo(path);Need(info.Exists&&info.Length>0&&info.Length<=65536);
            return Parse(authority,File.ReadAllBytes(path),rawPin,configSha256,scheduleSha256,opportunities);
        }
        public static SimulationFaultPlan Parse(SimulationTestAuthority authority,byte[] raw,string rawPin,string configSha256,string scheduleSha256,IEnumerable<string> opportunities)
        {
            Need(authority!=null&&SimulationTestAuthority.CompiledCapability&&raw!=null&&raw.Length<=65536&&rawPin!=null&&Sha256(raw)==rawPin);
            JObject p;try{p=StationConfig.ParseStrict(new UTF8Encoding(false,true).GetString(raw));}catch(SessionFault){throw;}catch{throw new SessionFault("JOIN_SIMULATION_FAULT_PLAN_INVALID");}
            var keys=new[]{"version","scope","case_id","scenario","run_id","build_manifest_sha256","config_sha256","schedule_sha256","expected_native_codes","planned_opportunity_id","minimum_observation_ms"};
            Need(p!=null&&p.Properties().Select(x=>x.Name).OrderBy(x=>x,StringComparer.Ordinal).SequenceEqual(keys.OrderBy(x=>x,StringComparer.Ordinal)));
            Need(p["version"].Type==JTokenType.Integer&&(long)p["version"]==1&&p["scope"].Type==JTokenType.String&&(string)p["scope"]=="SIMULATION_TEST");
            Need(Id(p["case_id"])&&Id(p["run_id"])&&p["scenario"].Type==JTokenType.String&&Hooks.ContainsKey((string)p["scenario"]));
            Need(Hash(p["build_manifest_sha256"])&&Hash(p["config_sha256"])&&Hash(p["schedule_sha256"]));
            // Bind to the actually loaded run; the build inventory pin is only
            // checked later against the closed run manifest by faults.py.
            Need((string)p["config_sha256"]==configSha256&&(string)p["schedule_sha256"]==scheduleSha256);
            Need(p["expected_native_codes"] is JArray codes&&codes.Count>=1&&codes.Count<=8&&codes.All(c=>c.Type==JTokenType.String&&Regex.IsMatch((string)c,@"\A[A-Za-z0-9_:-]{1,128}\z"))
                &&codes.Select(c=>(string)c).Distinct(StringComparer.Ordinal).Count()==codes.Count);
            var opportunity=p["planned_opportunity_id"];
            Need(opportunity.Type==JTokenType.Null?(string)p["scenario"]=="headset_disconnect":Id(opportunity)&&opportunities!=null&&opportunities.Contains((string)opportunity,StringComparer.Ordinal));
            Need(p["minimum_observation_ms"].Type is JTokenType.Integer or JTokenType.Float);double window=(double)p["minimum_observation_ms"];
            Need(!double.IsNaN(window)&&window>=1000&&window<=60000);
            return new SimulationFaultPlan(rawPin,p);
        }
    }
    internal enum FaultApplication{Pending,Applied,Refused}
    internal readonly struct FaultStep
    {
        internal readonly FaultApplication State;internal readonly string RefusalCode;internal readonly JObject Parameters;
        FaultStep(FaultApplication state,string code,JObject parameters){State=state;RefusalCode=code;Parameters=parameters;}
        internal static FaultStep Pending=>new FaultStep(FaultApplication.Pending,null,null);
        internal static FaultStep Applied(JObject parameters)=>new FaultStep(FaultApplication.Applied,null,parameters??new JObject());
        internal static FaultStep Refused(string code)=>new FaultStep(FaultApplication.Refused,code,null);
    }
    // The native mechanism behind each hook. Implementations must only cause the
    // fault; detection, pause, reset and recovery stay in the ordinary app code.
    internal interface ISimulationFaultTargets
    {
        FaultStep Step(string scenario,Func<bool> plannedCurrent);
        JObject AppliedParameters{get;} // Observation only; never arms a hook.
        void Disarm(string scenario);
    }
    internal interface IFaultEngineView{string CurrentOpportunityId{get;}ItemState? CurrentState{get;}SessionState Status{get;}}
    internal sealed class EngineFaultView:IFaultEngineView
    {
        readonly FixedSlotEngine engine;internal EngineFaultView(FixedSlotEngine engine){this.engine=engine??throw new ArgumentNullException(nameof(engine));}
        public string CurrentOpportunityId=>engine.CurrentOpportunityId;public ItemState? CurrentState=>engine.CurrentState;public SessionState Status=>engine.Status;
    }
    // Requests the planned hook once, at the planned point, and writes one
    // create-new receipt. It never infers an outcome from a fault code.
    internal sealed class SimulationFaultInjector
    {
        public const string ReceiptName="simulation-fault-injection.local.json";
        readonly SimulationTestAuthority authority;readonly SimulationFaultPlan plan;readonly ISimulationFaultTargets targets;readonly IFaultEngineView engine;
        readonly string path,nonce;readonly Func<double> mono;readonly Func<DateTime> utc;readonly Func<string> lastCommitted;readonly Action<JObject> audit;
        bool requested,seenPlanned,applying,written;double requestedMono;DateTime requestedUtc;string prefix,appliedOpportunity;
        internal bool Written=>written;
        internal SimulationFaultInjector(SimulationTestAuthority authority,SimulationFaultPlan plan,ISimulationFaultTargets targets,IFaultEngineView engine,string receiptPath,string sessionNonce,
            Func<double> monotonicMs,Func<DateTime> utcNow,Func<string> lastCommittedDataSha256,Action<JObject> audit)
        {
            if(authority==null||!SimulationTestAuthority.CompiledCapability||plan==null||targets==null||engine==null||receiptPath==null||monotonicMs==null||utcNow==null||lastCommittedDataSha256==null||audit==null||
                sessionNonce==null||!Regex.IsMatch(sessionNonce,@"\A[0-9a-f]{32}\z"))throw new SessionFault("JOIN_SIMULATION_FAULT_INVALID");
            this.authority=authority;this.plan=plan;this.targets=targets;this.engine=engine;path=receiptPath;nonce=sessionNonce;mono=monotonicMs;utc=utcNow;lastCommitted=lastCommittedDataSha256;this.audit=audit;
        }
        bool PlannedCurrent()=>plan.PlannedOpportunityId!=null&&engine.CurrentOpportunityId==plan.PlannedOpportunityId;
        // Arm-type hooks are installed before the planned opportunity loads; the
        // others fire inside its cue/response interval, where the real checks run.
        bool Eligible()
        {
            if(engine.Status!=SessionState.Running)return false;
            var state=engine.CurrentState;
            switch(plan.Scenario)
            {
                case "corrupt_file_hash":return true;
                case "presentation_stall":case "input_loss":case "missing_response_log":return PlannedCurrent()&&state==ItemState.ResponseOpen;
                case "audio_underrun":return PlannedCurrent()&&state is ItemState.CueRequested or ItemState.ResponseOpen;
                case "failed_reset":return PlannedCurrent()&&state is ItemState.CueRequested or ItemState.ResponseOpen or ItemState.Closed;
                case "headset_disconnect":return plan.PlannedOpportunityId==null||PlannedCurrent()&&state>=ItemState.CueRequested&&state<ItemState.Done;
                default:return false;
            }
        }
        // These hooks fire later, inside their predicate, so the planned
        // opportunity is the only one they can affect.
        bool ArmType=>plan.Scenario is "corrupt_file_hash" or "missing_response_log" or "failed_reset";
        string requestOpportunity;
        internal void Tick()
        {
            if(written||applying)return;
            bool planned=PlannedCurrent();if(planned)seenPlanned=true;
            if(!requested)
            {
                if(seenPlanned&&!planned){Finish("not_applied","SIMULATION_FAULT_WINDOW_MISSED",null);return;}
                if(!Eligible())return;
                requested=true;requestedMono=mono();requestedUtc=utc();prefix=lastCommitted();requestOpportunity=engine.CurrentOpportunityId;
                audit(new JObject{["kind"]="injection_requested",["case_id"]=plan.CaseId,["scenario"]=plan.Scenario,["hook"]=plan.Hook,["plan_sha256"]=plan.RawSha256,
                    ["planned_opportunity_id"]=plan.PlannedOpportunityId,["current_opportunity_id"]=requestOpportunity,["last_committed_data_sha256"]=prefix,["participant_admission"]=false});
            }
            FaultStep step;applying=true;
            try{step=targets.Step(plan.Scenario,PlannedCurrent);}
            catch(Exception error){step=FaultStep.Refused(error is SessionFault s?s.Code:error is AudioFault a?a.Code:error is FrameFault f?f.Code:error is ControlFault c?c.Code:"SIMULATION_FAULT_HOOK_FAILED");}
            finally{applying=false;}
            if(step.State==FaultApplication.Pending)
            {
                if(seenPlanned&&!PlannedCurrent())Finish("not_applied","SIMULATION_FAULT_WINDOW_MISSED",null);
                return;
            }
            Resolve(step);
        }
        void Resolve(FaultStep step)
        {
            bool applied=step.State==FaultApplication.Applied;
            appliedOpportunity=!applied?null:ArmType?plan.PlannedOpportunityId:requestOpportunity;
            Finish(applied?"applied":"not_applied",step.RefusalCode,step.Parameters);
        }
        // Closing before resolution never invents success.
        internal void Close()
        {
            if(written||applying)return;
            if(!requested){Finish("not_applied","SIMULATION_FAULT_NOT_REACHED",null);return;}
            JObject parameters=null;try{parameters=targets.AppliedParameters;}catch{}
            if(parameters!=null){Resolve(FaultStep.Applied(parameters));return;}
            Finish(plan.Scenario=="input_loss"?"unknown":"not_applied","SIMULATION_FAULT_UNRESOLVED_AT_CLOSE",null);
        }
        static string Utc(DateTime value)=>value.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss.ffffff'Z'",CultureInfo.InvariantCulture);
        void Finish(string outcome,string refusal,JObject parameters)
        {
            if(written)return;written=true;
            try{targets.Disarm(plan.Scenario);}catch{}
            double observedMono=mono();DateTime observedUtc=utc();
            if(!requested){requestedMono=observedMono;requestedUtc=observedUtc;prefix=lastCommitted();}
            var receipt=new JObject{["version"]=1,["scope"]="SIMULATION_TEST",["case_id"]=plan.CaseId,["scenario"]=plan.Scenario,["plan_sha256"]=plan.RawSha256,
                ["session_nonce"]=nonce,["simulation_capability_sha256"]=authority.RawSha256,["config_sha256"]=plan.ConfigSha256,["schedule_sha256"]=plan.ScheduleSha256,
                ["planned_opportunity_id"]=plan.PlannedOpportunityId,["method"]="software_harness",["hook"]=plan.Hook,["parameters"]=parameters??new JObject(),
                ["requested_utc"]=Utc(requestedUtc),["observed_utc"]=Utc(observedUtc),["outcome"]=outcome,["refusal_code"]=outcome=="applied"?null:refusal??"SIMULATION_FAULT_NOT_APPLIED",
                ["requested_host_mono_ms"]=requestedMono,["observed_host_mono_ms"]=observedMono,["applied_opportunity_id"]=outcome=="applied"?appliedOpportunity:null,
                ["last_committed_data_sha256"]=prefix,["participant_admission"]=false};
            try{audit(new JObject{["kind"]="injection_resolved",["case_id"]=plan.CaseId,["outcome"]=outcome,["refusal_code"]=receipt["refusal_code"].DeepClone(),["parameters"]=receipt["parameters"].DeepClone()});}catch{}
            byte[] bytes=new UTF8Encoding(false).GetBytes(receipt.ToString(Formatting.Indented).Replace("\r\n","\n")+"\n");
            string pending=path+".pending";
            using(var stream=new FileStream(pending,FileMode.CreateNew,FileAccess.Write,FileShare.Read,4096,FileOptions.WriteThrough)){stream.Write(bytes,0,bytes.Length);stream.Flush(true);}
            File.Move(pending,path);
            Debug.Log("SIMULATION_FAULT_INJECTION "+plan.Scenario+" outcome="+outcome+" participant_admission=false");
        }
    }
    // Real component hooks for the joined SIMULATION_TEST player.
    internal sealed class NativeFaultTargets:ISimulationFaultTargets
    {
        public const int StallMs=400,AudioStallMs=500,InputLossMs=3000;
        readonly SimulationTestAuthority authority;readonly FrameCaptureHost frames;readonly AudioPlayer player;readonly LoadedAudioPackage package;
        readonly DataJournal data;readonly Func<PrivateModeResetClient> control;readonly ResponsePanelController panel;
        bool armed;JObject applied;PrivateModeResetClient armedControl;
        public JObject AppliedParameters=>applied;
        internal NativeFaultTargets(SimulationTestAuthority authority,FrameCaptureHost frames,AudioPlayer player,LoadedAudioPackage package,DataJournal data,Func<PrivateModeResetClient> control,ResponsePanelController panel)
        {
            if(authority==null||!SimulationTestAuthority.CompiledCapability)throw new SessionFault("JOIN_SIMULATION_FAULT_INVALID");
            this.authority=authority;this.frames=frames;this.player=player;this.package=package;this.data=data;this.control=control;this.panel=panel;
        }
        public FaultStep Step(string scenario,Func<bool> plannedCurrent)
        {
            switch(scenario)
            {
                case "presentation_stall":
                    if(applied!=null)return FaultStep.Applied(applied);
                    frames.InjectStall(StallMs); // Existing engineering hook; refused unless frame setup opts in.
                    return FaultStep.Applied(applied=new JObject{["stall_ms"]=StallMs});
                case "audio_underrun":
                    if(applied!=null)return FaultStep.Applied(applied);
                    return player.SimulationStallAudioThread(authority,AudioStallMs)==null?FaultStep.Applied(applied=new JObject{["audio_thread_stall_ms"]=AudioStallMs}):FaultStep.Pending;
                case "corrupt_file_hash":
                    if(!armed){armed=true;package.SimulationCorruptReads(authority,plannedCurrent,offset=>applied??=new JObject{["flipped_byte_offset"]=offset});}
                    return applied!=null?FaultStep.Applied(applied):FaultStep.Pending;
                case "missing_response_log":
                    if(!armed){armed=true;data.SimulationRefuseNext(authority,"panel_response",plannedCurrent,()=>applied??=new JObject{["refused_event_type"]="panel_response"});}
                    return applied!=null?FaultStep.Applied(applied):FaultStep.Pending;
                case "failed_reset":
                    if(!armed){var c=control();if(c==null)return FaultStep.Pending;armed=true;armedControl=c;c.SimulationWithholdNextResetReply(authority,id=>applied??=new JObject{["withheld_request_id"]=id});}
                    return applied!=null?FaultStep.Applied(applied):FaultStep.Pending;
                case "input_loss":
                    if(!armed){armed=true;panel.SimulationSuppressInput(authority,InputLossMs);}
                    return applied!=null||!panel.InputAvailable?FaultStep.Applied(applied??=new JObject{["suppress_ms"]=InputLossMs}):FaultStep.Pending;
                case "headset_disconnect":
                    if(applied!=null)return FaultStep.Applied(applied);
                    applied=new JObject{["dispatch"]="OnApplicationFocus(false)"};DispatchFocusLoss();return FaultStep.Applied(applied);
                default:return FaultStep.Refused("SIMULATION_FAULT_SCENARIO_UNKNOWN");
            }
        }
        // Deliver the same message Unity sends on an OS focus change to every
        // live scene object; each component's own handler decides the response.
        internal static int DispatchFocusLoss()
        {
            var targets=UnityEngine.Object.FindObjectsByType<MonoBehaviour>(FindObjectsInactive.Exclude,FindObjectsSortMode.None).Select(x=>x.gameObject).Distinct().ToList();
            foreach(var target in targets)if(target!=null)target.SendMessage("OnApplicationFocus",false,SendMessageOptions.DontRequireReceiver);
            return targets.Count;
        }
        public void Disarm(string scenario)
        {
            if(scenario=="corrupt_file_hash"&&armed)package.SimulationCorruptReads(authority,null,null);
            if(scenario=="missing_response_log"&&armed&&applied==null)data.SimulationRefuseNext(authority,null,null,null);
            if(scenario=="failed_reset"&&armedControl!=null&&applied==null)try{armedControl.SimulationCancelWithheldReset(authority);}catch{}
        }
    }
}
