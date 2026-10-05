using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using AcousticVocab.Foundation;
using AcousticVocab.ResponsePanel;
using AcousticVocab.StateIntegration;
using AcousticVocab.StateSources;
using AcousticVocab.Workcell;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.XR;
using UnityEngine.XR.Hands;

namespace AcousticVocab.Orientation
{
    [DisallowMultipleComponent]
    public sealed class OrientationHost : MonoBehaviour
    {
        public FoundationBootstrap foundation;
        public StateSourceHost stateSource;
        public WorkcellRegistry workcell;
        public ResponsePanelController panel;
        public Shader shader;
        public Font font;
        public OrientationFlow Flow { get; private set; }
        public bool EligibleOutcomeRecorded => isActiveAndEnabled && !failed && Flow?.EligibleOutcomeRecorded==true;
        public event Action<OrientationOutcome> OutcomeRecorded;
        OrientationSetup setup;OrientationDemos demos;OrientationJournal journal;PanelSettings input;
        OrientationDemo activeDemo;Transform display;TextMesh title,body,buttonText;BoxCollider button;MeshRenderer buttonSurface;LineRenderer pointer;
        Material surfaceMaterial,textBackingMaterial;readonly List<XRHandSubsystem> hands=new List<XRHandSubsystem>();
        bool initialized,failed,focused=true,paused,held,armed,haveTip,pokeArmed,buttonEnabled;
        Vector3 previousTip;double started;string faultCode;

        void Awake()
        {
            // Own the panel before any Start/Update can run its optional engineering one-shot.
            if(panel!=null) panel.enabled=false;
            started=PanelJournal.NowMs;
        }
        void Start()
        {
            try
            {
                if(foundation==null || panel==null || stateSource==null || workcell==null || foundation.Configuration==null) throw new OrientationFault("ORIENTATION_FOUNDATION_MISSING");
                var station=foundation.Configuration;var identity=Resources.Load<TextAsset>("BuildIdentity");
                setup=OrientationSetup.Load(Application.persistentDataPath,(string)station["protocol_version"]);
                journal=new OrientationJournal(Path.Combine(Application.persistentDataPath,"operator-logs"),StationConfig.ParseStrict(identity.text),(string)station["station_id"],setup.PlanHash,setup.DemoIndexHash);
                input=PanelSettings.Parse(File.ReadAllText(Path.Combine(Application.persistentDataPath,"response-panel.local.json")),Resources.Load<TextAsset>("ResponsePanelSchema").text,(string)station["protocol_version"]);
                input.VerifyStationInput((string)station["input_method"]);
                if(input.EngineeringMode!="disabled") throw new OrientationFault("ORIENTATION_PANEL_MUST_BE_EXTERNALLY_OWNED");
                Flow=new OrientationFlow(setup.Plan,()=>PanelJournal.NowMs,journal.Record);
                Flow.OutcomeRecorded+=value=> { OutcomeRecorded?.Invoke(value); };
                CreateDisplay(StationConfig.ReferencePose(station));
                foundation.Faulted+=FoundationFault;stateSource.Event+=SourceEvent;panel.Responded+=Response;panel.Faulted+=PanelFault;
            }
            catch(OrientationFault e) { Fail(e.Message); }
            catch { Fail("ORIENTATION_CONFIG_UNAVAILABLE"); }
        }
        void InitializeDemos()
        {
            if(!stateSource.Initialized) return;
            if(stateSource.Kind!="snapshot") throw new OrientationFault("ORIENTATION_LIVE_DEMO_CONTROL_UNAVAILABLE");
            var station=foundation.Configuration;
            var config=StateSourceConfiguration.Load(File.ReadAllText(Path.Combine(Application.persistentDataPath,"state-source.local.json")));
            var registry=config.Registry((string)station["station_id"],workcell.ImportedLayout.text,workcell.CanonicalJointNames);
            demos=OrientationDemos.Load(setup.DemoDirectory,setup.DemoIndexHash,registry,config.LoadNeutralBytes(Application.persistentDataPath),setup.Plan.EngineeringDraft);
            journal.Record(new JObject { ["event"]="orientation_assets_validated",["mono_ms"]=PanelJournal.NowMs,["source_kind"]=stateSource.Kind,["demo_count"]=8,["nominal_duration_seconds"]=10,["timing_tolerance"]="one recorded sample period; provisional",["study_package_access"]=false });
            initialized=true;panel.enabled=true;
        }
        void Update()
        {
            if(failed || Flow==null)return;
            try
            {
                if(!initialized) { InitializeDemos();if(!initialized && PanelJournal.NowMs-started>30000) throw new OrientationFault("ORIENTATION_SOURCE_STARTUP_TIMEOUT"); }
                bool active=Flow.Stage!=OrientationStage.NotStarted && Flow.Stage!=OrientationStage.RecordedOutcome;
                if(active && (!focused || paused || !foundation.Ready || !panel.InputAvailable || panel.FaultLatched)) throw new OrientationFault("ORIENTATION_INPUT_OR_TRACKING_LOST");
                if(active && stateSource.Stale) throw new OrientationFault("ORIENTATION_STATE_STALE");
                if(Flow.Stage==OrientationStage.Fault) throw new OrientationFault("ORIENTATION_FLOW_FAILED");
                Refresh();PollButton();
            }
            catch(OrientationFault e) { Fail(e.Message); }
            catch { Fail("ORIENTATION_RUNTIME_FAILED"); }
        }
        public void Advance()
        {
            if(!buttonEnabled || failed || !focused || paused) return;
            try
            {
                if(Flow.Stage==OrientationStage.NotStarted) { if(!panel.ReadyForTrial || !stateSource.ConfirmReset()) throw new OrientationFault("ORIENTATION_START_NOT_READY");Flow.Start(); }
                else Flow.Next();
                EnterStage();Refresh();
            }
            catch(OrientationFault e) { Fail(e.Message); }
            catch { Fail("ORIENTATION_ADVANCE_FAILED"); }
        }
        void EnterStage()
        {
            if(Flow.Stage==OrientationStage.Action)
            {
                activeDemo=demos[Flow.CurrentCard.Id];stateSource.RestoreSnapshotNeutral();
                if(!stateSource.ConfirmReset()) throw new OrientationFault("ORIENTATION_DEMO_RESET_REFUSED");
                stateSource.PlaySnapshotTrajectory(activeDemo.CopyBytes(),activeDemo.Sha256,activeDemo.DurationSeconds);
            }
            else if(Flow.Stage==OrientationStage.Target) { activeDemo=null;stateSource.RestoreSnapshotNeutral(); }
            else if(Flow.Stage==OrientationStage.Practice)
            {
                activeDemo=null;stateSource.RestoreSnapshotNeutral();
                if(!stateSource.ConfirmReset() || !panel.ReadyForTrial) throw new OrientationFault("ORIENTATION_PRACTICE_NOT_READY");
                panel.Open(Flow.OpenPractice());
            }
        }
        void Response(PanelResponse response)
        {
            if(failed)return;
            try { Flow.Respond(response);panel.CloseAtBoundary();Refresh(); }
            catch(OrientationFault e) { Fail(e.Message); }
            catch { Fail("ORIENTATION_RESPONSE_FAILED"); }
        }
        void SourceEvent(SourceEvent value)
        {
            if(failed || Flow==null)return;
            try
            {
                if(value.Code=="TRAJECTORY_COMPLETED" && activeDemo!=null)
                { Flow.CompleteDemo(activeDemo.Action,(value.DurationSeconds??double.NaN)*1000,activeDemo.DurationSeconds*1000,activeDemo.FrameToleranceMs);Refresh(); }
                else if(Flow.Stage!=OrientationStage.NotStarted && Flow.Stage!=OrientationStage.RecordedOutcome && (value.Code=="STATE_STALE" || value.Code=="STATE_SOURCE_RENDER_FAILED" || value.Code=="STATE_NONPROGRESSING")) Fail("ORIENTATION_STATE_SOURCE_FAULT");
            }
            catch { Fail("ORIENTATION_DEMO_EVIDENCE_FAILED"); }
        }
        void FoundationFault(string _) => Fail("ORIENTATION_FOUNDATION_FAULT");
        void PanelFault(string _) => Fail("ORIENTATION_PANEL_FAULT");
        void Fail(string code)
        {
            if(failed)return;failed=true;faultCode=code;buttonEnabled=false;
            if(journal==null && foundation!=null && foundation.Configuration!=null)
                try { journal=new OrientationJournal(Path.Combine(Application.persistentDataPath,"operator-logs"),StationConfig.ParseStrict(Resources.Load<TextAsset>("BuildIdentity").text),(string)foundation.Configuration["station_id"],null,null); }catch { }
            try { panel?.CloseAtBoundary();if(stateSource!=null&&stateSource.Initialized&&stateSource.Kind=="snapshot")stateSource.RestoreSnapshotNeutral(); }catch { }
            try { if(Flow!=null) Flow.Fault(code);else journal?.Record(new JObject { ["event"]="orientation_fault",["code"]=code,["mono_ms"]=PanelJournal.NowMs }); }catch { }
            Debug.LogError(code);Refresh();
        }
        void Refresh()
        {
            if(display==null)return;
            if(failed) { title.text="Orientation unavailable";body.text=Wrap(faultCode+". Ask the operator.");buttonText.text="Blocked";buttonEnabled=false; }
            else
            {
                string prefix=setup.Plan.EngineeringDraft?"Engineering draft - owner review pending\n":"";
                switch(Flow.Stage)
                {
                    case OrientationStage.NotStarted:title.text="Silent orientation";body.text=Wrap(prefix+"Eight actions, eight targets, then written practice. No study sound plays.");buttonText.text="Start";buttonEnabled=initialized&&panel.ReadyForTrial;break;
                    case OrientationStage.Action:title.text=Flow.CurrentCard.Title;body.text=Wrap(prefix+Flow.CurrentCard.Meaning+"\nKinematic visualization");buttonText.text=Flow.DemoComplete?"Next":"Demonstration";buttonEnabled=Flow.DemoComplete;break;
                    case OrientationStage.Target:title.text=Flow.CurrentCard.Title;body.text=Wrap(prefix+Flow.CurrentCard.Meaning);buttonText.text="Next";buttonEnabled=true;break;
                    case OrientationStage.Practice:title.text="Written practice "+Flow.ItemNumber+" / 8";body.text=Wrap(prefix+Flow.CurrentItem.Request);buttonText.text="Use the response panel";buttonEnabled=false;break;
                    case OrientationStage.Feedback:title.text=Flow.LastCorrect==true?"Correct":"Review this request";body.text=Wrap(prefix+Flow.CurrentItem.Request+"\nExpected: "+Flow.CurrentItem.Target+" / "+Flow.CurrentItem.Action.Replace('_',' '));buttonText.text="Continue";buttonEnabled=true;break;
                    case OrientationStage.Reexplanation:title.text="One re-explanation";body.text=Wrap(prefix+"We will repeat the same explanation once, then check the same eight requests in the stored second order.");buttonText.text="Repeat explanation";buttonEnabled=true;break;
                    case OrientationStage.RecordedOutcome:title.text="Outcome recorded";body.text=Wrap(prefix+(Flow.Outcome.Passed?"The check is complete. Please wait for the operator.":"The second check is complete. Please speak with the operator.")+"\nNo allocation is revealed here.");buttonText.text="Wait for operator";buttonEnabled=false;break;
                }
            }
            buttonSurface.sharedMaterial.color=buttonEnabled?new Color(.12f,.3f,.4f):new Color(.08f,.09f,.1f);
        }
        static string Wrap(string text)
        {
            var output=new StringBuilder();int line=0;
            foreach(string paragraph in text.Split('\n'))
            {
                if(output.Length>0)output.Append('\n');line=0;
                foreach(string word in paragraph.Split(' ')) { if(line>0&&line+word.Length+1>54) { output.Append('\n');line=0; }if(line>0){output.Append(' ');line++;}output.Append(word);line+=word.Length; }
            }
            return output.ToString();
        }
        void CreateDisplay(Pose reference)
        {
            if(shader==null||font==null)throw new OrientationFault("ORIENTATION_RENDER_RESOURCES");
            display=new GameObject("Silent orientation screen").transform;display.SetParent(foundation.presentationRoot.transform,false);
            display.SetPositionAndRotation(reference.position+reference.rotation*new Vector3(0,.24f,.95f),reference.rotation);
            textBackingMaterial=new Material(shader){color=new Color(.035f,.045f,.06f)};
            var backing=GameObject.CreatePrimitive(PrimitiveType.Cube);backing.transform.SetParent(display,false);backing.transform.localScale=new Vector3(.72f,.28f,.012f);DisposeObject(backing.GetComponent<Collider>());backing.GetComponent<Renderer>().sharedMaterial=textBackingMaterial;
            title=Label(display,new Vector3(0,.095f,-.012f),.012f);body=Label(display,new Vector3(0,.015f,-.012f),.008f);
            var key=GameObject.CreatePrimitive(PrimitiveType.Cube);key.transform.SetParent(display,false);key.transform.localPosition=new Vector3(0,-.102f,-.005f);key.transform.localScale=new Vector3(.32f,.045f,.015f);
            button=key.GetComponent<BoxCollider>();buttonSurface=key.GetComponent<MeshRenderer>();surfaceMaterial=new Material(shader);buttonSurface.sharedMaterial=surfaceMaterial;
            buttonText=Label(display,new Vector3(0,-.102f,-.016f),.009f);
            pointer=new GameObject("Orientation pointer").AddComponent<LineRenderer>();pointer.transform.SetParent(display,false);pointer.positionCount=2;pointer.startWidth=pointer.endWidth=.0015f;pointer.sharedMaterial=surfaceMaterial;pointer.enabled=false;
            Refresh();
        }
        TextMesh Label(Transform parent,Vector3 position,float height)
        {
            var label=new GameObject("Silent text").AddComponent<TextMesh>();label.transform.SetParent(parent,false);label.transform.localPosition=position;
            label.font=font;label.fontSize=100;label.characterSize=.01f;label.anchor=TextAnchor.MiddleCenter;label.alignment=TextAlignment.Center;label.richText=false;label.color=Color.white;label.text="A";
            label.GetComponent<Renderer>().sharedMaterial=font.material;
            label.transform.localScale=Vector3.one*(height/PanelTypography.LocalInkHeight(label));return label;
        }
        void PollButton()
        {
            pointer.enabled=false;
            if(!buttonEnabled || !focused || paused || !foundation.Ready) { armed=held=haveTip=pokeArmed=false;return; }
            Transform tracking=panel.trackingSpace;
            if(input.InputMethod=="controller_ray")
            {
                var device=InputDevices.GetDeviceAtXRNode(input.LeftHand?XRNode.LeftHand:XRNode.RightHand);
                if(!device.TryGetFeatureValue(CommonUsages.isTracked,out bool tracked)||!tracked || !device.TryGetFeatureValue(CommonUsages.trackingState,out InputTrackingState state) || (state&(InputTrackingState.Position|InputTrackingState.Rotation))!=(InputTrackingState.Position|InputTrackingState.Rotation) || !device.TryGetFeatureValue(CommonUsages.devicePosition,out Vector3 p) || !device.TryGetFeatureValue(CommonUsages.deviceRotation,out Quaternion q) || !device.TryGetFeatureValue(CommonUsages.triggerButton,out bool down)) { armed=held=false;return; }
                float norm=q.x*q.x+q.y*q.y+q.z*q.z+q.w*q.w;if(!Finite(p)||!float.IsFinite(norm)||Mathf.Abs(norm-1)>.001f) { armed=held=false;return; }
                var ray=new Ray(tracking.TransformPoint(p),tracking.TransformDirection(q*Vector3.forward));pointer.enabled=true;pointer.SetPosition(0,ray.origin);pointer.SetPosition(1,ray.GetPoint(2));
                if(!down)armed=true;if(down&&!held&&armed) { armed=false;if(button.Raycast(ray,out _,2))Advance(); }held=down;
            }
            else
            {
                hands.Clear();SubsystemManager.GetSubsystems(hands);var subsystem=hands.Find(x=>x.running);var hand=subsystem==null?default:input.LeftHand?subsystem.leftHand:subsystem.rightHand;
                if(subsystem==null||!hand.isTracked||!hand.GetJoint(XRHandJointID.IndexTip).TryGetPose(out Pose pose)||!Finite(pose.position)) { haveTip=pokeArmed=false;return; }
                Vector3 p=tracking.TransformPoint(pose.position),local=button.transform.InverseTransformPoint(p);
                if(local.z<-.5f-.04f/.015f)pokeArmed=true;
                if(haveTip&&pokeArmed&&local.z>=-.5f) { var delta=p-previousTip;if(delta.sqrMagnitude>0 && button.Raycast(new Ray(previousTip,delta.normalized),out _,delta.magnitude))Advance();pokeArmed=false; }
                previousTip=p;haveTip=true;
            }
        }
        static bool Finite(Vector3 p)=>float.IsFinite(p.x)&&float.IsFinite(p.y)&&float.IsFinite(p.z);
        void OnApplicationFocus(bool value) { focused=value;if(!value&&Flow!=null&&Flow.Stage!=OrientationStage.NotStarted&&Flow.Stage!=OrientationStage.RecordedOutcome)Fail("ORIENTATION_FOCUS_LOST"); }
        void OnApplicationPause(bool value) { paused=value;if(value&&Flow!=null&&Flow.Stage!=OrientationStage.NotStarted&&Flow.Stage!=OrientationStage.RecordedOutcome)Fail("ORIENTATION_PAUSED"); }
        void OnDisable() { if(Application.isPlaying && Flow!=null)Fail("ORIENTATION_COMPONENT_DISABLED"); }
        void OnDestroy()
        {
            if(foundation!=null)foundation.Faulted-=FoundationFault;if(stateSource!=null)stateSource.Event-=SourceEvent;
            if(panel!=null){panel.Responded-=Response;panel.Faulted-=PanelFault;}
            journal?.Dispose();if(surfaceMaterial!=null)DisposeObject(surfaceMaterial);if(textBackingMaterial!=null)DisposeObject(textBackingMaterial);
        }
        static void DisposeObject(UnityEngine.Object value) { if(Application.isPlaying)Destroy(value);else DestroyImmediate(value); }
    }
}
