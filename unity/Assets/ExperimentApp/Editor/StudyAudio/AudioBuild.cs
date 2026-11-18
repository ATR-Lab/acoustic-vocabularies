using System.IO;
using AcousticVocab.Foundation;
using AcousticVocab.Foundation.Editor;
using AcousticVocab.Theme;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem.UI;
using UnityEngine.UI;

namespace AcousticVocab.StudyAudio.Editor
{
    public static class AudioBuild
    {
        public static void Configure()
        {
            FoundationBuild.Configure();
            var scene=EditorSceneManager.OpenScene(FoundationBuild.ScenePath);
            var foundation=Object.FindAnyObjectByType<FoundationBootstrap>();
            var host=new GameObject("Audio calibration").AddComponent<AudioCalibrationHost>();
            host.foundation=foundation;
            var player=new GameObject("Nonsemantic audio").AddComponent<AudioPlayer>();
            host.player=player;
            var source=player.GetComponent<AudioSource>();source.playOnAwake=false;source.spatialBlend=0;
            var canvasObject=new GameObject("Comfort screening",typeof(RectTransform),typeof(Canvas),typeof(GraphicRaycaster));
            var canvas=canvasObject.GetComponent<Canvas>();canvas.renderMode=RenderMode.WorldSpace;canvas.worldCamera=foundation.observerCamera;
            canvasObject.GetComponent<RectTransform>().sizeDelta=new Vector2(900,620);
            canvasObject.transform.SetPositionAndRotation(new Vector3(0,1.2f,1.3f),Quaternion.identity);canvasObject.transform.localScale=Vector3.one*.0015f;
            host.canvas=canvas;
            BuildView(canvas,host);
            var events=new GameObject("Calibration input",typeof(EventSystem),typeof(InputSystemUIInputModule));
            events.GetComponent<InputSystemUIInputModule>().AssignDefaultActions();
            events.GetComponent<EventSystem>().sendNavigationEvents=false;
            var line=new GameObject("Controller ray").AddComponent<LineRenderer>();
            line.positionCount=2;line.useWorldSpace=true;line.startWidth=line.endWidth=.002f;line.enabled=false;
            line.startColor=line.endColor=StudyTheme.Pointer;
            Directory.CreateDirectory("Assets/Generated.local.data/StudyAudio");
            const string materialPath="Assets/Generated.local.data/StudyAudio/Pointer.mat";
            var material=AssetDatabase.LoadAssetAtPath<Material>(materialPath);
            if(material==null) { material=new Material(Shader.Find("Sprites/Default"));AssetDatabase.CreateAsset(material,materialPath); }
            line.sharedMaterial=material;host.controllerRay=line;
            EditorSceneManager.SaveScene(scene,FoundationBuild.CalibrationScenePath);
            FoundationBuild.ParticipantScenePath=FoundationBuild.CalibrationScenePath;
            AssetDatabase.SaveAssets();FoundationBuild.VerifyParticipantScene();
            Debug.Log("AUDIO_CALIBRATION_SCENE_CONFIGURED");
        }
        // The comfort screen in the participant theme. Positions, sizes and
        // label sizes are the reviewed ones; the theme adds colour, a hairline
        // edge (an Image just outside each rect) and a display-only level bar.
        public static void BuildView(Canvas canvas,AudioCalibrationHost host)
        {
            Edge(canvas.transform,"Background",Vector2.zero,canvas.GetComponent<RectTransform>().sizeDelta,true);
            var background=new GameObject("Background",typeof(RectTransform),typeof(CanvasRenderer),typeof(Image));
            background.transform.SetParent(canvas.transform,false);
            var bgRect=background.GetComponent<RectTransform>();bgRect.anchorMin=Vector2.zero;bgRect.anchorMax=Vector2.one;bgRect.offsetMin=bgRect.offsetMax=Vector2.zero;
            background.GetComponent<Image>().color=StudyTheme.PanelFill;background.GetComponent<Image>().raycastTarget=false;
            Text(canvas.transform,"Title","Choose a comfortable level",new Vector2(0,245),new Vector2(840,60),38);
            host.statusText=Text(canvas.transform,"Status","Ask the operator to start.",new Vector2(0,160),new Vector2(800,100),28);
            host.gainText=Text(canvas.transform,"Level","Level: --",new Vector2(0,60),new Vector2(300,50),32);
            host.quieterButton=Button(canvas.transform,"Quieter",new Vector2(-255,60),new Vector2(180,60));
            host.louderButton=Button(canvas.transform,"Louder",new Vector2(255,60),new Vector2(180,60));
            host.listenButton=Button(canvas.transform,"Listen twice",new Vector2(0,-30),new Vector2(350,70));
            host.yesButton=Button(canvas.transform,"Yes, comfortable",new Vector2(-205,-125),new Vector2(340,70));
            host.noButton=Button(canvas.transform,"No",new Vector2(205,-125),new Vector2(340,70));
            host.stopButton=Button(canvas.transform,"Stop",new Vector2(0,-235),new Vector2(220,60));
            foreach(var button in new[]{host.listenButton,host.quieterButton,host.louderButton,host.yesButton,host.noButton,host.stopButton}) button.interactable=false;
            // Display-only bar between the level row and the listen button. It
            // shows the gain the level logic set; it takes no input.
            var track=Rect(canvas.transform,"Level track",new Vector2(0,17),new Vector2(600,10),StudyTheme.SurfaceDisabled);
            host.levelFill=Rect(track.transform,"Level fill",Vector2.zero,Vector2.zero,StudyTheme.EdgeLit);
            host.levelKnob=Rect(track.transform,"Level knob",Vector2.zero,new Vector2(20,20),StudyTheme.TextPrimary);
            CalibrationLevelView.Show(host.levelFill,host.levelKnob,ComfortableGainStore.InitialGain);
        }
        static RectTransform Rect(Transform parent,string name,Vector2 position,Vector2 size,Color color)
        {
            var obj=new GameObject(name,typeof(RectTransform),typeof(CanvasRenderer),typeof(Image));obj.transform.SetParent(parent,false);
            var rect=obj.GetComponent<RectTransform>();rect.anchoredPosition=position;rect.sizeDelta=size;
            var image=obj.GetComponent<Image>();image.color=color;image.raycastTarget=false;return rect;
        }
        // Teal hairline: an Image one theme edge larger than the element, drawn before it.
        static void Edge(Transform parent,string name,Vector2 position,Vector2 size,bool stretch=false)
        {
            float e=StudyTheme.EdgeWidthCanvasUnits;
            var rect=Rect(parent,name+" edge",position,size+Vector2.one*2*e,StudyTheme.EdgeIdle);
            if(stretch){rect.anchorMin=Vector2.zero;rect.anchorMax=Vector2.one;rect.offsetMin=-Vector2.one*e;rect.offsetMax=Vector2.one*e;}
        }
        static Text Text(Transform parent,string name,string text,Vector2 position,Vector2 size,int fontSize)
        {
            var obj=new GameObject(name,typeof(RectTransform),typeof(CanvasRenderer),typeof(Text));obj.transform.SetParent(parent,false);
            var rect=obj.GetComponent<RectTransform>();rect.anchoredPosition=position;rect.sizeDelta=size;
            var label=obj.GetComponent<Text>();label.text=text;label.font=Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            label.fontSize=fontSize;label.color=StudyTheme.TextPrimary;label.fontStyle=StudyTheme.TextStyle;label.alignment=TextAnchor.MiddleCenter;label.raycastTarget=false;
            return label;
        }
        static Button Button(Transform parent,string text,Vector2 position,Vector2 size)
        {
            Edge(parent,text,position,size);
            var obj=new GameObject(text,typeof(RectTransform),typeof(CanvasRenderer),typeof(Image),typeof(Button));obj.transform.SetParent(parent,false);
            var rect=obj.GetComponent<RectTransform>();rect.anchoredPosition=position;rect.sizeDelta=size;
            var graphic=obj.GetComponent<Image>();graphic.color=Color.white;
            var button=obj.GetComponent<Button>();button.targetGraphic=graphic;button.navigation=new Navigation { mode=Navigation.Mode.None };
            // State tints multiply the white graphic, so each state shows its token exactly.
            button.colors=new ColorBlock { normalColor=StudyTheme.SurfaceEnabled,highlightedColor=StudyTheme.SurfaceSelected,pressedColor=StudyTheme.SurfaceSelected,
                selectedColor=StudyTheme.SurfaceEnabled,disabledColor=StudyTheme.SurfaceDisabled,colorMultiplier=1,fadeDuration=.1f };
            Text(obj.transform,"Label",text,Vector2.zero,size-Vector2.one*10,28);return button;
        }
        public static void BuildAndroid() { Configure();FoundationBuild.BuildAndroid(); }
        public static void BuildWindows() { Configure();FoundationBuild.BuildWindows(); }
    }
}
