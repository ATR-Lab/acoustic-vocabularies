using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using AcousticVocab.Foundation;
using AcousticVocab.ResponsePanel;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using Unity.Collections;
using UnityEngine;
using UnityEngine.XR;
using UnityEngine.XR.OpenXR.Features.Meta;

namespace AcousticVocab.FrameBudget
{
    [DisallowMultipleComponent]
    public sealed class FrameCaptureHost : MonoBehaviour
    {
        public FoundationBootstrap foundation;
        public ResponsePanelController panel;
        readonly object timingLock=new object();readonly List<XRDisplaySubsystem> displays=new List<XRDisplaySubsystem>();readonly FrameTiming[] timing=new FrameTiming[1];
        FrameSetup setup;RefreshPin refresh;FrameCsvEvidence evidence;FrameMonitor monitor;FrameDataAdapter data;Action<string> engineFault;Timer watchdog;
        volatile bool installed,closed,failed;bool rateRecorded;long lastFrame=-1;int physicsSteps;double installedMs;
        public bool Ready=>installed&&!closed&&!failed&&isActiveAndEnabled&&foundation!=null&&foundation.Ready&&refresh.Ready&&monitor.Healthy&&monitor.HasRenderSample;
        public FrameMonitor Monitor=>monitor;
        static double Now=>AudioPlayer.Now*1000;
        sealed class UnityDisplay : IDisplayRate
        {
            readonly XRDisplaySubsystem display;
            internal UnityDisplay(XRDisplaySubsystem display){this.display=display;}
            public bool Running=>display!=null&&display.running;
            public bool TryOffered(out double[] rates)
            {rates=null;if(!Running||!display.TryGetSupportedDisplayRefreshRates(Allocator.Temp,out var values))return false;using(values){rates=values.Select(x=>(double)x).ToArray();return true;}}
            public bool TryCurrent(out double hz){hz=0;if(!Running||!display.TryGetDisplayRefreshRate(out float value))return false;hz=value;return true;}
            public bool Request(double hz)=>Running&&display.TryRequestDisplayRefreshRate((float)hz);
        }
        // Explicit trusted-owner installation. This component never opens a
        // schedule, chooses a refresh rate, installs a runtime or starts a visit.
        public void Install(byte[] privateSetup,string freshEvidenceDirectory,DataLogging.DataJournal journal,Action<string> fault)
        {
            Check.That(!installed&&!closed&&isActiveAndEnabled&&foundation!=null&&foundation.Ready&&foundation.Configuration!=null&&panel!=null&&fault!=null,"FRAME_HOST_UNAVAILABLE");
            engineFault=fault;setup=FrameSetup.Load(privateSetup,foundation.Configuration);
            displays.Clear();SubsystemManager.GetSubsystems(displays);var display=displays.SingleOrDefault(x=>x.running);Check.That(display!=null,"FRAME_REFRESH_UNAVAILABLE");
            refresh=new RefreshPin(setup,new UnityDisplay(display));refresh.Begin();
            var metadata=new JObject{["version"]=1,["clock"]="host_Stopwatch_absolute_ms",["stopwatch_frequency"]=System.Diagnostics.Stopwatch.Frequency,
                ["capture_kind"]="application_onBeforeRender_callbacks_not_photon_timestamps",["frame_timing_metrics"]="delayed_Unity_CPU_GPU_statistics",
                ["frame_timing_enabled"]=FrameTimingManager.IsFeatureEnabled(),["selected_refresh_hz"]=setup.RefreshHz,["refresh_control"]=setup.Control,["setup_sha256"]=setup.Sha256,
                ["station_id"]=foundation.Configuration["station_id"].DeepClone(),["protocol_version"]=foundation.Configuration["protocol_version"].DeepClone(),
                ["observer_reference"]=foundation.Configuration["observer_reference"].DeepClone(),["unity_fixed_step_seconds"]=Time.fixedDeltaTime,
                ["isaac_physics_hz"]=JValue.CreateNull(),["headset_photon_interval_ms"]=JValue.CreateNull(),["physical_qualification"]=false};
            evidence=new FrameCsvEvidence(freshEvidenceDirectory,metadata);monitor=new FrameMonitor(setup.RefreshHz,evidence);data=new FrameDataAdapter(journal,fault);installedMs=Now;installed=true;
            Application.onBeforeRender+=BeforeRender;
            watchdog=new Timer(_=>Watchdog(),null,setup.WatchdogPollMs,setup.WatchdogPollMs);
        }
        void Watchdog()
        {
            lock(timingLock)
            {
                if(closed||!installed)return;
                try{monitor.Watchdog(Now);}catch{failed=true;}
            }
        }
        void FixedUpdate(){if(installed&&!closed)physicsSteps++;}
        void BeforeRender()
        {
            if(!installed||closed||lastFrame==Time.frameCount)return;
            try
            {
                lock(timingLock)
                {
                    if(closed)return;
                    refresh.Observe();if(!refresh.Ready&&Now-installedMs>5000)throw new FrameFault("FRAME_REFRESH_UNCONFIRMED");
                    if(refresh.Ready&&!rateRecorded){evidence.Rate(new JObject{["version"]=1,["offered_hz"]=new JArray(refresh.Offered),["request_accepted"]=refresh.RequestAccepted,["reported_hz"]=refresh.CurrentHz,["selected_hz"]=setup.RefreshHz,["render_eye_texture_width"]=XRSettings.eyeTextureWidth,["render_eye_texture_height"]=XRSettings.eyeTextureHeight,["physical_display_resolution"]=JValue.CreateNull(),["qualification"]="runtime_report_only"});rateRecorded=true;}
                    uint n=FrameTimingManager.GetLatestTimings(1,timing);FrameTimingManager.CaptureFrameTimings();
                    var display=displays.SingleOrDefault(x=>x.running);int? presented=null,dropped=null;
                    if(display!=null&&display.TryGetFramePresentCount(out int p))presented=p;
                    if(display!=null&&display.TryGetDroppedFrameCount(out int d))dropped=d;
                    lastFrame=Time.frameCount;
                    monitor.Render(Now,new RenderSample(lastFrame,n>0&&timing[0].cpuFrameTime>0?(double?)timing[0].cpuFrameTime:null,n>0&&timing[0].gpuFrameTime>0?(double?)timing[0].gpuFrameTime:null,refresh.CurrentHz,Time.fixedDeltaTime*1000,physicsSteps,presented,dropped),panel.InputAvailable&&!panel.FaultLatched&&foundation.Ready);
                    physicsSteps=0;
                }
            }
            catch(FrameFault error){Fail(error.Code);}catch{Fail("FRAME_RUNTIME_FAILED");}
        }
        void Update(){if(installed&&!closed)Drain();}
        public void Drain()
        {
            if(!installed||closed)return;
            try{data.Drain(monitor);if(failed)Fail("FRAME_LOG_FAILED");}
            catch(FrameFault error){Fail(error.Code);}catch{Fail("FRAME_LOG_FAILED");}
        }
        public void Register(SlotContext context)
        {
            Check.That(Ready&&context.Item!=null,"FRAME_CAPTURE_UNAVAILABLE");lock(timingLock)
                monitor.Register(new FrameAttempt(context.OpportunityId,context.Item.TrialId,context.OnsetMonoMs,context.EndMonoMs,context.Item.Plays),
                    new FrameWindow("response","response",context.OnsetMonoMs+context.Item.ResponseOpensSeconds*1000,context.OnsetMonoMs+context.Item.ResponseClosesSeconds*1000),Now);
        }
        public void RegisterCue(string attempt,FrameWindow cue){Check.That(installed&&!closed,"FRAME_CAPTURE_UNAVAILABLE");lock(timingLock)monitor.Cue(attempt,cue,Now);}
        public void Cancel(string attempt){if(!installed||closed)return;lock(timingLock)monitor.Cancel(attempt,Now);}
        // Explicit engineering hook, bounded and unavailable unless opted in.
        // This intentionally stalls this application's main thread only.
        public void InjectStall(int milliseconds)
        {Check.That(installed&&!closed&&setup.StallHook&&milliseconds>=1&&milliseconds<=1000,"FRAME_INJECTION_REFUSED");Thread.Sleep(milliseconds);}
        void Fail(string code)
        {
            if(closed)return;failed=true;
            try{Shutdown(code);}finally{engineFault?.Invoke(code);}
        }
        void Shutdown(string code)
        {
            if(closed)return;closed=true;Application.onBeforeRender-=BeforeRender;watchdog?.Dispose();watchdog=null;
            Exception first=null;
            lock(timingLock)
            {
                try{monitor?.CloseIncomplete(Now,code);}catch(Exception e){first=e;}
                try{if(data!=null&&monitor!=null)data.Drain(monitor);}catch(Exception e){first??=e;}
                try{evidence?.Dispose();}catch(Exception e){first??=e;}
            }
            if(first!=null)throw new FrameFault("FRAME_LOG_FAILED");
        }
        void OnDisable(){if(installed)Fail("FRAME_HOST_DISABLED");}
        void OnDestroy(){if(installed&&!closed)Fail("FRAME_HOST_DESTROYED");}
    }
}
