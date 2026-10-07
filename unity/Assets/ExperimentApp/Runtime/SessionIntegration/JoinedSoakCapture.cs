using System;
using System.IO;
using System.Text;
using AcousticVocab.DataLogging;
using AcousticVocab.Foundation;
using AcousticVocab.Soak;
using AcousticVocab.StateSources;
using AcousticVocab.StudyAudio;
using UnityEngine;
namespace AcousticVocab.SessionIntegration
{
    // Observation only. The plan is independently pinned and can never change
    // source, mode, readiness, allocation, calibration or participant admission.
    // With -soakInputs/-soakSchedule it also consumes the soak driver's input
    // feed, but only in the explicitly compiled simulation-test player.
    [DefaultExecutionOrder(100)]
    [DisallowMultipleComponent]
    public sealed class JoinedSoakCapture:MonoBehaviour
    {
        public JoinedEngineeringBootstrap bootstrap;
        SoakCaptureHost capture;byte[] plan,scheduleRaw;string pin,output,inputs,operatorStatus;double started;bool requested,installed,closed;
        SoakSchedule schedule;SoakInputFeed feed;SoakFeedConsumer consumer;DataJournal soakData;
        static string Argument(string key)
        {var args=Environment.GetCommandLineArgs();for(int i=0;i<args.Length-1;i++)if(args[i]==key)return args[i+1];return null;}
        // Pure gate for the feed-consuming mode. Any feed argument requires the
        // complete pair, the compiled capability, the simulation-test scene and
        // a loaded SIMULATION_TEST authority; there is no fallback mode.
        internal static string FeedRefusal(string inputs,string schedulePath,bool compiledCapability,bool simulationScene,bool authorityLoaded)
        {
            if(inputs==null&&schedulePath==null)return null;
            bool rooted(string p)=>p!=null&&Path.IsPathRooted(p)&&!p.StartsWith("\\\\")&&!p.StartsWith("//");
            if(!rooted(inputs)||!rooted(schedulePath))return "SOAK_FEED_ARGUMENTS";
            return compiledCapability&&simulationScene&&authorityLoaded?null:"SOAK_FEED_REQUIRES_SIMULATION";
        }
        internal static byte[] ReadBounded(string path,long maximum,string code)
        {
            var info=new FileInfo(path);if(!info.Exists||info.Length<=0||info.Length>maximum)throw new SoakFault(code);
            for(FileSystemInfo entry=info;entry!=null;entry=entry is FileInfo f?f.Directory:((DirectoryInfo)entry).Parent)
                if(entry.Exists&&(entry.Attributes&FileAttributes.ReparsePoint)!=0)throw new SoakFault(code);
            return File.ReadAllBytes(info.FullName);
        }
        void Start()
        {
            string path=Argument("-soakPlan");pin=Argument("-soakPlanSha256");output=Argument("-soakOutput");inputs=Argument("-soakInputs");string schedulePath=Argument("-soakSchedule");
            if(path==null&&pin==null&&output==null&&inputs==null&&schedulePath==null)return;
            try
            {
                if(bootstrap==null||path==null||pin==null||output==null)throw new SoakFault("SOAK_ARGUMENTS");
                string refusal=FeedRefusal(inputs,schedulePath,SimulationTestAuthority.CompiledCapability,bootstrap.simulationTestScene,bootstrap.SimulationAuthority!=null&&bootstrap.SoakFeedOnly);if(refusal!=null)throw new SoakFault(refusal);
                plan=ReadBounded(path,16384,"SOAK_PLAN_FILE");var loaded=SoakPlan.Load(plan,pin);
                if(inputs!=null)
                {
                    scheduleRaw=ReadBounded(schedulePath,SoakSchedule.MaximumBytes,"SOAK_SCHEDULE_FILE");schedule=SoakSchedule.Load(scheduleRaw,loaded.ScheduleSha256);
                    if(schedule.StationId!=loaded.StationId)throw new SoakFault("SOAK_SCHEDULE_STATION");
                }
                requested=true;started=Time.realtimeSinceStartupAsDouble;
            }
            catch(SoakFault e){Fault(e.Message=="SOAK_FEED_REQUIRES_SIMULATION"||e.Message=="SOAK_FEED_ARGUMENTS"?e.Message:"SOAK_CONFIG_INVALID");}
            catch{Fault("SOAK_CONFIG_INVALID");}
        }
        void Update()
        {
            if(!requested||closed)return;
            try
            {
                if(installed){Pump();return;}
                var config=bootstrap.ObservationConfig;var assets=bootstrap.ObservationAssets;
                if(config==null||assets==null||!bootstrap.foundation.Ready||!bootstrap.source.Initialized)
                {if(Time.realtimeSinceStartupAsDouble-started>30)throw new SoakFault("SOAK_STARTUP_TIMEOUT");return;}
                if(bootstrap.source.Kind!="live")throw new SoakFault("SOAK_REQUIRES_LIVE_SOURCE");
                if(schedule!=null&&!(SimulationTestAuthority.CompiledCapability&&bootstrap.simulationTestScene&&bootstrap.SimulationAuthority!=null&&bootstrap.SoakFeedOnly))throw new SoakFault("SOAK_FEED_REQUIRES_SIMULATION");
                var source=StateSourceConfiguration.Load(Encoding.UTF8.GetString(config.RequireFile("state_source").ReadVerified()));
                if(bootstrap.source.LoadedConfigurationSha256!=config.RequireFile("state_source").Sha256||bootstrap.source.LoadedNeutralSha256!=config.RequireFile("neutral").Sha256)throw new SoakFault("SOAK_LOADED_BINDING");
                capture=gameObject.AddComponent<SoakCaptureHost>();
                // In feed mode the plan pins the driver schedule; the joined
                // engine is not started, so its own schedule is not the binding.
                capture.Install(plan,pin,output,config.StationId,config.BuildId,source.SceneHash,source.NeutralHash,schedule?.Sha256??assets.Schedule.Sha256,
                    schedule==null?(Func<SoakContext>)bootstrap.ObservationContext:()=>consumer?.Context??SoakFeedConsumer.Waiting,Fault);
                bootstrap.source.FrameApplied+=capture.SourceApplied;installed=true;
                if(schedule!=null)
                {
                    var build=Resources.Load<TextAsset>("BuildIdentity");if(build==null)throw new SoakFault("SOAK_BUILD_IDENTITY");
                    soakData=new DataJournal(Path.Combine(capture.OutputDirectory,"soak-data"),new DataIdentity(Guid.NewGuid().ToString("N"),"SOAK-SYNTHETIC","soak-feed",config.StationId,config.ProtocolVersion,PcmWave.Hash(Encoding.UTF8.GetBytes(build.text))),
                        Guid.NewGuid().ToString("N"),()=>SoakCaptureHost.Now*1000);
                    consumer=new SoakFeedConsumer(schedule,new SoakSessionRecorder(soakData,schedule,()=>SoakCaptureHost.Now*1000,()=>Application.isFocused),capture.Observe,capture.ObserveContext);
                    feed=new SoakInputFeed(schedule,config.StationId,inputs);
                    Debug.Log("SOAK_FEED_BOUND participant_admission=false scope=SIMULATION_TEST rows=0");
                }
                Debug.Log("SOAK_CAPTURE_INSTALLED participant_admission=false render_basis=onBeforeRender");
            }
            catch(SoakFault e){Fault(e.Message);}catch(DataFault e){Fault(e.Code);}catch{Fault("SOAK_INSTALL_FAILED");}
        }
        void Pump()
        {
            if(feed==null)return;
            foreach(var row in feed.Poll())consumer.Accept(row);
            string status=consumer.PollOperator(capture.OutputDirectory);
            if(status!=operatorStatus){operatorStatus=status;if(status!=null)Debug.Log("SOAK_FEED_OPERATOR "+status+" fault="+(consumer.ActiveFaultId??"none")+" rows="+feed.Rows);}
        }
        void Fault(string code)
        {if(closed)return;Debug.LogError("SOAK_CAPTURE_FAULT "+code);Close();bootstrap?.ObservationFailed(code);}
        void Close()
        {
            if(closed)return;closed=true;
            if(capture!=null){if(bootstrap?.source!=null)bootstrap.source.FrameApplied-=capture.SourceApplied;capture.Finish();}
            try{soakData?.Dispose();}catch{Debug.LogError("SOAK_CAPTURE_FAULT SOAK_DATA_CLOSE_FAILED");}
        }
        void OnDisable()=>Close();void OnDestroy()=>Close();
    }
}
