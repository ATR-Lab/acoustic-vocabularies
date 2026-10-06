using System;
using System.IO;
using System.Text;
using AcousticVocab.Soak;
using AcousticVocab.StateSources;
using UnityEngine;
namespace AcousticVocab.SessionIntegration
{
    // Observation only. The plan is independently pinned and can never change
    // source, mode, readiness, allocation, calibration or participant admission.
    [DefaultExecutionOrder(100)]
    [DisallowMultipleComponent]
    public sealed class JoinedSoakCapture:MonoBehaviour
    {
        public JoinedEngineeringBootstrap bootstrap;
        SoakCaptureHost capture;byte[] plan;string pin,output;double started;bool requested,installed,closed;
        static string Argument(string key)
        {var args=Environment.GetCommandLineArgs();for(int i=0;i<args.Length-1;i++)if(args[i]==key)return args[i+1];return null;}
        void Start()
        {
            string path=Argument("-soakPlan");pin=Argument("-soakPlanSha256");output=Argument("-soakOutput");
            if(path==null&&pin==null&&output==null)return;
            try
            {
                if(bootstrap==null||path==null||pin==null||output==null)throw new SoakFault("SOAK_ARGUMENTS");
                var info=new FileInfo(path);if(!info.Exists||info.Length<=0||info.Length>16384)throw new SoakFault("SOAK_PLAN_FILE");
                for(FileSystemInfo entry=info;entry!=null;entry=entry is FileInfo f?f.Directory:((DirectoryInfo)entry).Parent)
                    if(entry.Exists&&(entry.Attributes&FileAttributes.ReparsePoint)!=0)throw new SoakFault("SOAK_PLAN_LINK");
                plan=File.ReadAllBytes(info.FullName);SoakPlan.Load(plan,pin);requested=true;started=Time.realtimeSinceStartupAsDouble;
            }
            catch{Fault("SOAK_CONFIG_INVALID");}
        }
        void Update()
        {
            if(!requested||installed||closed)return;
            try
            {
                var config=bootstrap.ObservationConfig;var assets=bootstrap.ObservationAssets;
                if(config==null||assets==null||!bootstrap.foundation.Ready||!bootstrap.source.Initialized)
                {if(Time.realtimeSinceStartupAsDouble-started>30)throw new SoakFault("SOAK_STARTUP_TIMEOUT");return;}
                if(bootstrap.source.Kind!="live")throw new SoakFault("SOAK_REQUIRES_LIVE_SOURCE");
                var source=StateSourceConfiguration.Load(Encoding.UTF8.GetString(config.RequireFile("state_source").ReadVerified()));
                if(bootstrap.source.LoadedConfigurationSha256!=config.RequireFile("state_source").Sha256||bootstrap.source.LoadedNeutralSha256!=config.RequireFile("neutral").Sha256)throw new SoakFault("SOAK_LOADED_BINDING");
                capture=gameObject.AddComponent<SoakCaptureHost>();
                capture.Install(plan,pin,output,config.StationId,config.BuildId,source.SceneHash,source.NeutralHash,assets.Schedule.Sha256,bootstrap.ObservationContext,Fault);
                bootstrap.source.FrameApplied+=capture.SourceApplied;installed=true;
                Debug.Log("SOAK_CAPTURE_INSTALLED participant_admission=false render_basis=onBeforeRender");
            }
            catch(SoakFault e){Fault(e.Message);}catch{Fault("SOAK_INSTALL_FAILED");}
        }
        void Fault(string code)
        {if(closed)return;Debug.LogError("SOAK_CAPTURE_FAULT "+code);Close();bootstrap?.ObservationFailed(code);}
        void Close()
        {
            if(closed)return;closed=true;
            if(capture!=null){if(bootstrap?.source!=null)bootstrap.source.FrameApplied-=capture.SourceApplied;capture.Finish();}
        }
        void OnDisable()=>Close();void OnDestroy()=>Close();
    }
}
