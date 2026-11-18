using System;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.Foundation;
using AcousticVocab.ResponsePanel;
using AcousticVocab.SessionEngine;
using AcousticVocab.StateIntegration;
using AcousticVocab.StudyAudio;
using AcousticVocab.Theme;
using UnityEngine;
using UnityEngine.UI;

namespace AcousticVocab.Teaching
{
    // Admission, allocation and schedule dispatch remain with the session
    // owner. An uninstalled scene shows no meaning and cannot request audio.
    [DisallowMultipleComponent]
    public sealed class TeachingSessionHost : MonoBehaviour,ITeachingView,ILessonStoryView
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
        Image actionBacking,targetBacking;
        // Storyboard layer (presentation only): glass card, decorative motif
        // strips with the aligned playhead, the grammar timeline row, and a
        // separate step-chip canvas that stays up during the try while the
        // meaning canvas is hidden.
        StoryPanel card,stepsCard;
        StoryWaveform actionStrip,targetStrip;
        RawImage stripSpeaker;
        GameObject grammarRow;
        Canvas steps;
        StoryStepChips stepChips;
        Text question;
        string stepOwner;
        RawImage image;
        readonly Dictionary<string,Texture2D> textures=new Dictionary<string,Texture2D>(StringComparer.Ordinal);
        string visibleOwner,visibleImageHash,scheduleHash,packageHash;
        bool failed,focused=true,paused,handlingFault,uninstalled;
        public bool Installed => factory!=null&&!failed&&isActiveAndEnabled;
        public string FaultCode {get;private set;}
        public string CleanupFailureCode {get;private set;}
        public event Action<string> Faulted;
        public TeachingContentFactory Install(TeachingCatalog catalog,ITeachingSelections selections,ITeachingBackend backend,
            AudioRouteCalibration qualifiedRoute,float storedComfortableGain,Action<LessonEvent> durableLessonSink,
            Action<AudioPlaybackEvent> durableAudioSink,Action<string> responseSink,Action<string> faultSink,bool engineeringPreview=false,Action<SlotContext,int,PcmWave> beforeSchedule=null,Action<ContentCueGateRefusal> cueRefused=null)
        {
            LessonTimeline.Require(factory==null&&!uninstalled&&!failed&&foundation!=null&&foundation.Ready&&panel!=null&&source!=null&&player!=null&&
                qualifiedRoute!=null&&qualifiedRoute.CanScheduleSoftware&&qualifiedRoute.UncertaintyMs<=20&&Faulted!=null&&(!catalog.Demo||engineeringPreview)&&(grammar==null||grammar.Complete),"LESSON_HOST_NOT_READY");
            grammar?.Dispose();grammar=null;if(canvas==null)CreateView();
            scheduleHash=catalog.ScheduleSha256;packageHash=catalog.PackageSha256;
            factory=new TeachingContentFactory(catalog,selections,backend,player,panel,source,this,
                ()=>isActiveAndEnabled&&focused&&!paused&&!failed&&foundation.Ready,durableLessonSink,durableAudioSink,responseSink,
                code=>{Fail(code);faultSink(code);},beforeSchedule,cueRefused);
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
            Action<Newtonsoft.Json.Linq.JObject> durableGrammarSink,Action<AudioPlaybackEvent> durableAudioSink,Action<string> gateRefused=null)
        {
            LessonTimeline.Require(factory==null&&grammar==null&&!uninstalled&&!failed&&isActiveAndEnabled&&foundation!=null&&foundation.Ready&&panel!=null&&panel.ReadyForTrial&&source!=null&&source.CheckExposureReady()&&Faulted!=null&&independentNeutralControlGate!=null,"GRAMMAR_HOST_NOT_READY");
            if(canvas==null)CreateView();
            bool Gate()
            {
                // Preserve the original short-circuit order and source reads.
                // Only a failed check emits its bounded diagnostic.
                string code=!isActiveAndEnabled?"GRAMMAR_HOST_INACTIVE":!focused?"GRAMMAR_FOCUS_LOST":paused?"GRAMMAR_PAUSED":failed?"GRAMMAR_HOST_FAULTED":
                    !foundation.Ready?"GRAMMAR_FOUNDATION_NOT_READY":!panel.ReadyForTrial?"GRAMMAR_INPUT_NOT_READY":
                    !source.CheckExposureReady()?"GRAMMAR_SOURCE_NOT_READY":!independentNeutralControlGate()?"GRAMMAR_PRIVATE_CONTROL_NOT_READY":null;
                if(code==null)return true;gateRefused?.Invoke(code);return false;
            }
            grammar=new GrammarFamiliarization(assets,player,route,gain,
                Gate,
                ShowGrammar,durableGrammarSink,durableAudioSink,Fail);
        }
        void ShowGrammar(string phase)
        {
            visibleOwner="grammar";image.gameObject.SetActive(false);feedback.text="";definition.text=phase=="ready"?"READY":"";
            actionWords.text=phase=="roles"?"Action":"";targetWords.text=phase=="roles"?"Target":"";Light(LessonHighlight.None);
            SetLessonStrips(false,false);grammarRow.SetActive(phase=="roles");HideSteps();
            canvas.gameObject.SetActive(phase!="hidden");
        }
        void CreateView()
        {
            LessonTimeline.Require(presentationParent!=null&&font!=null&&foundation!=null&&foundation.Configuration!=null,"LESSON_VIEW_UNAVAILABLE");
            var reference=StationConfig.ReferencePose(foundation.Configuration);
            var root=new GameObject("Teaching presentation",typeof(RectTransform),typeof(Canvas));root.transform.SetParent(presentationParent,false);
            root.transform.SetPositionAndRotation(reference.position+reference.rotation*new Vector3(0,.05f,1.05f),reference.rotation);root.transform.localScale=Vector3.one*.001f;
            canvas=root.GetComponent<Canvas>();canvas.renderMode=RenderMode.WorldSpace;((RectTransform)root.transform).sizeDelta=new Vector2(800,540);
            definition=TextAt("Definition",new Vector2(0,220),new Vector2(780,100));
            actionWords=TextAt("Action words",new Vector2(-190,140),new Vector2(370,70),out actionBacking);targetWords=TextAt("Target words",new Vector2(190,140),new Vector2(370,70),out targetBacking);
            feedback=TextAt("Feedback",new Vector2(0,-220),new Vector2(780,80));
            var picture=new GameObject("Task image",typeof(RectTransform),typeof(CanvasRenderer),typeof(RawImage));picture.transform.SetParent(root.transform,false);
            var rect=(RectTransform)picture.transform;rect.sizeDelta=new Vector2(460,270);rect.anchoredPosition=new Vector2(0,-25);image=picture.GetComponent<RawImage>();image.raycastTarget=false;
            CreateStoryLayer(root.transform,reference);
            root.SetActive(false);
        }
        // Decorative storyboard elements sit outside every existing label rect
        // (labels span y -260..270): motif strips below the feedback line, the
        // step chips above the definition on their own canvas, and the grammar
        // row in the image area, which is hidden whenever that row shows.
        internal const float StripY=-305,StepsY=305,GrammarRowY=20;
        void CreateStoryLayer(Transform root,Pose reference)
        {
            card=StoryPanel.Create(root,"Story card",new Vector2(0,0),new Vector2(860,690));
            stripSpeaker=StoryUi.Icon(root,"Motif speaker",StoryIcon.Speaker,new Vector2(-392,StripY),30,StudyTheme.EdgeLit);
            actionStrip=StoryWaveform.Create(root,"Action motif strip",new Vector2(-190,StripY),new Vector2(330,34),11);
            targetStrip=StoryWaveform.Create(root,"Target motif strip",new Vector2(190,StripY),new Vector2(330,34),23);
            grammarRow=StoryUi.Rect(root,"Grammar timeline",new Vector2(0,GrammarRowY),new Vector2(780,70)).gameObject;
            StoryUi.Icon(grammarRow.transform,"Speaker",StoryIcon.Speaker,new Vector2(-350,0),40,StudyTheme.EdgeLit);
            StoryWaveform.Create(grammarRow.transform,"Action part",new Vector2(-200,0),new Vector2(220,50),5).SetLit(true);
            StoryUi.Label(grammarRow.transform,"Pause",Vector2.zero,new Vector2(110,44),font,24,"pause",StudyTheme.PanelFill,false);
            StoryWaveform.Create(grammarRow.transform,"Target part",new Vector2(200,0),new Vector2(220,50),9).SetLit(true);
            grammarRow.SetActive(false);
            var stepsRoot=new GameObject("Lesson steps",typeof(RectTransform),typeof(Canvas));stepsRoot.transform.SetParent(presentationParent,false);
            // 2 mm toward the viewer so the chips never z-fight the card behind them.
            stepsRoot.transform.SetPositionAndRotation(root.position+reference.rotation*new Vector3(0,0,-.002f),reference.rotation);stepsRoot.transform.localScale=Vector3.one*.001f;
            steps=stepsRoot.GetComponent<Canvas>();steps.renderMode=RenderMode.WorldSpace;steps.sortingOrder=1;((RectTransform)stepsRoot.transform).sizeDelta=new Vector2(800,540);
            stepsCard=StoryPanel.Create(stepsRoot.transform,"Steps card",new Vector2(0,StepsY-45),new Vector2(860,170));
            stepChips=StoryStepChips.Create(stepsRoot.transform,"Step chips",new Vector2(0,StepsY),font,24,190,"Listen","Your turn","Check");
            question=StoryUi.Label(stepsRoot.transform,"Question",new Vector2(0,StepsY-80),new Vector2(780,60),font,27,"",StudyTheme.PanelFill,false);
            stepsRoot.SetActive(false);
        }
        void SetLessonStrips(bool action,bool target)
        {
            actionStrip.SetPlayhead(null);targetStrip.SetPlayhead(null);
            actionStrip.gameObject.SetActive(action);targetStrip.gameObject.SetActive(target);stripSpeaker.gameObject.SetActive(action||target);
        }
        void HideSteps(){stepOwner=null;if(steps!=null)steps.gameObject.SetActive(false);}
        public void Step(string owner,LessonDisplayPhase phase,LessonQuestion kind)
        {
            if(steps==null||owner==null)return;
            if(phase!=LessonDisplayPhase.Definition&&stepOwner!=owner)return;
            int current=phase==LessonDisplayPhase.Definition?0:phase==LessonDisplayPhase.Retrieval?1:phase==LessonDisplayPhase.Feedback?2:-1;
            if(current<0){HideSteps();return;}
            stepOwner=owner;stepChips.Set(current);
            // The prompt never carries the answer; the meaning canvas stays hidden during the try.
            bool trying=current==1;
            question.text=trying?(kind==LessonQuestion.Action?"Which action was that?":kind==LessonQuestion.Target?"Which target was that?":"Which target and action was that?"):"";
            question.gameObject.SetActive(trying);question.transform.parent.Find("Question surface").gameObject.SetActive(trying);
            stepsCard.gameObject.SetActive(trying);
            steps.gameObject.SetActive(true);
        }
        public void Playhead(string owner,LessonHighlight segment,float? fraction)
        {
            if(visibleOwner!=owner||actionStrip==null)return;
            actionStrip.SetPlayhead(segment==LessonHighlight.Action?fraction:null);
            targetStrip.SetPlayhead(segment==LessonHighlight.Target?fraction:null);
        }
        Text TextAt(string name,Vector2 position,Vector2 size)=>TextAt(name,position,size,out _);
        Text TextAt(string name,Vector2 position,Vector2 size,out Image backing)
        {
            backing=PresentationTextBacking.Add(canvas.transform,name,position,size);
            var node=new GameObject(name,typeof(RectTransform),typeof(CanvasRenderer),typeof(Text));node.transform.SetParent(canvas.transform,false);
            var rect=(RectTransform)node.transform;rect.anchoredPosition=position;rect.sizeDelta=size;
            var text=node.GetComponent<Text>();text.font=font;text.fontSize=27;text.alignment=TextAnchor.MiddleCenter;text.color=StudyTheme.TextPrimary;text.fontStyle=StudyTheme.TextStyle;text.supportRichText=false;text.raycastTarget=false;return text;
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
            Light(LessonHighlight.None);grammarRow.SetActive(false);SetLessonStrips(!string.IsNullOrEmpty(display.ActionWords),!string.IsNullOrEmpty(display.TargetWords));
            visibleOwner=owner;canvas.gameObject.SetActive(true);
        }
        public void Hide(string owner)
        {if(visibleOwner!=owner)return;canvas.gameObject.SetActive(false);visibleOwner=null;visibleImageHash=null;}
        public void Highlight(string owner,LessonHighlight value)
        {if(visibleOwner!=owner)return;Light(value);}
        // A lit label gets the teal fill and bright edge; text stays white so its
        // contrast never depends on the highlight. Static lessons never light.
        void Light(LessonHighlight value)
        {
            actionWords.color=targetWords.color=StudyTheme.TextPrimary;
            PresentationTextBacking.Style(actionBacking,value==LessonHighlight.Action?StudyTheme.HighlightFill:StudyTheme.PanelFill,value==LessonHighlight.Action);
            PresentationTextBacking.Style(targetBacking,value==LessonHighlight.Target?StudyTheme.HighlightFill:StudyTheme.PanelFill,value==LessonHighlight.Target);
        }
        internal void Fail(string code)
        {
            if(uninstalled||failed||handlingFault)return;handlingFault=true;failed=true;
            FaultCode=new SessionFault(code).Code;
            try
            {
                var error=TeachingCleanup.Attempt(()=>{if(canvas!=null)canvas.gameObject.SetActive(false);},HideSteps,
                    ()=>engine?.Fault(FaultCode),()=>grammar?.Dispose(),()=>factory?.Dispose(),
                    ()=>player?.Abort(FaultCode),()=>TeachingCleanup.Notify(Faulted,FaultCode));
                if(error!=null)CleanupFailureCode=TeachingCleanup.Code(error);
            }
            finally{handlingFault=false;}
        }
        void OnApplicationFocus(bool value){focused=value;if(!value&&(factory!=null||grammar!=null))Fail("LESSON_FOCUS_LOST");}
        void OnApplicationPause(bool value){paused=value;if(value&&(factory!=null||grammar!=null))Fail("LESSON_APPLICATION_PAUSED");}
        void OnDisable(){if(factory!=null||grammar!=null)Fail("LESSON_HOST_DISABLED");}
        // The construction scope invokes this before another module acquires
        // the player. Fresh leases use a fresh host and fresh backend. Clearing
        // ownership first makes deferred Unity callbacks inert for later leases.
        public void Uninstall()
        {
            if(uninstalled)return;uninstalled=true;var formerFactory=factory;var formerGrammar=grammar;
            factory=null;grammar=null;engine=null;scheduleHash=null;packageHash=null;
            bool owned=formerFactory!=null||formerGrammar!=null;
            var formerCanvas=canvas;var formerSteps=steps;
            var error=TeachingCleanup.Attempt(()=>formerGrammar?.Dispose(),()=>formerFactory?.Dispose(),
                ()=>{if(owned)player?.Abort("LESSON_HOST_UNINSTALLED");},
                ()=>{if(formerCanvas!=null){formerCanvas.gameObject.SetActive(false);Destroy(formerCanvas.gameObject);}},
                ()=>{if(formerSteps!=null){formerSteps.gameObject.SetActive(false);Destroy(formerSteps.gameObject);}});
            // Dispose may legitimately call this view's Hide, so retain its
            // canvas until those callbacks finish; ownership was already cleared.
            canvas=null;steps=null;
            if(error!=null){failed=true;CleanupFailureCode=TeachingCleanup.Code(error);TeachingCleanup.ThrowFirst(error);}
        }
        void OnDestroy()
        {
            if(!uninstalled&&(factory!=null||grammar!=null))Fail("LESSON_HOST_DESTROYED");
            TeachingCleanup.Attempt(Uninstall);
            foreach(var texture in textures.Values)if(texture!=null)Destroy(texture);textures.Clear();
        }
    }
}
