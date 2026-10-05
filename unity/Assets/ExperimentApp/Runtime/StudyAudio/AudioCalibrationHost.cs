using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.Foundation;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem;
using UnityEngine.UI;
using UnityEngine.XR;

namespace AcousticVocab.StudyAudio
{
    public sealed class AudioCalibrationConfiguration
    {
        public string CodedId { get; private set; }
        public string VisitId { get; private set; }
        public string ConfigSha256 { get; private set; }
        public bool DesktopPreview { get; private set; }
        public long BudgetBytes { get; private set; }
        public IReadOnlyList<string> ProfileOrder { get; private set; }
        readonly Dictionary<string,JObject> examples=new Dictionary<string,JObject>();
        // Public #14 reserved registry, asset spec0.1.0. These pins ensure that
        // changing a private filename/hash cannot smuggle study audio here.
        public static readonly IReadOnlyList<string> FileHashes=Array.AsReadOnly(new[]{
            "f5a1a6d526bb5685e8a26217c5313e0afd18c3824c9adee8643fe99e2af8bd90",
            "94c4aa228c33f3f57b78bb5c10ccc86a00e85efe5d59fa0c7e8ac09d2d2bb19a",
            "0645d88eabf758f4e53645119538f703dc1ae641771655e900e1b9089f1787fd"});
        public static readonly IReadOnlyList<string> PcmHashes=Array.AsReadOnly(new[]{
            "e50e47627a73137392e2cfdd0dac2253adda3c8b10aea49174c9506c3fb90211",
            "5c1cf02801d36137df047975043a89613aff99882eca77f3604579a2e223dca8",
            "14299502afa920cc0413def79b289f0258dcea794bf4922a0666ad34186ea2b1"});
        public static AudioCalibrationConfiguration Load(byte[] bytes)
        {
            try
            {
                PackageRules.Require(bytes!=null && bytes.Length<=65536); var value=PackageRules.Json(bytes);
                PackageRules.Keys(value,"schema_version","coded_id","visit_id","profile_order","examples","preload_budget_bytes","engineering_desktop_preview");
                PackageRules.Require(PackageRules.Integer(value["schema_version"])==1);
                string id=PackageRules.String(value["coded_id"]),visit=PackageRules.String(value["visit_id"]);
                PackageRules.Require(PackageRules.Match(id,"[A-Za-z0-9][A-Za-z0-9._-]{0,79}") && PackageRules.Match(visit,"[0-9a-f]{32}"));
                var order=PackageRules.Array(value["profile_order"],3).Select(PackageRules.String).ToArray();
                PackageRules.Require(order.Distinct().Count()==3 && order.All(PackageRules.Profiles.Contains));
                long budget=PackageRules.Integer(value["preload_budget_bytes"]); PackageRules.Require(budget>=2880000 && budget<=64*1024*1024);
                var result=new AudioCalibrationConfiguration { CodedId=id,VisitId=visit,ConfigSha256=PcmWave.Hash(bytes),
                    DesktopPreview=PackageRules.Boolean(value["engineering_desktop_preview"]),BudgetBytes=budget,ProfileOrder=Array.AsReadOnly(order) };
                PackageRules.Keys(value["examples"],"P1","P2","P3");
                for(int i=0;i<3;i++)
                {
                    string p="P"+(i+1); var row=value["examples"][p]; PackageRules.Keys(row,"file","file_sha256","pcm_sha256");
                    PackageRules.Require((string)row["file"]=="calibration-"+p+".wav" && (string)row["file_sha256"]==FileHashes[i] && (string)row["pcm_sha256"]==PcmHashes[i]);
                    result.examples.Add(p,(JObject)row.DeepClone());
                }
                return result;
            }
            catch(AudioFault) { throw; }
            catch(Exception) { throw new AudioFault("CALIBRATION_CONFIG_INVALID"); }
        }
        public IReadOnlyDictionary<string,PcmWave> ReadExamples(string privateDirectory)
        {
            try
            {
                var waves=new Dictionary<string,PcmWave>();
                foreach(var entry in examples)
                {
                    byte[] bytes=PackageRules.Read(privateDirectory,(string)entry.Value["file"],192044);
                    var wave=PcmWave.ParseCanonical(bytes);
                    PackageRules.Require(wave.SampleCount==96000 && wave.FileSha256==(string)entry.Value["file_sha256"] && wave.PcmSha256==(string)entry.Value["pcm_sha256"]);
                    waves.Add(entry.Key,wave);
                }
                return new System.Collections.ObjectModel.ReadOnlyDictionary<string,PcmWave>(waves);
            }
            catch(AudioFault) { throw; }
            catch(Exception) { throw new AudioFault("HASH_MISMATCH"); }
        }
    }

    // Calibration only. It neither loads participant packages nor enables a
    // trial gate. Study B profile order is provisioned, never shuffled here.
    [DisallowMultipleComponent]
    public sealed class AudioCalibrationHost : MonoBehaviour
    {
        public FoundationBootstrap foundation;
        public AudioPlayer player;
        public Canvas canvas;
        public Text statusText,gainText;
        public Button listenButton,quieterButton,louderButton,yesButton,noButton,stopButton;
        public LineRenderer controllerRay;
        AudioCalibrationConfiguration config;
        ProfileComfortSequence sequence;
        ComfortableGainStore gains;
        IReadOnlyDictionary<string,PcmWave> examples;
        FileStream log;
        bool failed,focused=true,paused,pairActive,firstComplete,secondScheduled,secondComplete;
        bool anyUncomfortable;
        int answered;
        string profile;
        InputAction controllerPosition,controllerRotation,controllerTracked,controllerTrigger;
        Button hovered;
        public bool Finished => sequence!=null && sequence.Finished && !failed;

        void Start()
        {
            try
            {
                if(foundation==null || foundation.Configuration==null || player==null || canvas==null) throw new AudioFault("CALIBRATION_FOUNDATION_UNAVAILABLE");
                string root=Application.persistentDataPath;
                config=AudioCalibrationConfiguration.Load(PackageRules.Read(root,"audio-calibration.local.json",65536));
                if(config.DesktopPreview && Application.platform==RuntimePlatform.Android) throw new AudioFault("CALIBRATION_PREVIEW_PLATFORM");
                Pose observer=StationConfig.ReferencePose(foundation.Configuration);
                canvas.transform.SetPositionAndRotation(observer.position+observer.rotation*new Vector3(0,0,1.3f),observer.rotation);
                if(config.DesktopPreview && !XRSettings.isDeviceActive)
                    foundation.observerCamera.transform.SetPositionAndRotation(observer.position,observer.rotation);
                Directory.CreateDirectory(Path.Combine(root,"operator-logs"));
                log=new FileStream(Path.Combine(root,"operator-logs","audio-calibration-"+Guid.NewGuid().ToString("N")+".jsonl"),FileMode.CreateNew,FileAccess.Write,FileShare.Read,4096,FileOptions.WriteThrough);
                Write("calibration_started",new JObject { ["config_sha256"]=config.ConfigSha256,["profile_order"]=new JArray(config.ProfileOrder),
                    ["engineering_desktop_preview"]=config.DesktopPreview,["route"]=foundation.Configuration["audio"]["route"],
                    ["audio_onset_estimate_mono_ms"]=null,["onset_uncertainty_ms"]=null,["route_offset_ms"]=null,["study_playback_enabled"]=false });
                examples=config.ReadExamples(root); gains=new ComfortableGainStore(Path.Combine(root,"comfortable-gain"));
                sequence=new ProfileComfortSequence(config.ProfileOrder.ToArray()); sequence.Answer+=OnAnswer;
                player.Event+=OnAudioEvent;
                player.Configure(AudioRouteCalibration.Unmeasured((string)foundation.Configuration["audio"]["route"]),Gate);
                float restored=gains.Restore(config.CodedId); player.SetComfortableGain(restored); player.Preload(examples,config.BudgetBytes);
                Write("calibration_preloaded",new JObject { ["gain"]=restored,["estimated_preload_bytes"]=player.EstimatedPreloadBytes,
                    ["unity_allocated_bytes"]=player.UnityAllocatedBytes,["file_sha256"]=new JArray(AudioCalibrationConfiguration.FileHashes),["pcm_sha256"]=new JArray(AudioCalibrationConfiguration.PcmHashes) });
                listenButton.onClick.AddListener(BeginProfile); quieterButton.onClick.AddListener(()=>ChangeGain(-.05f)); louderButton.onClick.AddListener(()=>ChangeGain(.05f));
                yesButton.onClick.AddListener(()=>Respond(true)); noButton.onClick.AddListener(()=>Respond(false)); stopButton.onClick.AddListener(()=>Fail("CALIBRATION_OPERATOR_STOP"));
                controllerPosition=new InputAction("Calibration controller position",InputActionType.Value,"<XRController>{RightHand}/devicePosition");
                controllerRotation=new InputAction("Calibration controller rotation",InputActionType.Value,"<XRController>{RightHand}/deviceRotation");
                controllerTracked=new InputAction("Calibration controller tracked",InputActionType.Button,"<XRController>{RightHand}/isTracked");
                controllerTrigger=new InputAction("Calibration controller select",InputActionType.Button,"<XRController>{RightHand}/triggerPressed");
                controllerPosition.Enable(); controllerRotation.Enable(); controllerTracked.Enable(); controllerTrigger.Enable();
                RefreshUi();
            }
            catch(AudioFault error) { Fail(error.Code); }
            catch(Exception) { Fail("CALIBRATION_START_FAILED"); }
        }
        bool Gate() => !failed && focused && !paused && config!=null && foundation!=null && foundation.Configuration!=null &&
            (foundation.Ready || config.DesktopPreview && Application.platform!=RuntimePlatform.Android && !XRSettings.isDeviceActive);
        void Write(string kind,JObject fields)
        {
            if(log==null) throw new AudioFault("CALIBRATION_LOG_UNAVAILABLE");
            fields["event"]=kind; fields["schema_version"]=1; fields["host_mono_ms"]=AudioPlayer.Now*1000;
            fields["coded_id"]=config.CodedId; fields["visit_id"]=config.VisitId;
            byte[] bytes=new UTF8Encoding(false).GetBytes(fields.ToString(Formatting.None)+"\n"); log.Write(bytes,0,bytes.Length); log.Flush(true);
        }
        void BeginProfile()
        {
            if(!Gate() || pairActive || sequence.Finished || !player.Ready) return;
            try
            {
                profile=sequence.Profile; double first=AudioPlayer.Now+.75;
                sequence.StartProfile(first,examples[profile]); pairActive=true; firstComplete=secondScheduled=secondComplete=false;
                Write("comfort_pair_started",new JObject { ["profile"]=profile,["gain"]=player.CurrentGain,["first_requested_mono_ms"]=first*1000,["second_requested_mono_ms"]=(first+4)*1000,
                    ["digital_example_seconds"]=2,["requested_gap_seconds"]=2,["audible_gap_qualified"]=false });
                player.ScheduleCalibration(profile,first); RefreshUi();
            }
            catch(AudioFault error) { Fail(error.Code); }
            catch(Exception) { Fail("CALIBRATION_SCHEDULE_FAILED"); }
        }
        void ChangeGain(float delta)
        {
            if(!Gate() || pairActive || player.Playing || sequence.Finished) return;
            try
            {
                float value=Mathf.Clamp(player.CurrentGain+delta,.01f,1f);
                var change=gains.ChangeForComfort(config.CodedId,config.VisitId,value,AudioPlayer.Now,!player.Playing);
                player.SetComfortableGain(value); Write("calibration_gain_applied",new JObject { ["old_gain"]=change.Previous,["new_gain"]=change.Current }); RefreshUi();
            }
            catch(AudioFault error) { Fail(error.Code); }
            catch(Exception) { Fail("CALIBRATION_GAIN_FAILED"); }
        }
        void Respond(bool comfortable)
        {
            if(!Gate() || !pairActive || !sequence.CanAnswer || player.Playing) return;
            try { sequence.Respond(comfortable,AudioPlayer.Now); pairActive=false; RefreshUi(); }
            catch(AudioFault error) { Fail(error.Code); }
            catch(Exception) { Fail("CALIBRATION_RESPONSE_FAILED"); }
        }
        void OnAnswer(string currentProfile,bool comfortable,double now)
        {
            Write("profile_comfort_answer",new JObject { ["profile"]=currentProfile,["comfortable"]=comfortable,["gain"]=player.CurrentGain,["response_mono_ms"]=now*1000,
                ["both_software_plays_completed"]=firstComplete && secondComplete,["physical_delivery_qualified"]=false });
            anyUncomfortable|=!comfortable; answered++;
            if(answered==3) Write("calibration_responses_complete",new JObject { ["all_comfortable"]=!anyUncomfortable,["study_playback_enabled"]=false });
        }
        void OnAudioEvent(AudioPlaybackEvent value)
        {
            if(failed) return;
            try
            {
                var timing=value.Timing;
                Write("audio_playback",new JObject { ["playback_status"]=value.Code,["audio_id"]=value.AudioId,["pcm_sha256"]=value.PcmSha256,
                    ["action_pcm_sha256"]=value.ActionPcmSha256,["referent_pcm_sha256"]=value.ReferentPcmSha256,
                    ["first_output_callback_dsp_s"]=value.FirstOutputCallbackDspSeconds.HasValue?new JValue(value.FirstOutputCallbackDspSeconds.Value):JValue.CreateNull(),
                    ["audio_request_mono_ms"]=timing.RequestMonoSeconds*1000,["scheduled_mono_ms"]=timing.ScheduledMonoSeconds*1000,
                    ["scheduled_dsp_s"]=timing.ScheduledDspSeconds,["audio_onset_estimate_mono_ms"]=timing.OnsetEstimateMonoSeconds.HasValue?new JValue(timing.OnsetEstimateMonoSeconds.Value*1000):JValue.CreateNull(),
                    ["onset_uncertainty_ms"]=timing.OnsetUncertaintyMs.HasValue?new JValue(timing.OnsetUncertaintyMs.Value):JValue.CreateNull(),
                    ["route_offset_ms"]=timing.RouteOffsetMs.HasValue?new JValue(timing.RouteOffsetMs.Value):JValue.CreateNull(),
                    ["observed_mono_ms"]=value.ObservedMonoSeconds*1000,["delivered_samples"]=value.DeliveredSamples,["callback_count"]=value.CallbackCount,["calibration_only"]=timing.CalibrationOnly });
                if(value.Code=="AUDIO_PLAYBACK_COMPLETED")
                {
                    int index=secondScheduled?1:0;
                    if(value.AudioId!=profile || !pairActive) throw new AudioFault("CALIBRATION_DELIVERY_INVALID");
                    sequence.ConfirmPlayback(index,value.ObservedMonoSeconds);
                    if(index==0) firstComplete=true; else secondComplete=true;
                }
                else if(value.Code!="AUDIO_REQUESTED" && value.Code!="CALIBRATION_DELIVERY_OBSERVED") Fail(value.Code);
            }
            catch { Fail("CALIBRATION_LOG_OR_SEQUENCE_FAILED"); throw; }
        }
        void Update()
        {
            if(config==null || failed) return;
            try
            {
                if(pairActive && !Gate()) { Fail("CALIBRATION_FOCUS_OR_TRACKING_LOST"); return; }
                if(pairActive && firstComplete && !secondScheduled && !player.Playing)
                { secondScheduled=true; player.ScheduleCalibration(profile,sequence.SecondOnsetMonoSeconds); }
                UpdateRay(); RefreshUi();
            }
            catch(AudioFault error) { Fail(error.Code); }
            catch(Exception) { Fail("CALIBRATION_UPDATE_FAILED"); }
        }
        void UpdateRay()
        {
            bool tracked=Gate() && controllerTracked!=null && controllerTracked.IsPressed();
            if(controllerRay!=null) controllerRay.enabled=tracked;
            Button next=null;
            if(tracked)
            {
                Vector3 start=foundation.seatedOrigin.TransformPoint(controllerPosition.ReadValue<Vector3>());
                Vector3 direction=foundation.seatedOrigin.rotation*controllerRotation.ReadValue<Quaternion>()*Vector3.forward;
                var ray=new Ray(start,direction); var plane=new Plane(canvas.transform.forward,canvas.transform.position);
                float distance=4;
                if(plane.Raycast(ray,out float hit) && hit>0 && hit<4)
                {
                    distance=hit; Vector2 screen=RectTransformUtility.WorldToScreenPoint(canvas.worldCamera,ray.GetPoint(hit));
                    foreach(var button in Buttons()) if(button.interactable && RectTransformUtility.RectangleContainsScreenPoint((RectTransform)button.transform,screen,canvas.worldCamera)) { next=button; break; }
                }
                if(controllerRay!=null) { controllerRay.SetPosition(0,start); controllerRay.SetPosition(1,ray.GetPoint(distance)); }
            }
            if(next!=hovered)
            {
                if(EventSystem.current!=null)
                {
                    var data=new PointerEventData(EventSystem.current);
                    if(hovered!=null) ExecuteEvents.Execute(hovered.gameObject,data,ExecuteEvents.pointerExitHandler);
                    if(next!=null) ExecuteEvents.Execute(next.gameObject,data,ExecuteEvents.pointerEnterHandler);
                }
                hovered=next;
            }
            if(hovered!=null && controllerTrigger.WasPressedThisFrame()) hovered.onClick.Invoke();
        }
        IEnumerable<Button> Buttons() => new[]{listenButton,quieterButton,louderButton,yesButton,noButton,stopButton}.Where(b=>b!=null);
        void RefreshUi()
        {
            bool gate=Gate(),answer=gate && pairActive && sequence.CanAnswer && !player.Playing;
            listenButton.interactable=gate && !pairActive && !sequence.Finished && player.Ready;
            quieterButton.interactable=louderButton.interactable=gate && !pairActive && !sequence.Finished && !player.Playing;
            yesButton.interactable=noButton.interactable=answer; stopButton.interactable=!failed && !sequence.Finished;
            gainText.text="Level: "+Mathf.RoundToInt(player.CurrentGain*100)+"%";
            statusText.text=sequence.Finished?(anyUncomfortable?"Responses saved. Tell the operator about discomfort.":"Responses saved. Continue with the operator."):
                !gate?"Waiting for tracking and focus.":answer?"Was that comfortable?":pairActive?"Listen to both examples.":"Example "+(answered+1)+" of 3. Choose a comfortable level, then listen.";
        }
        void Fail(string code)
        {
            if(failed) return; failed=true;
            try { if(log!=null) Write("calibration_fault",new JObject { ["code"]=new AudioFault(code).Code }); } catch { }
            if(player!=null) player.Abort(code);
            foreach(var button in Buttons()) button.interactable=false;
            if(controllerRay!=null) controllerRay.enabled=false;
            if(statusText!=null) statusText.text="Calibration stopped. Ask the operator.";
            Debug.LogError("AUDIO_CALIBRATION_FAULT "+new AudioFault(code).Code);
        }
        void OnApplicationFocus(bool value) { focused=value; if(!value && config!=null) Fail("CALIBRATION_FOCUS_LOST"); }
        void OnApplicationPause(bool value) { paused=value; if(value && config!=null) Fail("CALIBRATION_PAUSED"); }
        void OnDestroy()
        {
            if(player!=null) { player.Abort("CALIBRATION_CLOSED"); player.Event-=OnAudioEvent; }
            controllerPosition?.Dispose(); controllerRotation?.Dispose(); controllerTracked?.Dispose(); controllerTrigger?.Dispose();
            if(log!=null) { try { log.Flush(true); } catch { Debug.LogError("AUDIO_CALIBRATION_FAULT CALIBRATION_LOG_CLOSE_FAILED"); } finally { log.Dispose(); } }
        }
    }
}
