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

namespace AcousticVocab.Teaching
{
    // Admission, allocation and schedule dispatch remain with the session
    // owner. An uninstalled scene shows no meaning and cannot request audio.
    [DisallowMultipleComponent]
    public sealed class TeachingSessionHost : MonoBehaviour,ITeachingView
    {
        public FoundationBootstrap foundation;
        public AudioPlayer player;
        public ResponsePanelController panel;
        public StateSourceHost source;
        public Transform presentationParent;
        public Font font;
        TeachingContentFactory factory;
        GrammarFamiliarization grammar;
        FixedSlotEngine engine;
        Canvas canvas;
        Text definition,actionWords,targetWords,feedback;
        RawImage image;
        readonly Dictionary<string,Texture2D> textures=new Dictionary<string,Texture2D>(StringComparer.Ordinal);
        string visibleOwner,visibleImageHash,scheduleHash,packageHash;
        bool failed,focused=true,paused,handlingFault;
        public bool Installed => factory!=null&&!failed&&isActiveAndEnabled;
        public event Action<string> Faulted;
        public TeachingContentFactory Install(TeachingCatalog catalog,ITeachingSelections selections,ITeachingBackend backend,
            AudioRouteCalibration qualifiedRoute,float storedComfortableGain,Action<LessonEvent> durableLessonSink,
            Action<AudioPlaybackEvent> durableAudioSink,Action<string> responseSink,Action<string> faultSink,bool engineeringPreview=false)
        {
            LessonTimeline.Require(factory==null&&!failed&&foundation!=null&&foundation.Ready&&panel!=null&&source!=null&&player!=null&&
                qualifiedRoute!=null&&qualifiedRoute.IsQualified&&qualifiedRoute.UncertaintyMs<=20&&Faulted!=null&&(!catalog.Demo||engineeringPreview)&&(grammar==null||grammar.Complete),"LESSON_HOST_NOT_READY");
            grammar?.Dispose();grammar=null;if(canvas==null)CreateView();
            scheduleHash=catalog.ScheduleSha256;packageHash=catalog.PackageSha256;
            factory=new TeachingContentFactory(catalog,selections,backend,player,panel,source,this,
                ()=>isActiveAndEnabled&&focused&&!paused&&!failed&&foundation.Ready,durableLessonSink,durableAudioSink,responseSink,
                code=>{Fail(code);faultSink(code);});
            player.Configure(qualifiedRoute,()=>factory.ExposureGate);player.SetComfortableGain(storedComfortableGain);
            return factory;
        }
        // The session owner builds a multiplexer including this lesson factory
        // and the other visit blocks, then binds its sole engine here. No auto
        // resume occurs, and this component never chooses a schedule.
        public void BindEngine(FixedSlotEngine owner)
        {LessonTimeline.Require(Installed&&engine==null&&owner!=null&&owner.ScheduleSha256==scheduleHash&&owner.PackageSha256==packageHash,"LESSON_ENGINE_BINDING");engine=owner;}
        void Update()
        {
            if(grammar!=null){grammar.Tick();return;}
            if(!Installed)return;
            // When bound, the sole external engine/console driver owns Tick;
            // the factory pump is invoked by the engine itself before deadlines.
            try{if(engine==null)factory.Tick();}catch(SessionFault e){Fail(e.Code);}catch{Fail("LESSON_HOST_FAILED");}
        }
        public bool GrammarComplete=>grammar?.Complete==true;
        // Call only at the script's explicit familiarization step; it is not a
        // trial replay. The provided gate must include independently verified
        // neutral/control readiness. The host adds focus, input and foundation.
        public void BeginGrammar(GrammarAssets assets,AudioRouteCalibration route,float gain,Func<bool> independentNeutralControlGate,
            Action<Newtonsoft.Json.Linq.JObject> durableGrammarSink,Action<AudioPlaybackEvent> durableAudioSink)
        {
            LessonTimeline.Require(factory==null&&grammar==null&&!failed&&isActiveAndEnabled&&foundation!=null&&foundation.Ready&&panel!=null&&panel.ReadyForTrial&&source!=null&&source.CheckExposureReady()&&Faulted!=null&&independentNeutralControlGate!=null,"GRAMMAR_HOST_NOT_READY");
            if(canvas==null)CreateView();
            grammar=new GrammarFamiliarization(assets,player,route,gain,
                ()=>isActiveAndEnabled&&focused&&!paused&&!failed&&foundation.Ready&&panel.ReadyForTrial&&source.CheckExposureReady()&&independentNeutralControlGate(),
                ShowGrammar,durableGrammarSink,durableAudioSink,Fail);
        }
        void ShowGrammar(string phase)
        {
            visibleOwner="grammar";image.gameObject.SetActive(false);feedback.text="";definition.text=phase=="ready"?"READY":"";
            actionWords.text=phase=="roles"?"Action":"";targetWords.text=phase=="roles"?"Target":"";actionWords.color=targetWords.color=Color.white;
            canvas.gameObject.SetActive(phase!="hidden");
        }
        void CreateView()
        {
            LessonTimeline.Require(presentationParent!=null&&font!=null,"LESSON_VIEW_UNAVAILABLE");
            var root=new GameObject("Teaching presentation",typeof(RectTransform),typeof(Canvas));root.transform.SetParent(presentationParent,false);
            root.transform.localPosition=new Vector3(0,.05f,1.05f);root.transform.localScale=Vector3.one*.001f;
            canvas=root.GetComponent<Canvas>();canvas.renderMode=RenderMode.WorldSpace;((RectTransform)root.transform).sizeDelta=new Vector2(800,540);
            definition=TextAt("Definition",new Vector2(0,220),new Vector2(780,100));
            actionWords=TextAt("Action words",new Vector2(-190,140),new Vector2(370,70));targetWords=TextAt("Target words",new Vector2(190,140),new Vector2(370,70));
            feedback=TextAt("Feedback",new Vector2(0,-220),new Vector2(780,80));
            var picture=new GameObject("Task image",typeof(RectTransform),typeof(CanvasRenderer),typeof(RawImage));picture.transform.SetParent(root.transform,false);
            var rect=(RectTransform)picture.transform;rect.sizeDelta=new Vector2(460,270);rect.anchoredPosition=new Vector2(0,-25);image=picture.GetComponent<RawImage>();image.raycastTarget=false;
            root.SetActive(false);
        }
        Text TextAt(string name,Vector2 position,Vector2 size)
        {
            var node=new GameObject(name,typeof(RectTransform),typeof(CanvasRenderer),typeof(Text));node.transform.SetParent(canvas.transform,false);
            var rect=(RectTransform)node.transform;rect.anchoredPosition=position;rect.sizeDelta=size;
            var text=node.GetComponent<Text>();text.font=font;text.fontSize=27;text.alignment=TextAnchor.MiddleCenter;text.color=Color.white;text.supportRichText=false;text.raycastTarget=false;return text;
        }
        public void Prepare(TeachingDisplay display)
        {
            LessonTimeline.Require(Installed&&display!=null,"LESSON_DISPLAY_REFUSED");if(textures.ContainsKey(display.ImageSha256))return;
            var texture=new Texture2D(2,2,TextureFormat.RGBA32,false);
            try{LessonTimeline.Require(texture.LoadImage(display.CopyImage(),true)&&texture.width<=2048&&texture.height<=2048,"LESSON_IMAGE_INVALID");}
            catch{Destroy(texture);throw;}
            foreach(string key in textures.Keys.Where(x=>x!=visibleImageHash).ToArray()){Destroy(textures[key]);textures.Remove(key);}
            textures.Add(display.ImageSha256,texture);
        }
        public void Show(string owner,TeachingDisplay display,string feedbackText)
        {
            LessonTimeline.Require(Installed&&display!=null&&owner!=null,"LESSON_DISPLAY_REFUSED");
            LessonTimeline.Require(textures.TryGetValue(display.ImageSha256,out var texture),"LESSON_DISPLAY_REFUSED");
            visibleImageHash=display.ImageSha256;image.gameObject.SetActive(true);image.texture=texture;definition.text=display.Definition;actionWords.text=display.ActionWords;targetWords.text=display.TargetWords;feedback.text=feedbackText??"";
            actionWords.color=targetWords.color=Color.white;visibleOwner=owner;canvas.gameObject.SetActive(true);
        }
        public void Hide(string owner)
        {if(visibleOwner!=owner)return;canvas.gameObject.SetActive(false);visibleOwner=null;visibleImageHash=null;}
        public void Highlight(string owner,LessonHighlight value)
        {if(visibleOwner!=owner)return;actionWords.color=value==LessonHighlight.Action?Color.yellow:Color.white;targetWords.color=value==LessonHighlight.Target?Color.yellow:Color.white;}
        void Fail(string code)
        {
            if(failed||handlingFault)return;handlingFault=true;failed=true;
            try{if(canvas!=null)canvas.gameObject.SetActive(false);engine?.Fault(code);grammar?.Dispose();factory?.Dispose();Faulted?.Invoke(code);}finally{handlingFault=false;}
        }
        void OnApplicationFocus(bool value){focused=value;if(!value&&(factory!=null||grammar!=null))Fail("LESSON_FOCUS_LOST");}
        void OnApplicationPause(bool value){paused=value;if(value&&(factory!=null||grammar!=null))Fail("LESSON_APPLICATION_PAUSED");}
        void OnDisable(){if(factory!=null||grammar!=null)Fail("LESSON_HOST_DISABLED");}
        void OnDestroy(){grammar?.Dispose();factory?.Dispose();foreach(var texture in textures.Values)if(texture!=null)Destroy(texture);textures.Clear();}
    }
}
