using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using AcousticVocab.ResponsePanel;
using AcousticVocab.StateIntegration;
using AcousticVocab.StateSources;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace AcousticVocab.ViewCapture
{
    // Desktop engineering: the participant camera is placed at the plan pose
    // (its tracked-pose driver is disabled for this diagnostic process).
    // Headset/simulator: the tracked pose is only read back and refused if it
    // does not reach the plan pose.
    public sealed class TransformHeadRig:IHeadPoseRig
    {
        readonly Transform head;
        public bool CanSetPose{get;}
        public TransformHeadRig(Camera camera,bool desktopEngineering)
        {
            ViewCaptureFault.Require(camera!=null,"CAPTURE_CAMERA_MISSING");head=camera.transform;CanSetPose=desktopEngineering;
            if(desktopEngineering)foreach(var driver in camera.GetComponents<UnityEngine.InputSystem.XR.TrackedPoseDriver>())driver.enabled=false;
        }
        public void SetPose(HeadPose pose)
        {
            ViewCaptureFault.Require(CanSetPose,"HEAD_POSE_NOT_SETTABLE");
            head.SetPositionAndRotation(new Vector3((float)pose.Position[0],(float)pose.Position[1],(float)pose.Position[2]),
                new Quaternion((float)pose.Rotation[0],(float)pose.Rotation[1],(float)pose.Rotation[2],(float)pose.Rotation[3]));
        }
        public HeadPose ReadPose()
        {
            var p=head.position;var q=head.rotation;
            return new HeadPose(new double[]{p.x,p.y,p.z},new double[]{q.x,q.y,q.z,q.w});
        }
    }

    // Renders the actual observer camera (same scene, culling mask, clear and
    // projection settings) into an offscreen RGB target. This is a software
    // render of the participant camera, not the compositor/eye-buffer output.
    public sealed class CameraFrameGrabber:IFrameGrabber
    {
        readonly Camera camera;
        public CameraFrameGrabber(Camera camera){ViewCaptureFault.Require(camera!=null,"CAPTURE_CAMERA_MISSING");this.camera=camera;}
        public string Method=>"observer_camera_offscreen_render_mono_rgb24_not_compositor_output";
        public byte[] CapturePng(int width,int height)
        {
            var descriptor=new RenderTextureDescriptor(width,height,RenderTextureFormat.ARGB32,24){sRGB=QualitySettings.activeColorSpace==ColorSpace.Linear,msaaSamples=1};
            var target=RenderTexture.GetTemporary(descriptor);
            var previousTarget=camera.targetTexture;var previousActive=RenderTexture.active;var previousEye=camera.stereoTargetEye;
            Texture2D pixels=null;
            try
            {
                camera.targetTexture=target;camera.stereoTargetEye=StereoTargetEyeMask.None;camera.Render();
                RenderTexture.active=target;pixels=new Texture2D(width,height,TextureFormat.RGB24,false,false);
                pixels.ReadPixels(new Rect(0,0,width,height),0,0,false);pixels.Apply(false,false);
                return pixels.EncodeToPNG();
            }
            finally
            {
                camera.targetTexture=previousTarget;camera.stereoTargetEye=previousEye;RenderTexture.active=previousActive;
                RenderTexture.ReleaseTemporary(target);if(pixels!=null)UnityEngine.Object.Destroy(pixels);
            }
        }
    }

    // Actual visible renderers and text inside the participant camera's
    // frustum and culling mask. It reads scene objects, never trial content.
    public sealed class SceneViewInventory:IViewInventory
    {
        readonly Camera camera;readonly Func<(string ConfigurationSha256,bool RequestOpen)> panel;
        static readonly string[] DiagnosticMarkers={"simulation","watermark","diagnostic","debug","console","overlay"};
        public SceneViewInventory(Camera camera,ResponsePanelController panel)
            :this(camera,panel==null?null:(Func<(string,bool)>)(()=>
            {
                ViewCaptureFault.Require(panel.LoadedConfigurationSha256!=null&&panel.State!=null,"PANEL_UNAVAILABLE");
                return (panel.LoadedConfigurationSha256,panel.State.Request!=null);
            })){}
        // The panel state is held constant; tests may supply the descriptor.
        internal SceneViewInventory(Camera camera,Func<(string,bool)> panelState)
        {ViewCaptureFault.Require(camera!=null&&panelState!=null,"CAPTURE_VIEW_BINDING");this.camera=camera;panel=panelState;}

        // Independently computable fixed-panel pin: SHA-256 of the canonical
        // descriptor (sorted keys, compact separators, ASCII, final newline).
        public static JObject PanelStateDescriptor(string configurationSha256,bool requestOpen)=>
            new JObject{["version"]=1,["kind"]="view_capture_panel_state",["configuration_sha256"]=configurationSha256,["request_open"]=requestOpen};
        public static string PanelStateSha256(string configurationSha256,bool requestOpen)=>CaptureJson.Hash(CaptureJson.Canonical(PanelStateDescriptor(configurationSha256,requestOpen)));

        internal static string Ascii(string value)
        {
            var text=new StringBuilder();
            foreach(char c in value??"")text.Append(c>=32&&c<127&&c!='\\'?c.ToString():"\\u"+((int)c).ToString("x4"));
            return text.ToString();
        }
        static string PathOf(Transform t){var parts=new List<string>();for(;t!=null;t=t.parent)parts.Add(t.name);parts.Reverse();return Ascii(string.Join("/",parts));}

        public ViewReading Read()
        {
            var panelState=panel();
            var planes=GeometryUtility.CalculateFrustumPlanes(camera);
            var visible=UnityEngine.Object.FindObjectsByType<Renderer>(FindObjectsInactive.Exclude,FindObjectsSortMode.None)
                .Where(r=>r.enabled&&r.gameObject.activeInHierarchy&&(camera.cullingMask&(1<<r.gameObject.layer))!=0&&GeometryUtility.TestPlanesAABB(planes,r.bounds)).ToArray();
            var renderers=visible.Select(r=>new JObject{["path"]=PathOf(r.transform),["type"]=r.GetType().Name,["layer"]=r.gameObject.layer,
                    ["materials"]=new JArray(r.sharedMaterials.Select(m=>m==null?"<null>":Ascii(m.name)+"|"+Ascii(m.shader!=null?m.shader.name:"<none>")))})
                .OrderBy(x=>(string)x["path"],StringComparer.Ordinal).ThenBy(x=>(string)x["type"],StringComparer.Ordinal).ToList();
            var text=visible.Select(r=>r.GetComponent<TextMesh>()).Where(t=>t!=null)
                .Select(t=>(Path:PathOf(t.transform),Text:Ascii(t.text))).OrderBy(x=>x.Path,StringComparer.Ordinal).ToList();
            var overlays=UnityEngine.Object.FindObjectsByType<Canvas>(FindObjectsInactive.Exclude,FindObjectsSortMode.None)
                .Where(c=>c.isActiveAndEnabled&&c.renderMode==RenderMode.ScreenSpaceOverlay).Select(c=>PathOf(c.transform)).OrderBy(x=>x,StringComparer.Ordinal).ToList();
            var ongui=UnityEngine.Object.FindObjectsByType<MonoBehaviour>(FindObjectsInactive.Exclude,FindObjectsSortMode.None)
                .Where(b=>b.isActiveAndEnabled&&b.GetType().GetMethod("OnGUI",System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.Public|System.Reflection.BindingFlags.NonPublic)!=null)
                .Select(b=>PathOf(b.transform)+"#"+b.GetType().Name).OrderBy(x=>x,StringComparer.Ordinal).ToList();
            var marked=renderers.Select(x=>(string)x["path"]).Where(p=>DiagnosticMarkers.Any(m=>p.IndexOf(m,StringComparison.OrdinalIgnoreCase)>=0)).ToList();
            var inventory=new JObject{["version"]=1,["kind"]="view_capture_renderer_inventory",["renderers"]=new JArray(renderers),
                ["text"]=new JArray(text.Select(x=>new JObject{["path"]=x.Path,["text"]=x.Text})),["overlay_canvases"]=new JArray(overlays),
                ["ongui_components"]=new JArray(ongui),["diagnostic_named_renderers"]=new JArray(marked),["developer_console_visible"]=Debug.developerConsoleVisible};
            string inventoryHash=CaptureJson.Hash(CaptureJson.Canonical(new JObject{["renderers"]=inventory["renderers"],["text"]=inventory["text"]}));
            var view=new JObject{["panel_state_sha256"]=PanelStateSha256(panelState.ConfigurationSha256,panelState.RequestOpen),
                ["visible_text"]=new JArray(text.Select(x=>x.Text)),["renderer_inventory_sha256"]=inventoryHash,
                ["console_visible"]=overlays.Count>0||Debug.developerConsoleVisible,["diagnostics_visible"]=ongui.Count>0||marked.Count>0};
            return new ViewReading(view,inventory);
        }
    }

    // Saved settings of the single scene AudioSource. Identical metadata is
    // not an acoustic routing measurement.
    public sealed class AudioSourceRouting:IAudioRouting
    {
        public JObject Read()
        {
            var sources=UnityEngine.Object.FindObjectsByType<AudioSource>(FindObjectsInactive.Include,FindObjectsSortMode.None);
            ViewCaptureFault.Require(sources.Length==1,"AUDIO_SOURCE_COUNT");var s=sources[0];
            var effects=s.GetComponents<Behaviour>().Where(b=>b is AudioLowPassFilter||b is AudioHighPassFilter||b is AudioEchoFilter||b is AudioDistortionFilter||b is AudioReverbFilter||b is AudioChorusFilter)
                .Select(b=>b.GetType().Name).OrderBy(x=>x,StringComparer.Ordinal).ToArray();
            var configuration=AudioSettings.GetConfiguration();
            return new JObject{["spatial_blend"]=(double)s.spatialBlend,["pan_stereo"]=(double)s.panStereo,["doppler_level"]=(double)s.dopplerLevel,["spread"]=(double)s.spread,
                ["volume"]=(double)s.volume,["pitch"]=(double)s.pitch,["mute"]=s.mute,["enabled"]=s.enabled&&s.gameObject.activeInHierarchy,["loop"]=s.loop,
                ["output_mixer_group"]=s.outputAudioMixerGroup==null?"":SceneViewInventory.Ascii(s.outputAudioMixerGroup.name),
                ["route_id"]="unity_default_output:"+AudioSettings.outputSampleRate+":"+configuration.speakerMode,["source_count"]=sources.Length,["effects"]=new JArray(effects)};
        }
    }

    static class AppliedFrames
    {
        internal static JObject Project(SceneFrame frame)=>new JObject{["version"]=1,["kind"]="view_capture_applied_frame",["provenance"]=frame.Provenance,
            ["session_id"]=frame.SessionId,["sequence"]=frame.Sequence,["sim_step"]=frame.SimStep,["published_ns"]=frame.PublishedNs.ToString(),["sim_time"]=frame.SimTime,
            ["coordinate_frame"]="isaac_world_public_projection",["joint_positions"]=new JArray(frame.Joints.Cast<object>().ToArray()),
            ["objects"]=new JArray(frame.Objects.Select(o=>new JObject{["id"]=o.Id,["position_m"]=new JArray((double)o.Position.x,(double)o.Position.y,(double)o.Position.z),
                ["rotation_xyzw"]=new JArray((double)o.Rotation.x,(double)o.Rotation.y,(double)o.Rotation.z,(double)o.Rotation.w),["visible"]=o.Visible,["enabled"]=o.Enabled,["state"]=o.VisualState}))};
    }

    // Snapshot source: the complete pinned neutral state is the source state.
    // It is written per capture only after the host's own reset/neutral
    // checks pass; it is not an Isaac readback.
    public sealed class SnapshotStateReadback:IStateReadback
    {
        readonly StateSourceHost source;readonly JObject state;readonly IViewCaptureClock clock;
        public string Origin=>"snapshot_source_pinned_neutral_state_not_isaac_readback";
        public SnapshotStateReadback(StateSourceHost source,byte[] verifiedNeutralBytes,string expectedSha256,IViewCaptureClock clock)
        {
            ViewCaptureFault.Require(source!=null&&source.Kind=="snapshot"&&clock!=null&&verifiedNeutralBytes!=null&&CaptureJson.Hash(verifiedNeutralBytes)==expectedSha256&&source.LoadedNeutralSha256==expectedSha256,"SNAPSHOT_STATE_BINDING");
            state=CaptureJson.ParseStrictObject(verifiedNeutralBytes,65536,"SNAPSHOT_STATE_BINDING")["state"] as JObject;
            ViewCaptureFault.Require(state!=null,"SNAPSHOT_STATE_BINDING");this.source=source;this.clock=clock;
        }
        public bool TryRead(long notBeforeNs,out StateReading reading)
        {
            reading=null;if(!source.ConfirmReset()||!source.CheckExposureReady())return false;
            long now=clock.NowNs;if(now<notBeforeNs)return false;
            reading=new StateReading((JObject)state.DeepClone(),now,null);return true;
        }
    }

    // Live source: Unity only has the public projection it applied. The
    // complete #53 state is joined offline from the Isaac owner-thread
    // observation journal by frame identity (tools/view_capture_assemble.py).
    public sealed class LiveAppliedStateReadback:IStateReadback,IDisposable
    {
        readonly StateSourceHost source;SceneFrame latest;long latestReceivedNs;
        public string Origin=>"live_applied_projection_complete_state_joined_offline";
        public LiveAppliedStateReadback(StateSourceHost source)
        {ViewCaptureFault.Require(source!=null&&source.Kind=="live","LIVE_STATE_BINDING");this.source=source;source.FrameApplied+=Applied;}
        void Applied(SceneFrame frame,double lastReceivedSeconds){latest=frame;latestReceivedNs=StopwatchCaptureClock.FromSeconds(lastReceivedSeconds);}
        public bool TryRead(long notBeforeNs,out StateReading reading)
        {
            reading=null;if(!source.ConfirmReset()||!source.CheckExposureReady())return false;
            if(latest==null||latest.Provenance!="live"||latestReceivedNs<notBeforeNs)return false;
            reading=new StateReading(null,latestReceivedNs,AppliedFrames.Project(latest));return true;
        }
        public void Dispose(){source.FrameApplied-=Applied;}
    }

    public sealed class PrivateControlPort:IViewCaptureControl
    {
        readonly PrivateModeResetClient client;
        public PrivateControlPort(PrivateModeResetClient client,string controlSessionId)
        {ViewCaptureFault.Require(client!=null&&client.RequiredMode=="test","VIEW_CAPTURE_PROTECTED_TEST_MODE_REQUIRED");this.client=client;ControlSessionId=controlSessionId;}
        public string RequiredMode=>client.RequiredMode;
        public bool ModeAcknowledged=>client.ModeAcknowledged;
        public string ControlSessionId{get;}
        public string FaultCode
        {
            get
            {
                var diagnostic=client.ReadinessDiagnostic(null);
                if(diagnostic["failed"]?.Type==JTokenType.Boolean&&!(bool)diagnostic["failed"])return null;
                string code=diagnostic["first_failure_code"]?.Type==JTokenType.String?(string)diagnostic["first_failure_code"]:"CONTROL_FAILED";
                return new ViewCaptureFault(code).Code;
            }
        }
        public string RequestReset(){try{return client.RequestReset();}catch(ControlFault error){throw new ViewCaptureFault(error.Code);}}
        public bool TryResetAcknowledged(string requestId,out byte[] rawReply,out long receivedNs)
        {
            rawReply=null;receivedNs=0;
            try{if(!client.ResetAcknowledged(requestId))return false;}catch(ControlFault error){throw new ViewCaptureFault(error.Code);}
            if(!client.TryGetAcceptedResetReply(requestId,out string raw,out double ms)||raw==null)return false;
            rawReply=new UTF8Encoding(false,true).GetBytes(raw);receivedNs=StopwatchCaptureClock.FromMilliseconds(ms);return true;
        }
    }

    // Records the boundary kind honestly: this boundary keeps the pair in the
    // driver only. It does not load a session-engine trial or prepare audio.
    public sealed class DriverOnlyTrialBoundary:IProtectedTrialBoundary
    {
        readonly IViewCaptureClock clock;bool loaded;
        public DriverOnlyTrialBoundary(IViewCaptureClock clock){this.clock=clock??throw new ArgumentNullException(nameof(clock));}
        public string Kind=>"driver_only_no_session_engine_no_cue_audio";
        public void Load(LegalPair pair){ViewCaptureFault.Require(!loaded,"TRIAL_BOUNDARY_STATE");loaded=true;}
        public long RequestCue(){ViewCaptureFault.Require(loaded,"TRIAL_BOUNDARY_STATE");return clock.NowNs;}
        public void Unload(){loaded=false;}
    }
}
