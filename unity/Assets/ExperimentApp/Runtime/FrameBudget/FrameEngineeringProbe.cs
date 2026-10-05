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

namespace AcousticVocab.FrameBudget
{
    // Installed only in the separately named engineering scene. There is no
    // session allocation or audio API, and no inferred headset qualification.
    public sealed class FrameEngineeringProbe:MonoBehaviour
    {
        public FoundationBootstrap foundation;public StateSourceHost source;public ResponsePanelController panel;
        FrameCsvEvidence evidence;FrameMonitor monitor;string output;int stall;long frame=-1;double enabledAt,start;bool injected,finished,captured;int callbacks,physicsSteps;readonly FrameTiming[] timings=new FrameTiming[1];double? reportedHz;
        static double Now=>AudioPlayer.Now*1000;
        static string Argument(string key){var a=Environment.GetCommandLineArgs();int i=Array.IndexOf(a,key);return i>=0&&i+1<a.Length?a[i+1]:null;}
        void Start()
        {
            enabledAt=Now;output=Argument("-frameProbeOutput");
            if(string.IsNullOrWhiteSpace(output)||!int.TryParse(Argument("-frameProbeStall"),out stall)||!new[]{0,200,300}.Contains(stall)){Finish(2,"FRAME_PROBE_ARGUMENTS");return;}
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
                    evidence=new FrameCsvEvidence(output,new JObject{["version"]=1,["source"]="actual_Unity_onBeforeRender_Meta_XR_Simulator_snapshot_preview",["qualification"]=false,["windows"]="synthetic_engineering_only",["selected_station_hz"]=(int)foundation.Configuration["refresh_hz"],["runtime_reported_hz"]=hz,["physical_headset_present"]=false,["audio_present"]=false,["requested_stall_ms"]=stall,["utc"]=DateTime.UtcNow.ToString("O")});
                    monitor=new FrameMonitor((int)foundation.Configuration["refresh_hz"],evidence);
                    captured=true;Capture();start=Now;frame=Time.frameCount;monitor.Render(start,new RenderSample(frame),panel.InputAvailable);
                    monitor.Register(new FrameAttempt("engineering-probe","engineering-probe",start+1000,start+11000,0),new FrameWindow("response","response",start+1000,start+10000),start);
                }
                catch{Finish(2,"FRAME_PROBE_STARTUP_FAILED");}
            }
            else if(!foundation.Ready||!source.Initialized){Finish(2,"FRAME_PROBE_READINESS_LOST");}
            else if(!injected&&Now-start>4000){injected=true;if(stall>0)Thread.Sleep(stall);}
            else if(Now-start>11500){Finish(0,"FRAME_PROBE_COMPLETE");}
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
                if(evidence!=null){var result=new JObject{["version"]=1,["code"]=code,["exit_code"]=exit,["actual_render_callbacks"]=callbacks,["captured"]=captured,["maximum_observed_interval_ms"]=monitor.MaximumMs,["faulted"]=!monitor.Healthy,["physical_qualification"]=false};using var f=new FileStream(Path.Combine(output,"probe.json"),FileMode.CreateNew,FileAccess.Write,FileShare.Read);var bytes=System.Text.Encoding.UTF8.GetBytes(result.ToString());f.Write(bytes,0,bytes.Length);f.Flush(true);}
            }
            catch{exit=2;}
            Debug.Log(code);Application.Quit(exit);
        }
        void OnDisable(){if(!finished&&monitor!=null)Finish(2,"FRAME_PROBE_DISABLED");}
    }
}
