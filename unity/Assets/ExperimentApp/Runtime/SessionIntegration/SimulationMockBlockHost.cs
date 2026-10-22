using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.DataLogging;
using AcousticVocab.Foundation;
using AcousticVocab.OperatorConsole;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;
namespace AcousticVocab.SessionIntegration
{
    // SIMULATION_TEST elapsed mock block for the #67 process-kill harness
    // (tools/session-kill/run-kill-resume.ps1). The real FixedSlotEngine,
    // DataJournal, operator mailbox and AudioPlayer run on the real Stopwatch
    // clock with synthetic MOCK-nn slots and a silent synthetic cue. There is no
    // backend, panel, package or participant content, and it never runs without
    // -simulationMockBlock plus the compiled simulation capability.
    [DisallowMultipleComponent]
    public sealed class SimulationMockBlockHost:MonoBehaviour
    {
        public AudioPlayer player;
        public const string ResultPrefix="mock-block-result-",StartPrefix="mock-block-start-";
        public string StatusCode{get;private set;}="MOCK_BLOCK_NOT_REQUESTED";
        internal FixedSlotEngine Engine=>engine;
        SimulationTestAuthority authority;VisitSchedule schedule;DataJournal data;FileOperatorCommandJournal commands;OperatorMailbox mailbox;AudioDataAdapter audio;FixedSlotEngine engine;
        string root,nonce,dataEpoch;bool closed,active;PcmWave wave;
        readonly Dictionary<string,AudioRequestContext> requests=new Dictionary<string,AudioRequestContext>(StringComparer.Ordinal);
        sealed class Clock:ISessionClock{public double NowMs=>AudioPlayer.Now*1000;}
        readonly Clock clock=new Clock();
        static string Argument(string key){var args=Environment.GetCommandLineArgs();for(int i=0;i<args.Length-1;i++)if(args[i]==key)return args[i+1];return null;}
        static bool Has(string key)=>Environment.GetCommandLineArgs().Contains(key);
        // The run root must be a fresh-or-resumed directory inside the
        // capability's dedicated simulation output root, never another store.
        internal static string ValidateRoot(SimulationTestAuthority authority,string requested)
        {
            if(authority==null||!SimulationTestAuthority.CompiledCapability||requested==null||!Path.IsPathRooted(requested)||requested.StartsWith("\\\\")||requested.StartsWith("//"))throw new SessionFault("MOCK_BLOCK_ROOT_INVALID");
            string full=Path.GetFullPath(requested).TrimEnd(Path.DirectorySeparatorChar,Path.AltDirectorySeparatorChar);
            string output=authority.OutputDirectory.TrimEnd(Path.DirectorySeparatorChar,Path.AltDirectorySeparatorChar)+Path.DirectorySeparatorChar;
            if(!full.StartsWith(output,StringComparison.OrdinalIgnoreCase))throw new SessionFault("MOCK_BLOCK_ROOT_INVALID");
            for(string at=full;at!=null;at=Path.GetDirectoryName(at))
                try{if((File.GetAttributes(at)&FileAttributes.ReparsePoint)!=0)throw new SessionFault("MOCK_BLOCK_ROOT_INVALID");}catch(FileNotFoundException){}catch(DirectoryNotFoundException){}
            return full;
        }
        internal static int ParseCount(string value){if(value==null)return 6;if(!int.TryParse(value,out int n)||n<2||n>36)throw new SessionFault("MOCK_BLOCK_ITEMS_INVALID");return n;}
        // Stable across relaunches so one durable journal spans both processes.
        internal static DataIdentity Identity(VisitSchedule schedule,string root,string protocol,string buildSha256)
            =>new DataIdentity(PcmWave.Hash(Encoding.UTF8.GetBytes("mock-block|"+schedule.Sha256+"|"+root.ToLowerInvariant())).Substring(0,32),"DEMO-MOCK","MOCK","simulation-mock-block",protocol,buildSha256);
        internal static PcmWave SilentCue()
        {
            const int samples=24000; // 0.5 s of silence; no study material.
            using var stream=new MemoryStream();using var writer=new BinaryWriter(stream);
            writer.Write(Encoding.ASCII.GetBytes("RIFF"));writer.Write(36+samples*2);writer.Write(Encoding.ASCII.GetBytes("WAVEfmt "));
            writer.Write(16);writer.Write((short)1);writer.Write((short)1);writer.Write(48000);writer.Write(96000);
            writer.Write((short)2);writer.Write((short)16);writer.Write(Encoding.ASCII.GetBytes("data"));writer.Write(samples*2);writer.Write(new byte[samples*2]);
            return PcmWave.ParseCanonical(stream.ToArray());
        }
        void Start()
        {
            string requested=Argument("-simulationMockBlock");
            if(requested==null){enabled=false;return;}
            try
            {
                if(!SimulationTestAuthority.CompiledCapability)throw new SessionFault("MOCK_BLOCK_SIMULATION_BUILD_REQUIRED");
                if(Argument("-joinedConfig")!=null||Argument("-simulationFaultPlan")!=null)throw new SessionFault("MOCK_BLOCK_EXCLUSIVE");
                string capPath=Argument("-simulationTestConfig"),capPin=Argument("-simulationTestConfigSha256");if(capPath==null||capPin==null||player==null)throw new SessionFault("MOCK_BLOCK_CAPABILITY_REQUIRED");
                var build=Resources.Load<TextAsset>("BuildIdentity");if(build==null)throw new SessionFault("MOCK_BLOCK_BUILD_IDENTITY");var identity=JObject.Parse(build.text);
                authority=SimulationTestAuthority.Load(capPath,capPin,(string)identity["build_id"],(string)identity["protocol_version"]);
                root=ValidateRoot(authority,requested);schedule=VisitSchedule.SyntheticMockBlock(ParseCount(Argument("-simulationMockBlockItems")));
                foreach(string d in new[]{"data","mailbox","operator"})Directory.CreateDirectory(Path.Combine(root,d));
                AudioPlayer.ConfigureSimulationDevice(authority);
                nonce=Guid.NewGuid().ToString("N");dataEpoch=Guid.NewGuid().ToString("N");
                data=new DataJournal(Path.Combine(root,"data"),Identity(schedule,root,(string)identity["protocol_version"],PcmWave.Hash(Encoding.UTF8.GetBytes(build.text))),dataEpoch,()=>clock.NowMs);
                // Recover() runs here: a durably requested cue is consumed and
                // skipped; the session then waits for an explicit operator command.
                engine=new FixedSlotEngine(schedule,clock,new SessionDataJournal(data),new Factory(this));
                wave=SilentCue();
                audio=new AudioDataAdapter(data,player,e=>requests.TryGetValue(e.AudioId,out var value)?value:null);
                player.Configure(AudioRouteCalibration.ForSimulation(authority),()=>!closed);player.SetComfortableGain(authority.AudioGain);
                player.Preload(new Dictionary<string,PcmWave>{["warmup"]=wave},4*1024*1024);
                commands=new FileOperatorCommandJournal(Path.Combine(root,"operator"),nonce);
                mailbox=new OperatorMailbox(Path.Combine(root,"mailbox"),nonce,schedule.Sha256,engine,commands,
                    ()=>new OperatorAdmission(true,true,!data.Failed),
                    ()=>new OperatorHealth(true,player.TrialReady||player.Playing,true,true,0,0,0),()=>clock.NowMs);
                WriteFresh(Path.Combine(root,StartPrefix+nonce+".local.json"),new JObject{["version"]=1,["scope"]="SIMULATION_TEST",["session_nonce"]=nonce,
                    ["process_id"]=Process.GetCurrentProcess().Id,["data_clock_epoch"]=dataEpoch,["schedule_sha256"]=schedule.Sha256,["items"]=schedule.Blocks[0].Items.Count,
                    ["recovered_records"]=data.Records.Count,["recovered_completed"]=engine.CompletedOpportunities,["simulation_capability_sha256"]=authority.RawSha256,
                    ["mock_content"]=true,["participant_admission"]=false,["acoustic_qualification"]=false});
                active=true;Report("MOCK_BLOCK_AWAITING_OPERATOR");
            }
            catch(SessionFault error){Fail(error.Code);}catch(Exception){Fail("MOCK_BLOCK_START_FAILED");}
        }
        void Update()
        {
            if(!active||closed)return;
            try
            {
                mailbox.Tick(); // sole engine.Tick owner
                if(engine.Status==SessionState.Complete)Close("MOCK_BLOCK_COMPLETE",true);
                else if(engine.Status==SessionState.Stopped)Close("MOCK_BLOCK_STOPPED",true);
                else Report("MOCK_BLOCK_"+engine.Status.ToString().ToUpperInvariant());
            }
            catch(SessionFault error){Fail(error.Code);}catch(Exception){Fail("MOCK_BLOCK_RUNTIME_FAILED");}
        }
        void Bind(SlotContext c)
        {
            string id=c.AudioRequestIds[0];
            requests[id]=new AudioRequestContext(new EventContext(c.OpportunityId,c.Item.TrialId,id),id,wave.FileSha256);
            player.Preload(new Dictionary<string,PcmWave>{[id]=wave},4*1024*1024);
        }
        sealed class Factory:ISlotContentFactory{readonly SimulationMockBlockHost host;internal Factory(SimulationMockBlockHost host){this.host=host;}public ISlotContent Create(SlotItem item)=>new MockBlockContent(host);}
        // Synthetic content: hash/reset/render/panel/focus/input gates are
        // mock values. Only the actual audio request/output path is exercised.
        sealed class MockBlockContent:ISlotContent
        {
            readonly SimulationMockBlockHost host;bool prepared,reset;
            internal MockBlockContent(SimulationMockBlockHost host){this.host=host;}
            public SlotReadiness Readiness=>new SlotReadiness(prepared,prepared&&host.player.TrialReady,true,true,true,true,true,true);
            public bool ResetComplete=>reset;
            public void Prepare(SlotContext c){host.Bind(c);prepared=true;}
            public void RequestCue(SlotContext c,INovelSlotAuthorization permit)=>host.player.Schedule(c.AudioRequestIds[0],c.OnsetMonoMs/1000d);
            public void OpenResponse(SlotContext c){}
            public void CloseResponse(SlotContext c){}
            public void RequestReset(SlotContext c){reset=true;}
            public void Interrupt(string code){if(host.player.Playing)host.player.Abort(code);}
        }
        void Report(string code){if(StatusCode==code)return;StatusCode=code;UnityEngine.Debug.Log("SIMULATION_MOCK_BLOCK_STATUS "+code+" participant_admission=false");}
        void Fail(string code){if(closed)return;Report(code);try{engine?.Fault(code);}catch{}Close(code,true);}
        static void WriteFresh(string path,JObject value)
        {
            byte[] bytes=new UTF8Encoding(false).GetBytes(value.ToString(Formatting.None)+"\n");string pending=path+".pending";
            using(var stream=new FileStream(pending,FileMode.CreateNew,FileAccess.Write,FileShare.Read,4096,FileOptions.WriteThrough)){stream.Write(bytes,0,bytes.Length);stream.Flush(true);}
            File.Move(pending,path);
        }
        void Close(string status,bool quit)
        {
            if(closed)return;closed=true;active=false;StatusCode=status;
            bool cleanup=true;string exportPin=null;
            foreach(Action action in new Action[]{()=>mailbox?.Dispose(),()=>{if(player!=null&&player.Playing)player.Abort("MOCK_BLOCK_CLOSED");},()=>audio?.Dispose(),()=>commands?.Dispose(),()=>data?.Dispose()})
                try{action();}catch{cleanup=false;}
            if(data!=null&&root!=null)
                try{var bundle=ExportBundle.Create(Path.Combine(root,"data"),Path.Combine(root,"export-"+nonce),data.Identity,ExportHeaders.Provisional());bundle.VerifyAll();exportPin=bundle.ManifestSha256;}catch{}
            if(root!=null&&nonce!=null)
                try{WriteFresh(Path.Combine(root,ResultPrefix+nonce+".local.json"),new JObject{["version"]=1,["scope"]="SIMULATION_TEST",["session_nonce"]=nonce,
                    ["process_id"]=Process.GetCurrentProcess().Id,["status"]=status,["cleanup_succeeded"]=cleanup,["export_manifest_sha256"]=exportPin,
                    ["completed_opportunities"]=engine?.CompletedOpportunities,["host_mono_ms"]=clock.NowMs,["mock_content"]=true,["participant_admission"]=false});}catch{}
            UnityEngine.Debug.Log("SIMULATION_MOCK_BLOCK_STATUS "+status+" export="+(exportPin??"none")+" participant_admission=false");
            if(quit&&Has("-simulationMockBlockQuit"))Application.Quit(status=="MOCK_BLOCK_COMPLETE"||status=="MOCK_BLOCK_STOPPED"?(exportPin!=null&&cleanup?0:1):2);
        }
        void OnDestroy(){if(active)Close("MOCK_BLOCK_DESTROYED",false);}
    }
}
