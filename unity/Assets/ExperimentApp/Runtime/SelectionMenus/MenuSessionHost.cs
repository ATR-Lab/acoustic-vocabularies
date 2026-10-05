using System;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.Foundation;
using AcousticVocab.ResponsePanel;
using AcousticVocab.SessionEngine;
using AcousticVocab.StateIntegration;
using AcousticVocab.StudyAudio;
using UnityEngine;
using UnityEngine.UI;
using UnityEngine.XR;
using UnityEngine.XR.Hands;

namespace AcousticVocab.SelectionMenus
{
    [DisallowMultipleComponent]
    public sealed class MenuSessionHost:MonoBehaviour,IMenuView
    {
        public FoundationBootstrap foundation;public AudioPlayer player;public ResponsePanelController panel;public StateSourceHost source;
        public Transform presentationParent;public Font font;
        MenuContentFactory factory;FixedSlotEngine engine;Canvas canvas;Text instructions,meaning;RawImage picture;
        readonly Text[] labels=new Text[3];readonly Image[] cards=new Image[3];readonly BoxCollider[] colliders=new BoxCollider[3];
        readonly Dictionary<string,Texture2D> textures=new Dictionary<string,Texture2D>(StringComparer.Ordinal);readonly List<XRHandSubsystem> hands=new List<XRHandSubsystem>();
        string visibleOwner,visibleImage,packageHash,scheduleHash,inputMethod;bool leftHand,failed,focused=true,paused,handlingFault,readOnly,choosing,triggerArmed,triggerDown,haveTip,pokeArmed;Vector3 previousTip;
        public bool Installed=>factory!=null&&!failed&&isActiveAndEnabled;
        public bool InputAvailable{get;private set;}
        public string FaultCode{get;private set;}public string CleanupFailureCode{get;private set;}
        public event Action<int> Chosen;public event Action<string> Faulted;
        public MenuContentFactory Install(MenuCatalog catalog,IMenuStore store,PrivateModeResetClient backend,AudioRouteCalibration route,float gain,
            Action<MenuEvent> durableMenuSink,Action<AudioPlaybackEvent> durableAudioSink,Action<string> faultSink,MenuReplaySequence replay=null,bool engineeringPreview=false,Action<SlotContext,int,PcmWave> bindAudio=null)
        {
            MenuRules.Require(factory==null&&!failed&&catalog!=null&&foundation!=null&&foundation.Ready&&panel!=null&&source!=null&&player!=null&&route!=null&&route.IsQualified&&route.UncertaintyMs<=20&&Faulted!=null&&faultSink!=null&&(!catalog.Demo||engineeringPreview),"MENU_HOST_NOT_READY");
            inputMethod=(string)foundation.Configuration?["input_method"];MenuRules.Require((inputMethod=="controllers"||inputMethod=="hands")&&panel.ConfiguredLeftHand.HasValue,"MENU_INPUT_CONFIGURATION");leftHand=panel.ConfiguredLeftHand.Value;if(canvas==null)CreateView();packageHash=catalog.PackageSha256;scheduleHash=catalog.ScheduleSha256;
            factory=new MenuContentFactory(catalog,store,backend,player,panel,source,this,()=>isActiveAndEnabled&&focused&&!paused&&!failed&&foundation.Ready,durableMenuSink,durableAudioSink,code=>{var error=Attempt(()=>Fail(code),()=>faultSink(code));if(error!=null)throw error;},replay,bindAudio);
            try{player.Configure(route,()=>factory.ExposureGate);player.SetComfortableGain(gain);return factory;}catch{Attempt(()=>factory.Dispose(),()=>player.Abort("MENU_INSTALL_FAILED"));failed=true;throw;}
        }
        public void BindEngine(FixedSlotEngine owner)
        {MenuRules.Require(Installed&&engine==null&&owner!=null&&owner.PackageSha256==packageHash&&owner.ScheduleSha256==scheduleHash,"MENU_ENGINE_BINDING");engine=owner;}
        void Update()
        {
            if(!Installed)return;
            try
            {
                bool prior=InputAvailable;InputAvailable=focused&&!paused&&foundation.Ready&&(inputMethod=="controllers"?Controller():Hand());
                if(!InputAvailable){triggerArmed=triggerDown=pokeArmed=haveTip=false;if(prior)Fail("MENU_INPUT_LOST");}
                if(engine==null)factory.Pump();
            }
            catch(SessionFault e){Fail(e.Code);}catch{Fail("MENU_HOST_FAILED");}
        }
        static bool Finite(Vector3 value)=>float.IsFinite(value.x)&&float.IsFinite(value.y)&&float.IsFinite(value.z);
        bool Controller()
        {
            var device=InputDevices.GetDeviceAtXRNode(leftHand?XRNode.LeftHand:XRNode.RightHand);
            if(!device.isValid||!device.TryGetFeatureValue(CommonUsages.isTracked,out bool tracked)||!tracked||!device.TryGetFeatureValue(CommonUsages.trackingState,out InputTrackingState tracking)||(tracking&(InputTrackingState.Position|InputTrackingState.Rotation))!=(InputTrackingState.Position|InputTrackingState.Rotation)||!device.TryGetFeatureValue(CommonUsages.devicePosition,out Vector3 position)||!Finite(position)||!device.TryGetFeatureValue(CommonUsages.deviceRotation,out Quaternion rotation)||!device.TryGetFeatureValue(CommonUsages.triggerButton,out bool down))return false;
            float norm=rotation.x*rotation.x+rotation.y*rotation.y+rotation.z*rotation.z+rotation.w*rotation.w;if(!float.IsFinite(norm)||Mathf.Abs(norm-1)>.001f)return false;
            var trackingSpace=foundation.observerCamera.transform.parent;Vector3 origin=trackingSpace.TransformPoint(position),direction=trackingSpace.TransformDirection(rotation*Vector3.forward);if(!Finite(direction))return false;
            if(!down)triggerArmed=true;if(down&&!triggerDown&&triggerArmed){Hit(new Ray(origin,direction),2);triggerArmed=false;}triggerDown=down;return true;
        }
        bool Hand()
        {
            hands.Clear();SubsystemManager.GetSubsystems(hands);var subsystem=hands.Find(x=>x.running);if(subsystem==null)return false;var hand=leftHand?subsystem.leftHand:subsystem.rightHand;
            if(!hand.isTracked||!hand.GetJoint(XRHandJointID.IndexTip).TryGetPose(out Pose pose)||!Finite(pose.position))return false;
            Vector3 point=foundation.observerCamera.transform.parent.TransformPoint(pose.position);Vector3 local=canvas.transform.InverseTransformPoint(point);
            if(local.z< -35)pokeArmed=true;if(haveTip&&pokeArmed&&local.z>= -7.5f){var delta=point-previousTip;if(delta.sqrMagnitude>0)Hit(new Ray(previousTip,delta.normalized),delta.magnitude);pokeArmed=false;}previousTip=point;haveTip=true;return true;
        }
        void Hit(Ray ray,float distance)
        {
            if(!Installed||!focused||paused||!choosing||readOnly||canvas==null||!canvas.gameObject.activeInHierarchy)return;
            int index=-1;float nearest=distance;for(int i=0;i<3;i++)if(colliders[i].Raycast(ray,out var hit,distance)&&hit.distance<nearest){nearest=hit.distance;index=i;}
            if(index>=0)Chosen?.Invoke(index+1);
        }
        void CreateView()
        {
            MenuRules.Require(canvas==null&&presentationParent!=null&&font!=null,"MENU_VIEW_UNAVAILABLE");var root=new GameObject("Selection menu",typeof(RectTransform),typeof(Canvas));root.transform.SetParent(presentationParent,false);root.transform.localPosition=new Vector3(0,.02f,1.05f);root.transform.localScale=Vector3.one*.001f;
            canvas=root.GetComponent<Canvas>();canvas.renderMode=RenderMode.WorldSpace;((RectTransform)root.transform).sizeDelta=new Vector2(840,570);instructions=TextAt(root.transform,"Instructions",new Vector2(0,225),new Vector2(820,95),28);meaning=TextAt(root.transform,"Meaning",new Vector2(0,145),new Vector2(810,75),26);
            var img=new GameObject("Meaning image",typeof(RectTransform),typeof(CanvasRenderer),typeof(RawImage));img.transform.SetParent(root.transform,false);var rect=(RectTransform)img.transform;rect.anchoredPosition=new Vector2(0,20);rect.sizeDelta=new Vector2(240,170);picture=img.GetComponent<RawImage>();picture.raycastTarget=false;
            for(int i=0;i<3;i++){var node=new GameObject("Candidate "+(i+1),typeof(RectTransform),typeof(CanvasRenderer),typeof(Image),typeof(BoxCollider));node.transform.SetParent(root.transform,false);var box=(RectTransform)node.transform;box.anchoredPosition=new Vector2((i-1)*270,-165);box.sizeDelta=new Vector2(245,130);cards[i]=node.GetComponent<Image>();cards[i].color=new Color(.15f,.18f,.23f);cards[i].raycastTarget=false;colliders[i]=node.GetComponent<BoxCollider>();colliders[i].size=new Vector3(245,130,15);labels[i]=TextAt(node.transform,"Stored label",Vector2.zero,new Vector2(235,120),27);}
            root.SetActive(false);
        }
        Text TextAt(Transform parent,string name,Vector2 at,Vector2 size,int fontSize)
        {var node=new GameObject(name,typeof(RectTransform),typeof(CanvasRenderer),typeof(Text));node.transform.SetParent(parent,false);var rect=(RectTransform)node.transform;rect.anchoredPosition=at;rect.sizeDelta=size;var text=node.GetComponent<Text>();text.font=font;text.fontSize=fontSize;text.alignment=TextAnchor.MiddleCenter;text.color=Color.white;text.raycastTarget=false;text.supportRichText=false;return text;}
        public void Prepare(MenuMaterial material)
        {
            MenuRules.Require(Installed&&material!=null,"MENU_VIEW_REFUSED");if(material.Meaning==null||textures.ContainsKey(material.Meaning.ImageSha256))return;
            var texture=new Texture2D(2,2,TextureFormat.RGBA32,false);try{MenuRules.Require(texture.LoadImage(material.Meaning.CopyImage(),true)&&texture.width<=2048&&texture.height<=2048,"MENU_IMAGE_INVALID");}catch{Destroy(texture);throw;}
            foreach(string key in textures.Keys.Where(x=>x!=visibleImage).ToArray()){Destroy(textures[key]);textures.Remove(key);}textures.Add(material.Meaning.ImageSha256,texture);
        }
        public void Show(string owner,MenuMaterial material,MenuPhase phase,int? selected,bool readOnly)
        {
            MenuRules.Require(Installed&&owner!=null&&material!=null&&phase is MenuPhase.Instructions or MenuPhase.Audition or MenuPhase.Choice or MenuPhase.Selected,"MENU_VIEW_REFUSED");
            MenuRules.Require(!(readOnly&&phase==MenuPhase.Choice&&selected.HasValue),"MENU_YOKED_DISCLOSURE");
            visibleOwner=owner;this.readOnly=readOnly;choosing=phase==MenuPhase.Choice;instructions.text=phase==MenuPhase.Choice?material.ChoiceInstructions:material.Instructions;meaning.text=material.Meaning?.Definition??"";
            picture.gameObject.SetActive(material.Meaning!=null);if(material.Meaning!=null){MenuRules.Require(textures.TryGetValue(material.Meaning.ImageSha256,out var texture),"MENU_IMAGE_MISSING");picture.texture=texture;visibleImage=material.Meaning.ImageSha256;}
            for(int i=0;i<3;i++){labels[i].text=material.Labels[i];cards[i].color=selected==i+1?new Color(.5f,.4f,.1f):new Color(.15f,.18f,.23f);}canvas.gameObject.SetActive(true);
        }
        public void Hide(string owner){if(visibleOwner!=owner)return;canvas.gameObject.SetActive(false);visibleOwner=visibleImage=null;choosing=false;}
        public void Uninstall()
        {
            // Detach lease ownership first. Delayed lifecycle callbacks on this
            // host must not touch a player already owned by a later module.
            var previous=factory;factory=null;engine=null;packageHash=scheduleHash=null;InputAvailable=false;choosing=false;
            if(canvas!=null)canvas.gameObject.SetActive(false);visibleOwner=visibleImage=null;
            if(previous!=null){var error=Attempt(previous.Dispose,()=>player?.Abort("MENU_UNINSTALLED"));if(error!=null)throw new SessionFault(error is SessionFault s?s.Code:"MENU_CLEANUP_FAILED");}
        }
        static Exception Attempt(params Action[] steps){Exception first=null;foreach(var step in steps)try{step();}catch(Exception e){if(first==null)first=e;}return first;}
        void Fail(string code)
        {
            if(failed||handlingFault)return;failed=true;handlingFault=true;FaultCode=new SessionFault(code).Code;
            try{var first=Attempt(()=>{if(canvas!=null)canvas.gameObject.SetActive(false);},()=>engine?.Fault(FaultCode),Uninstall);if(first!=null)CleanupFailureCode=first is SessionFault s?s.Code:"MENU_CLEANUP_FAILED";foreach(var callback in Faulted?.GetInvocationList()??Array.Empty<Delegate>())try{((Action<string>)callback)(FaultCode);}catch{CleanupFailureCode??="MENU_FAULT_OBSERVER_FAILED";}}
            finally{handlingFault=false;}
        }
        void OnApplicationFocus(bool value){focused=value;if(!value&&factory!=null)Fail("MENU_FOCUS_LOST");}
        void OnApplicationPause(bool value){paused=value;if(value&&factory!=null)Fail("MENU_APPLICATION_PAUSED");}
        void OnDisable(){if(factory!=null)Fail("MENU_HOST_DISABLED");InputAvailable=false;}
        void OnDestroy(){var first=Attempt(Uninstall);if(first!=null)CleanupFailureCode="MENU_CLEANUP_FAILED";foreach(var texture in textures.Values)Destroy(texture);textures.Clear();if(canvas!=null)Destroy(canvas.gameObject);}
    }
}
