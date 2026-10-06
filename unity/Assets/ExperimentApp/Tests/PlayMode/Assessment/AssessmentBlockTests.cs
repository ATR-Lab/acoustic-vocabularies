using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using AcousticVocab.ResponsePanel;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace AcousticVocab.Assessment.PlayModeTests
{
    // Real Unity lifecycle and actual protected renderer, with controlled clocks
    // and synthetic content. Graphics comparison is a separate named test;
    // a null graphics device never counts as a rendered-frame pass.
    public sealed class AssessmentBlockTests
    {
        sealed class Clock:ISessionClock{public double NowMs{get;set;}}
        sealed class Journal:ISessionJournal{public readonly List<SessionRecord> Rows=new List<SessionRecord>();public IReadOnlyList<SessionRecord> Records=>Rows;public void Append(SessionRecord r)=>Rows.Add(r);}
        sealed class StagesJournal:IAssessmentJournal{public readonly List<AssessmentRecord> Rows=new List<AssessmentRecord>();public IReadOnlyList<AssessmentRecord> Records=>Rows;public void Append(AssessmentRecord r)=>Rows.Add(r);}
        sealed class State:IProtectedState{public bool ModeReady=>true;public bool NeutralReady=>true;public bool ResetComplete=>true;public bool FocusOk=>true;public void BeginTrial(){}public void RequestReset(){}public void Pump(){}}
        sealed class Audio:IAssessmentAudio
        {public bool Ready=>true;public double? QualifiedOnsetMonoMs{get;private set;}public string FaultCode=>null;public void Prepare(SlotContext c){}public void Request(SlotContext c,INovelSlotAuthorization n,ISpeechSlotAuthorization s){QualifiedOnsetMonoMs=c.OnsetMonoMs;}public void Stop(string c){}}
        sealed class Panel:IAssessmentPanel
        {
            public readonly ResponseState State;public bool Ready=>true;public event Action<string> Responded;
            public Panel(Clock c){State=new ResponseState(()=>c.NowMs,_=>{});State.Responded+=r=>Responded?.Invoke(r.Code==ResponseCode.Commit?"commit":r.Code==ResponseCode.DontKnow?"dont_know":"timeout");}
            public void Tick()=>State.Tick();public void Open(PanelRequest r)=>State.Open(r);public void Hide()=>State.Abort();
        }
        sealed class Fixture:IDisposable
        {
            public readonly Clock Clock=new Clock();public readonly Panel Panel;public readonly Journal Journal=new Journal();
            public readonly GameObject Root=new GameObject("Synthetic assessment view");public readonly AssessmentAcknowledgmentView View;
            public readonly FixedSlotEngine Engine;public readonly ProtectedContentFactory Factory;
            public Fixture(bool atomic)
            {
                Root.transform.position=new Vector3(0,0,1.05f);View=new AssessmentAcknowledgmentView(Root.transform,Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf"));Panel=new Panel(Clock);
                var item=new SlotItem("DEMO-frame",atomic?"atomic":"trained",atomic?"K-a1":"K-a1-r1",atomic?"action":null,null,"protected",false,atomic?9:14,1,1);
                var schedule=new VisitSchedule(new string('a',64),new string('b',64),"DEMO","D0",true,new[]{new ScheduleBlock("trained",new[]{item})},"A","A");
                var stages=new AssessmentStages(schedule,Journal,new StagesJournal(),Clock,()=>Engine!=null&&Engine.Status==SessionState.Complete);
                Factory=new ProtectedContentFactory(Clock,new State(),new Audio(),Panel,View,stages,null,x=>Engine.RecordResponse(x),x=>Engine.Fault(x),_=>false);
                Engine=new FixedSlotEngine(schedule,Clock,Journal,Factory);Engine.ConfirmResume();Engine.Tick();
            }
            public void At(double ms){Clock.NowMs=ms;Engine.Tick();}
            public void Respond(string outcome,bool atomic)
            {
                if(outcome=="timeout")return;if(outcome=="dont_know"){Panel.State.DontKnow();return;}
                if(!atomic)Panel.State.SelectTarget(outcome=="correct"?"A":"B");Panel.State.SelectAction(outcome=="wrong"&&atomic?"REMOVE_ONE":"ADD_ONE");Panel.State.Commit();
            }
            public void Dispose(){Factory.Dispose();UnityEngine.Object.Destroy(Root);}
        }
        [UnityTest] public IEnumerator AcknowledgmentLifecycleRetainsExactlyTwoSecondsForEveryOutcome()
        {
            foreach(bool atomic in new[]{false,true})foreach(string outcome in new[]{"correct","wrong","dont_know","timeout"})
            {
                using var f=new Fixture(atomic);f.At(750);f.At(1750);f.Respond(outcome,atomic);yield return null;
                int close=atomic?7750:12750,end=close+2000;f.At(close-1);Assert.That(f.Root.activeSelf,Is.False);f.At(close);yield return null;
                Assert.That(f.Root.activeSelf,Is.True);Assert.That(f.View.Text.text,Is.EqualTo("Response recorded"));f.At(end-1);yield return null;
                Assert.That(f.Root.activeSelf,Is.True);f.At(end);Assert.That(f.Root.activeSelf,Is.False);Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Complete));
                Assert.That(f.Journal.Rows.Count(x=>x.Event=="response"),Is.EqualTo(1));
            }
        }
        // Run explicitly without -nographics; excluded from the ordinary suite
        // by its category, not reported as an optional passing/skipped check.
        [UnityTest,Category("GraphicsRequired")] public IEnumerator RenderedAcknowledgmentFramesMatchAllOutcomes()
        {
            Assert.That(SystemInfo.graphicsDeviceType,Is.Not.EqualTo(UnityEngine.Rendering.GraphicsDeviceType.Null));
            var cameraObject=new GameObject("Synthetic comparison camera");var camera=cameraObject.AddComponent<Camera>();camera.clearFlags=CameraClearFlags.SolidColor;camera.backgroundColor=Color.black;camera.fieldOfView=45;camera.nearClipPlane=.01f;camera.farClipPlane=10;
            var target=new RenderTexture(1024,512,24,RenderTextureFormat.ARGB32);camera.targetTexture=target;var pixels=new Texture2D(1024,512,TextureFormat.RGBA32,false);
            string directory=Environment.GetEnvironmentVariable("ASSESSMENT_FRAME_EVIDENCE");if(!string.IsNullOrEmpty(directory))Directory.CreateDirectory(directory);
            var hashes=new List<string>();
            try
            {
                string baseline=null;
                foreach(bool atomic in new[]{false,true})foreach(string outcome in new[]{"correct","wrong","dont_know","timeout"})
                {
                    using var f=new Fixture(atomic);f.At(750);f.At(1750);f.Respond(outcome,atomic);f.At(atomic?7750:12750);yield return null;
                    camera.Render();RenderTexture.active=target;pixels.ReadPixels(new Rect(0,0,1024,512),0,0);pixels.Apply();RenderTexture.active=null;
                    byte[] rgba=pixels.GetRawTextureData();Assert.That(rgba.Where((_,i)=>i%4!=3).Count(x=>x>10),Is.GreaterThan(100),"Text must actually be visible; blank frames cannot pass.");
                    string hash=PcmWave.Hash(rgba);hashes.Add(hash);if(baseline==null)baseline=hash;else Assert.That(hash,Is.EqualTo(baseline));
                    if(!string.IsNullOrEmpty(directory))File.WriteAllBytes(Path.Combine(directory,(atomic?"atomic-":"full-")+outcome+".png"),pixels.EncodeToPNG());
                    f.At(atomic?9750:14750);yield return null;
                }
                if(!string.IsNullOrEmpty(directory))File.WriteAllLines(Path.Combine(directory,"rgba-sha256.txt"),hashes);
            }
            finally{RenderTexture.active=null;camera.targetTexture=null;target.Release();UnityEngine.Object.Destroy(target);UnityEngine.Object.Destroy(pixels);UnityEngine.Object.Destroy(cameraObject);}
        }
    }
}
