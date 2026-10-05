using System;
using System.IO;
using System.Reflection;
using AcousticVocab.Foundation;
using AcousticVocab.Foundation.Editor;
using AcousticVocab.ResponsePanel;
using AcousticVocab.ResponsePanel.Editor;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace AcousticVocab.Orientation.Editor
{
    // Static engineering screens only: no source playback, HMD data or persistent app configuration.
    public static class OrientationPreview
    {
        static readonly BindingFlags Flags=BindingFlags.NonPublic|BindingFlags.Instance;
        public static void Capture()
        {
            if(Application.isPlaying)throw new InvalidOperationException("Editor-only screen preview");
            string directory=Environment.GetEnvironmentVariable("ORIENTATION_PREVIEW_OUTPUT")??throw new ArgumentException("Private output required");
            string prefix=Path.GetFullPath(Path.Combine(FoundationBuild.RepositoryRoot,".local"))+Path.DirectorySeparatorChar;
            if(!Path.GetFullPath(directory).StartsWith(prefix,StringComparison.OrdinalIgnoreCase)||Directory.Exists(directory))throw new ArgumentException("Fresh private folder required");
            Directory.CreateDirectory(directory);var captures=new JArray();
            foreach(string name in new[]{"intro","action","target","practice","feedback"})
            {
                OrientationBuild.Configure();var foundation=UnityEngine.Object.FindAnyObjectByType<FoundationBootstrap>();
                var panel=PanelPreview.Populate(foundation,PanelMode.Practice,PanelRole.Command);
                var host=foundation.GetComponent<OrientationHost>();var plan=OrientationPlan.Parse(File.ReadAllText(Path.Combine(FoundationBuild.RepositoryRoot,"apparatus/orientation/orientation-plan.example.json")),"engineering-pending-review");
                var setup=new OrientationSetup();typeof(OrientationSetup).GetProperty("Plan").SetValue(setup,plan);typeof(OrientationHost).GetField("setup",Flags).SetValue(host,setup);
                double now=0;var flow=new OrientationFlow(plan,()=>now,_=>{});typeof(OrientationHost).GetProperty("Flow").SetValue(host,flow);
                if(name!="intro")flow.Start();
                // These are synthetic UI states. No trajectory is loaded or applied and no demo qualification is recorded.
                if(name=="target"||name=="practice"||name=="feedback")
                    for(int i=0;i<8;i++){now+=10000;flow.CompleteDemo(flow.CurrentCard.Id,10000,10000,1000d/30);flow.Next();}
                if(name=="practice"||name=="feedback")
                {
                    for(int i=0;i<8;i++)flow.Next();var request=flow.OpenPractice();var state=new ResponseState(()=>now,_=>{});state.Open(request);state.Responded+=flow.Respond;
                    typeof(ResponsePanelController).GetProperty("State").SetValue(panel,state);
                    if(name=="feedback"){state.SelectTarget(flow.CurrentItem.Target);state.SelectAction(flow.CurrentItem.Action);state.Commit();}
                    typeof(ResponsePanelController).GetMethod("Refresh",Flags).Invoke(panel,null);
                }
                if(name!="practice")panel.CloseAtBoundary();
                typeof(OrientationHost).GetMethod("CreateDisplay",Flags).Invoke(host,new object[]{StationConfig.ReferencePose(foundation.Configuration)});
                var camera=foundation.observerCamera;camera.transform.SetPositionAndRotation(new Vector3(0,1.5f,1.45f),Quaternion.LookRotation(new Vector3(0,-.18f,-.7f),Vector3.up));camera.fieldOfView=80;
                var target=new RenderTexture(1920,1080,24);var pixels=new Texture2D(1920,1080,TextureFormat.RGB24,false);var old=RenderTexture.active;camera.targetTexture=target;
                try {camera.Render();RenderTexture.active=target;pixels.ReadPixels(new Rect(0,0,1920,1080),0,0);pixels.Apply();byte[] png=pixels.EncodeToPNG();string file=name+".png";File.WriteAllBytes(Path.Combine(directory,file),png);captures.Add(new JObject { ["file"]=file,["sha256"]=FoundationBuild.Hash(png),["stage"]=flow.Stage.ToString(),["synthetic_ui_state"]=true,["demo_playback_performed"]=false });}
                finally {RenderTexture.active=old;camera.targetTexture=null;UnityEngine.Object.DestroyImmediate(target);UnityEngine.Object.DestroyImmediate(pixels);}
            }
            File.WriteAllText(Path.Combine(directory,"capture.json"),new JObject { ["qualification"]="static_UI_preview_only_no_robot_demo_no_eligibility_evidence",["captures"]=captures }.ToString()+"\n");
            Debug.Log("ORIENTATION_STATIC_PREVIEWS count=5 actual_demo_playback=false");
        }
    }
}
