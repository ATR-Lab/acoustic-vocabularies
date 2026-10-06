using System.IO;
using AcousticVocab.Foundation;
using AcousticVocab.Foundation.Editor;
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
            var background=new GameObject("Background",typeof(RectTransform),typeof(CanvasRenderer),typeof(Image));
            background.transform.SetParent(canvas.transform,false);
            var bgRect=background.GetComponent<RectTransform>();bgRect.anchorMin=Vector2.zero;bgRect.anchorMax=Vector2.one;bgRect.offsetMin=bgRect.offsetMax=Vector2.zero;
            background.GetComponent<Image>().color=new Color(.055f,.07f,.09f,1);
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
            var events=new GameObject("Calibration input",typeof(EventSystem),typeof(InputSystemUIInputModule));
            events.GetComponent<InputSystemUIInputModule>().AssignDefaultActions();
            events.GetComponent<EventSystem>().sendNavigationEvents=false;
            var line=new GameObject("Controller ray").AddComponent<LineRenderer>();
            line.positionCount=2;line.useWorldSpace=true;line.startWidth=line.endWidth=.002f;line.enabled=false;
            line.startColor=line.endColor=new Color(.4f,.85f,1f,1);
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
        static Text Text(Transform parent,string name,string text,Vector2 position,Vector2 size,int fontSize)
        {
            var obj=new GameObject(name,typeof(RectTransform),typeof(CanvasRenderer),typeof(Text));obj.transform.SetParent(parent,false);
            var rect=obj.GetComponent<RectTransform>();rect.anchoredPosition=position;rect.sizeDelta=size;
            var label=obj.GetComponent<Text>();label.text=text;label.font=Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            label.fontSize=fontSize;label.color=Color.white;label.alignment=TextAnchor.MiddleCenter;label.raycastTarget=false;
            return label;
        }
        static Button Button(Transform parent,string text,Vector2 position,Vector2 size)
        {
            var obj=new GameObject(text,typeof(RectTransform),typeof(CanvasRenderer),typeof(Image),typeof(Button));obj.transform.SetParent(parent,false);
            var rect=obj.GetComponent<RectTransform>();rect.anchoredPosition=position;rect.sizeDelta=size;
            var graphic=obj.GetComponent<Image>();graphic.color=new Color(.12f,.22f,.31f,1);
            var button=obj.GetComponent<Button>();button.targetGraphic=graphic;button.navigation=new Navigation { mode=Navigation.Mode.None };
            Text(obj.transform,"Label",text,Vector2.zero,size-Vector2.one*10,28);return button;
        }
        public static void BuildAndroid() { Configure();FoundationBuild.BuildAndroid(); }
        public static void BuildWindows() { Configure();FoundationBuild.BuildWindows(); }
    }
}
