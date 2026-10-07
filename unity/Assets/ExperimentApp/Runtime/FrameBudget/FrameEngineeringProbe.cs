using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using AcousticVocab.Foundation;
using AcousticVocab.ResponsePanel;
using AcousticVocab.StateIntegration;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.XR;
using UnityEngine.XR.OpenXR;

namespace AcousticVocab.FrameBudget
{
    // Arguments, synthetic windows and result shape of the engineering probe.
    // One injection per run: a stall (0|200|300 ms) or, with stall 0, the loss
    // of the response-panel interface while frames continue.
    public sealed class FrameProbePlan
    {
        public const double ResponseStartMs=1000,ResponseEndMs=10000,AttemptEndMs=11000,InjectAfterMs=4000,FinishAfterMs=11500;
        public const string PanelLossSwitch="-frameProbePanelLoss";
        public string Output{get;}public int StallMs{get;}public bool PanelLoss{get;}
        public string Injection=>PanelLoss?"panel_loss":StallMs>0?"stall":"none";
        FrameProbePlan(string output,int stall,bool panelLoss){Output=output;StallMs=stall;PanelLoss=panelLoss;}
        public static FrameProbePlan Parse(string[] args)
        {
            Check.That(args!=null,"FRAME_PROBE_ARGUMENTS");
            string Value(string key)
            {
                Check.That(args.Count(a=>a==key)<=1,"FRAME_PROBE_ARGUMENTS");
                int i=Array.IndexOf(args,key);return i>=0&&i+1<args.Length?args[i+1]:null;
            }
            string output=Value("-frameProbeOutput"),stallText=Value("-frameProbeStall");
            int loss=Array.IndexOf(args,PanelLossSwitch);
            // A bare switch: a following value (for example "0") is refused, never read as "off".
            Check.That(args.Count(a=>a==PanelLossSwitch)<=1&&(loss<0||loss+1==args.Length||args[loss+1].StartsWith("-",StringComparison.Ordinal)),"FRAME_PROBE_ARGUMENTS");
            bool parsed=int.TryParse(stallText,System.Globalization.NumberStyles.None,System.Globalization.CultureInfo.InvariantCulture,out int stall);
            Check.That(!string.IsNullOrWhiteSpace(output)&&!output.StartsWith("-",StringComparison.Ordinal)&&
                parsed&&new[]{0,200,300}.Contains(stall)&&(loss<0||stall==0),"FRAME_PROBE_ARGUMENTS");
            return new FrameProbePlan(output,stall,loss>=0);
        }
        public FrameAttempt Attempt(double start)=>new FrameAttempt("engineering-probe","engineering-probe",start+ResponseStartMs,start+AttemptEndMs,0);
        public FrameWindow Response(double start)=>new FrameWindow("response","response",start+ResponseStartMs,start+ResponseEndMs);
        public JObject Describe(JObject metadata)
        {metadata["requested_stall_ms"]=StallMs;metadata["requested_panel_loss"]=PanelLoss;metadata["injection"]=Injection;return metadata;}
        public JObject Result(string code,int exit,int callbacks,bool captured,FrameMonitor monitor,double? injectedAtMs)
            =>new JObject{["version"]=1,["code"]=code,["exit_code"]=exit,["actual_render_callbacks"]=callbacks,["captured"]=captured,["maximum_observed_interval_ms"]=monitor.MaximumMs,["faulted"]=!monitor.Healthy,["injection"]=Injection,["injected_mono_ms"]=injectedAtMs,["physical_qualification"]=false};
    }

    // Installed only in the separately named engineering scene. There is no
    // session allocation or audio API, and no inferred headset qualification.
    public sealed class FrameEngineeringProbe:MonoBehaviour
    {
        public FoundationBootstrap foundation;public StateSourceHost source;public ResponsePanelController panel;
        FrameCsvEvidence evidence;FrameMonitor monitor;FrameProbePlan plan;string output;long frame=-1;double enabledAt,start;double? injectedAt;bool injected,finished,captured;int callbacks,physicsSteps;readonly FrameTiming[] timings=new FrameTiming[1];double? reportedHz;
        static double Now=>AudioPlayer.Now*1000;
        void Start()
        {
            enabledAt=Now;
            try{plan=FrameProbePlan.Parse(Environment.GetCommandLineArgs());output=plan.Output;}catch(FrameFault){Finish(2,"FRAME_PROBE_ARGUMENTS");return;}
            Application.onBeforeRender+=Render;
        }
        void Update()
        {
            if(finished)return;
            if(monitor==null)
            {
                if(Now-enabledAt>60000){Finish(2,"FRAME_PROBE_STARTUP_TIMEOUT");return;}
                if(foundation==null||!foundation.Ready||source==null||!source.Initialized||panel==null||panel.State==null||Now-enabledAt<5000)return;
                try
                {
                    var displays=new List<XRDisplaySubsystem>();SubsystemManager.GetSubsystems(displays);var d=displays.SingleOrDefault(x=>x.running);
                    double? hz=d!=null&&d.TryGetDisplayRefreshRate(out float rate)?(double?)rate:null;reportedHz=hz;
                    evidence=new FrameCsvEvidence(output,plan.Describe(new JObject{["version"]=1,["source"]="actual_Unity_onBeforeRender_snapshot_preview",["runtime_name"]=OpenXRRuntime.name,["clock_epoch"]=Guid.NewGuid().ToString("N"),["qualification"]=false,["windows"]="synthetic_engineering_only",["selected_station_hz"]=(int)foundation.Configuration["refresh_hz"],["runtime_reported_hz"]=hz,["physical_headset_present"]=JValue.CreateNull(),["observer_position_m"]=new JArray(foundation.observerCamera.transform.position.x,foundation.observerCamera.transform.position.y,foundation.observerCamera.transform.position.z),["observer_forward"]=new JArray(foundation.observerCamera.transform.forward.x,foundation.observerCamera.transform.forward.y,foundation.observerCamera.transform.forward.z),["observer_rotation_xyzw"]=new JArray(foundation.observerCamera.transform.rotation.x,foundation.observerCamera.transform.rotation.y,foundation.observerCamera.transform.rotation.z,foundation.observerCamera.transform.rotation.w),["camera_vertical_fov_deg"]=foundation.observerCamera.fieldOfView,["camera_aspect"]=foundation.observerCamera.aspect,["screen_width"]=Screen.width,["screen_height"]=Screen.height,["eye_texture_width"]=XRSettings.eyeTextureWidth,["eye_texture_height"]=XRSettings.eyeTextureHeight,["audio_present"]=false,["utc"]=DateTime.UtcNow.ToString("O")}));
                    monitor=new FrameMonitor((int)foundation.Configuration["refresh_hz"],evidence);
                    captured=true;Capture();start=Now;frame=Time.frameCount;monitor.Render(start,new RenderSample(frame),panel.InputAvailable);
                    monitor.Register(plan.Attempt(start),plan.Response(start),start);
                }
                catch{Finish(2,"FRAME_PROBE_STARTUP_FAILED");}
            }
            else if(!foundation.Ready||!source.Initialized){Finish(2,"FRAME_PROBE_READINESS_LOST");}
            else if(!injected&&Now-start>FrameProbePlan.InjectAfterMs)
            {
                injected=true;injectedAt=Now;
                if(plan.PanelLoss)
                {
                    // The interface must have been available, so the fault is attributable
                    // to this loss; disabling the controller runs its own OnDisable path.
                    if(!panel.InputAvailable||panel.FaultLatched){Finish(2,"FRAME_PROBE_PANEL_UNAVAILABLE_BEFORE_INJECTION");return;}
                    panel.enabled=false;
                    if(panel.InputAvailable){Finish(2,"FRAME_PROBE_PANEL_LOSS_NOT_OBSERVED");return;}
                }
                else if(plan.StallMs>0)Thread.Sleep(plan.StallMs);
            }
            else if(Now-start>FrameProbePlan.FinishAfterMs){Finish(0,"FRAME_PROBE_COMPLETE");}
        }
        void FixedUpdate(){physicsSteps++;}
        void Render()
        {
            if(monitor==null||finished||frame==Time.frameCount)return;
            try
            {
                frame=Time.frameCount;uint count=FrameTimingManager.GetLatestTimings(1,timings);FrameTimingManager.CaptureFrameTimings();
                monitor.Render(Now,new RenderSample(frame,count>0&&timings[0].cpuFrameTime>0?(double?)timings[0].cpuFrameTime:null,count>0&&timings[0].gpuFrameTime>0?(double?)timings[0].gpuFrameTime:null,reportedHz,Time.fixedDeltaTime*1000,physicsSteps),panel.InputAvailable&&!panel.FaultLatched);callbacks++;physicsSteps=0;
            }
            catch{Finish(2,"FRAME_PROBE_CAPTURE_FAILED");}
        }
        void Capture()
        {
            var camera=foundation.observerCamera;var previous=camera.targetTexture;var active=RenderTexture.active;
            var texture=new RenderTexture(1280,720,24);var pixels=new Texture2D(1280,720,TextureFormat.RGB24,false);
            try{camera.targetTexture=texture;camera.Render();RenderTexture.active=texture;pixels.ReadPixels(new Rect(0,0,1280,720),0,0);pixels.Apply();using var f=new FileStream(Path.Combine(output,"preview.png"),FileMode.CreateNew,FileAccess.Write,FileShare.Read);var bytes=pixels.EncodeToPNG();f.Write(bytes,0,bytes.Length);f.Flush(true);}
            finally{camera.targetTexture=previous;RenderTexture.active=active;Destroy(pixels);Destroy(texture);}
        }
        void Finish(int exit,string code)
        {
            if(finished)return;finished=true;Application.onBeforeRender-=Render;
            try
            {
                if(exit!=0)monitor?.CloseIncomplete(Now,code);evidence?.Dispose();
                if(evidence!=null){var result=plan.Result(code,exit,callbacks,captured,monitor,injectedAt);using var f=new FileStream(Path.Combine(output,"probe.json"),FileMode.CreateNew,FileAccess.Write,FileShare.Read);var bytes=System.Text.Encoding.UTF8.GetBytes(result.ToString());f.Write(bytes,0,bytes.Length);f.Flush(true);}
            }
            catch{exit=2;}
            Debug.Log(code);Application.Quit(exit);
        }
        void OnDisable(){if(!finished&&monitor!=null)Finish(2,"FRAME_PROBE_DISABLED");}
    }
}
