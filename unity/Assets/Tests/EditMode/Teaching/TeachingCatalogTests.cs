using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.ResponsePanel;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Teaching.Tests
{
    public sealed class TeachingCatalogTests
    {
        string directory,packageRoot,catalogRoot,repository,allocationHash;
        byte[] allocationBytes;
        LoadedAudioPackage package;
        VisitSchedule schedule;
        JObject catalog;
        string catalogHash,reviewHash;
        const string Hash="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        static byte[] Wave(byte[] pcm)
        {using var stream=new MemoryStream();using var w=new BinaryWriter(stream);w.Write(Encoding.ASCII.GetBytes("RIFF"));w.Write(36+pcm.Length);w.Write(Encoding.ASCII.GetBytes("WAVEfmt "));w.Write(16);w.Write((short)1);w.Write((short)1);w.Write(48000);w.Write(96000);w.Write((short)2);w.Write((short)16);w.Write(Encoding.ASCII.GetBytes("data"));w.Write(pcm.Length);w.Write(pcm);return stream.ToArray();}
        static void Json(string path,JObject value)=>File.WriteAllText(path,value.ToString()+"\n",new UTF8Encoding(false));
        [SetUp]public void Setup()
        {
            directory=Path.Combine(Path.GetTempPath(),"av-teaching-test-"+Guid.NewGuid().ToString("N"));packageRoot=Path.Combine(directory,"package");catalogRoot=Path.Combine(directory,"catalog");Directory.CreateDirectory(packageRoot);Directory.CreateDirectory(Path.Combine(catalogRoot,"images"));
            var repo=new DirectoryInfo(Directory.GetCurrentDirectory());while(repo!=null&&!Directory.Exists(Path.Combine(repo.FullName,"sound","examples","package-demo")))repo=repo.Parent;Assert.That(repo,Is.Not.Null);repository=repo.FullName;allocationBytes=null;allocationHash=null;
            foreach(string file in Directory.GetFiles(Path.Combine(repo.FullName,"sound","examples","package-demo"),"*.json"))File.Copy(file,Path.Combine(packageRoot,Path.GetFileName(file)));
            Directory.CreateDirectory(Path.Combine(packageRoot,"atoms"));Directory.CreateDirectory(Path.Combine(packageRoot,"messages"));
            var audio=JObject.Parse(File.ReadAllText(Path.Combine(packageRoot,"audio.json")));var atoms=new Dictionary<string,PcmWave>();int n=0;
            foreach(var atom in audio["atoms"])
            {
                var pcm=new byte[(int)atom["n_samples"]*2];for(int i=0;i<pcm.Length;i++)pcm[i]=(byte)((i*3+n)%251);n++;
                byte[] wav=Wave(pcm);var parsed=PcmWave.ParseCanonical(wav);atoms.Add((string)atom["atom_id"],parsed);File.WriteAllBytes(Path.Combine(packageRoot,(string)atom["path"]),wav);atom["pcm_sha256"]=parsed.PcmSha256;atom["file_sha256"]=parsed.FileSha256;
            }
            foreach(var row in audio["messages"])
            {
                var a=atoms[(string)row["action_atom"]];var r=atoms[(string)row["referent_atom"]];row["composite_sha256"]=MessageComposer.CompositeHash(a,r);
                if((string)row["status"]=="trained")
                {var pcm=new byte[(a.SampleCount+9600+r.SampleCount)*2];Buffer.BlockCopy(a.CopyPcm16(),0,pcm,0,a.SampleCount*2);Buffer.BlockCopy(r.CopyPcm16(),0,pcm,a.SampleCount*2+19200,r.SampleCount*2);byte[] wav=Wave(pcm);File.WriteAllBytes(Path.Combine(packageRoot,(string)row["path"]),wav);row["file_sha256"]=PcmWave.Hash(wav);}
            }
            Json(Path.Combine(packageRoot,"audio.json"),audio);var manifest=JObject.Parse(File.ReadAllText(Path.Combine(packageRoot,"manifest.json")));
            foreach(var entry in ((JObject)manifest["files"]).Properties()){var bytes=File.ReadAllBytes(Path.Combine(packageRoot,entry.Name));entry.Value["bytes"]=bytes.Length;entry.Value["sha256"]=PcmWave.Hash(bytes);}
            manifest["package_sha256"]=PackageLoader.CanonicalPackageHash(manifest.ToString());Json(Path.Combine(packageRoot,"manifest.json"),manifest);package=PackageLoader.Load(packageRoot,(string)manifest["package_sha256"],true);
            schedule=new VisitSchedule(Hash,package.PackageSha256,"DEMO-slot","D0",true,Array.Empty<ScheduleBlock>());
            byte[] png=Convert.FromBase64String("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7V8AAAAASUVORK5CYII=");File.WriteAllBytes(Path.Combine(catalogRoot,"images","fixture.png"),png);
            var permutation=JObject.Parse(File.ReadAllText(Path.Combine(packageRoot,"permutation.json")));
            var ids=permutation["atoms"].Select(x=>(string)x["atom_id"]).Concat(permutation["messages"].Where(x=>(string)x["status"]=="trained").Select(x=>(string)x["message_id"]));
            catalog=new JObject{["format"]="av-teaching/1",["package_sha256"]=package.PackageSha256,["content"]=new JArray(ids.Select(id=>new JObject{["content_id"]=id,["meaning_display_id"]="DEMO-display-"+id,["definition"]="Synthetic definition",["action_words"]="Synthetic action",["target_words"]="Synthetic target",["image_id"]="fixture"})),["images"]=new JObject{["fixture"]=new JObject{["file"]="images/fixture.png",["sha256"]=PcmWave.Hash(png)}},["feedback"]=new JObject()};
            foreach(string kind in new[]{"correct","incorrect","timeout"})catalog["feedback"][kind]=new JObject{["id"]="DEMO-feedback-"+kind,["text"]="Synthetic "+kind+" feedback"};SealCatalog();
        }
        void SealCatalog()
        {Json(Path.Combine(catalogRoot,"catalog.local.json"),catalog);catalogHash=PcmWave.Hash(File.ReadAllBytes(Path.Combine(catalogRoot,"catalog.local.json")));Json(Path.Combine(catalogRoot,"review.local.json"),new JObject{["version"]=1,["catalog_sha256"]=catalogHash,["approved"]=true,["methodology_sha256"]=Hash});reviewHash=PcmWave.Hash(File.ReadAllBytes(Path.Combine(catalogRoot,"review.local.json")));}
        TeachingCatalog Load()=>TeachingCatalog.Load(catalogRoot,catalogHash,reviewHash,package,schedule,File.ReadAllBytes(Path.Combine(packageRoot,"manifest.json")),File.ReadAllBytes(Path.Combine(packageRoot,"permutation.json")),allocationBytes,allocationHash);
        [TearDown]public void Cleanup(){if(directory!=null&&Directory.Exists(directory))Directory.Delete(directory,true);}
        [Test]public void AllTrainedContentBindsActualProducerPermutationAndComposition()
        {
            var loaded=Load();var rows=JObject.Parse(File.ReadAllText(Path.Combine(packageRoot,"permutation.json")))["messages"];
            int count=0;foreach(var row in rows.Where(x=>(string)x["status"]=="trained"))
            {var item=new SlotItem("DEMO-"+count++,"message_lesson",(string)row["message_id"],null,"structured","teaching",false,24,3,1);var material=loaded.Prepare(item,null);Assert.That(material.Wave.PcmSha256,Is.EqualTo(package.CompositeHash(item.ContentId)));Assert.That(material.ExpectedAction,Is.EqualTo((string)row["semantic_action"]));Assert.That(material.ExpectedTarget,Is.EqualTo((string)row["semantic_referent"]));}
            Assert.That(count,Is.EqualTo(18));
        }
        [Test]public void ChangedReviewCannotSelfAuthorize()
        {File.AppendAllText(Path.Combine(catalogRoot,"review.local.json")," ");Assert.Throws<SessionFault>(()=>Load());}
        [Test]public void ChangedImageRefused()
        {File.AppendAllText(Path.Combine(catalogRoot,"images","fixture.png")," ");Assert.Throws<SessionFault>(()=>Load());}
        [Test]public void ExtraFileRefused()
        {File.WriteAllText(Path.Combine(catalogRoot,"extra.txt"),"unexpected");Assert.Throws<SessionFault>(()=>Load());}
        [Test]public void UnknownCatalogKeyRefused()
        {catalog["heldout_allowed"]=true;SealCatalog();Assert.Throws<SessionFault>(()=>Load());}
        [Test]public void HeldoutInsteadOfTrainedContentRefused()
        {catalog["content"][16]["content_id"]="K-a1-r2";SealCatalog();Assert.Throws<SessionFault>(()=>Load());}
        [Test]public void StudyARejectsDictionarySchedule()
        {var item=new SlotItem("DEMO-dictionary","message_lesson","K-a1-r1",null,"dictionary","teaching",false,24,3,1);Assert.Throws<SessionFault>(()=>Load().Prepare(item,null));}
        [Test]public void CatalogCannotChangeScoringAnswer()
        {catalog["content"][0]["expected_action"]="SCAN";SealCatalog();Assert.Throws<SessionFault>(()=>Load());}
        [Test]public void MissingFeedbackOrInvalidReviewBooleanRefused()
        {var path=Path.Combine(catalogRoot,"review.local.json");var review=JObject.Parse(File.ReadAllText(path));review["approved"]="true";Json(path,review);reviewHash=PcmWave.Hash(File.ReadAllBytes(path));Assert.Throws<SessionFault>(()=>Load());}
        [Test]public void OversizedPngHeaderRefusedBeforeTextureAllocation()
        {byte[] png=File.ReadAllBytes(Path.Combine(catalogRoot,"images","fixture.png"));png[16]=1;File.WriteAllBytes(Path.Combine(catalogRoot,"images","fixture.png"),png);catalog["images"]["fixture"]["sha256"]=PcmWave.Hash(png);SealCatalog();Assert.Throws<SessionFault>(()=>Load());}
        // Drives the real response panel state; outcome is correct, wrong, dont_know or timeout.
        static PanelResponse Respond(PanelMode mode,PanelRole role,string outcome,string target=null,string action=null)
        {
            double now=750;var request=new PanelRequest("DEMO-feedback",mode,role,750);var state=new ResponseState(()=>now,_=>{});PanelResponse result=null;state.Responded+=x=>result=x;
            now=request.OpensMonoMs+100;state.Open(request);
            if(outcome=="timeout"){now=request.DeadlineMonoMs;state.Tick();}else if(outcome=="dont_know")Assert.That(state.DontKnow(),Is.True);
            else{if(target!=null)Assert.That(state.SelectTarget(target),Is.True);if(action!=null)Assert.That(state.SelectAction(action),Is.True);Assert.That(state.Commit(),Is.True);}
            Assert.That(result,Is.Not.Null);return result;
        }
        static LessonMaterial AtomicLesson(TeachingCatalog loaded,string id,string role)=>loaded.Prepare(new SlotItem("DEMO-"+id,"atomic_lesson",id,role,null,"teaching",false,20,3,1),null);
        static LessonMaterial MessageLesson(TeachingCatalog loaded,string id)=>loaded.Prepare(new SlotItem("DEMO-"+id,"message_lesson",id,null,"structured","teaching",false,24,3,1),null);
        // Study A schedules only the aligned format; feedback must not depend on it.
        static LessonMaterial DictionaryFormat(LessonMaterial m)=>new LessonMaterial{Display=m.Display,Wave=m.Wave,Action=m.Action,Referent=m.Referent,ExpectedAction=m.ExpectedAction,ExpectedTarget=m.ExpectedTarget,Atomic=false,Aligned=false};
        [Test,Category("REQ_LES_12"),NUnit.Framework.Property("Requirement","REQ-LES-12"),Category("REQ_LES_05"),NUnit.Framework.Property("Requirement","REQ-LES-05")]
        public void AtomicFeedbackFollowsTheSubmittedResponseForActionAndReferentRoles()
        {
            var loaded=Load();
            foreach(var (id,role,panelRole,right,wrong) in new (string,string,PanelRole,string,string)[]{("K-a1","action",PanelRole.Action,"REMOVE_ONE","ADD_ONE"),("K-r1","referent",PanelRole.Target,"A","B")})
            {
                var material=AtomicLesson(loaded,id,role);Assert.That(role=="action"?material.ExpectedAction:material.ExpectedTarget,Is.EqualTo(right));
                string Outcome(string outcome,string answer=null)=>loaded.Feedback(material,Respond(PanelMode.LessonAtomic,panelRole,outcome,panelRole==PanelRole.Target?answer:null,panelRole==PanelRole.Action?answer:null));
                Assert.That(Outcome("correct",right),Is.EqualTo("DEMO-feedback-correct"),id);
                Assert.That(Outcome("wrong",wrong),Is.EqualTo("DEMO-feedback-incorrect"),id);
                Assert.That(Outcome("dont_know"),Is.EqualTo("DEMO-feedback-incorrect"),id);
                Assert.That(Outcome("timeout"),Is.EqualTo("DEMO-feedback-timeout"),id);
            }
        }
        [Test,Category("REQ_LES_12"),NUnit.Framework.Property("Requirement","REQ-LES-12"),Category("REQ_LES_10"),NUnit.Framework.Property("Requirement","REQ-LES-10")]
        public void AtomicAndMessageLessonsShowTheSameFeedbackInBothFormats()
        {
            var loaded=Load();var atom=AtomicLesson(loaded,"K-a1","action");var aligned=MessageLesson(loaded,"K-a1-r1");var dictionary=DictionaryFormat(aligned);
            Assert.That(aligned.ExpectedAction,Is.EqualTo("REMOVE_ONE"));Assert.That(aligned.ExpectedTarget,Is.EqualTo("A"));
            foreach(var (outcome,target,action,atomAction) in new (string,string,string,string)[]{("correct","A","REMOVE_ONE","REMOVE_ONE"),("wrong","B","REMOVE_ONE","ADD_ONE"),("wrong","A","ADD_ONE","ADD_ONE"),("dont_know",null,null,null),("timeout",null,null,null)})
            {
                string expected=outcome=="wrong"||outcome=="dont_know"?"incorrect":outcome;
                string atomic=loaded.Feedback(atom,Respond(PanelMode.LessonAtomic,PanelRole.Action,outcome,action:atomAction));
                string structured=loaded.Feedback(aligned,Respond(PanelMode.LessonMessage,PanelRole.Command,outcome,target,action));
                string plain=loaded.Feedback(dictionary,Respond(PanelMode.LessonMessage,PanelRole.Command,outcome,target,action));
                Assert.That(new[]{atomic,structured,plain},Is.All.EqualTo("DEMO-feedback-"+expected),outcome+" "+target+" "+action);
                Assert.That(loaded.FeedbackText(atomic),Is.EqualTo("Synthetic "+expected+" feedback"));
            }
        }
        static string[] LoggedFeedback(SlotItem item,string feedbackId,bool timeout)
        {
            var context=new SlotContext(item,750,null);bool atomic=item.TrialType=="atomic_lesson";var events=new List<LessonEvent>();
            var timeline=new LessonTimeline(context,false,24000,atomic?0:24000,"DEMO-meaning",Hash,atomic?null:Hash,atomic?null:Hash,events.Add);timeline.Start(0);
            double[] offsets=atomic?new[]{0d,6000,14000}:new[]{0d,8000,18000};double respond=750+(timeout?item.ResponseClosesSeconds*1000+5:offsets[1]+100);
            for(double now=5;now<=context.EndMonoMs;now+=5)
            {
                timeline.Tick(now);
                for(int i=0;i<3;i++){if(now==750+offsets[i])timeline.Onset(context.AudioRequestIds[i],now,10,now);if(now==750+offsets[i]+1500)timeline.Completed(context.AudioRequestIds[i],now);}
                if(now==respond)timeline.Response(feedbackId,now,timeout);
            }
            Assert.That(timeline.Ended,Is.True);return events.Where(x=>x.FeedbackContentId!=null).Select(x=>x.Kind+"|"+x.FeedbackContentId).ToArray();
        }
        [Test,Category("REQ_LES_12"),NUnit.Framework.Property("Requirement","REQ-LES-12")]
        public void LoggedFeedbackIdIsTheOutcomeForBothLessonKinds()
        {
            var loaded=Load();var atom=AtomicLesson(loaded,"K-r1","referent");var message=MessageLesson(loaded,"K-a1-r1");
            foreach(var (outcome,id) in new (string,string)[]{("wrong","DEMO-feedback-incorrect"),("timeout","DEMO-feedback-timeout")})
            {
                bool timeout=outcome=="timeout";
                string[] atomic=LoggedFeedback(new SlotItem("DEMO-atom","atomic_lesson","K-r1","referent",null,"teaching",false,20,3,1),loaded.Feedback(atom,Respond(PanelMode.LessonAtomic,PanelRole.Target,outcome,target:"B")),timeout);
                string[] whole=LoggedFeedback(new SlotItem("DEMO-message","message_lesson","K-a1-r1",null,"structured","teaching",false,24,3,1),loaded.Feedback(message,Respond(PanelMode.LessonMessage,PanelRole.Command,outcome,"B","REMOVE_ONE")),timeout);
                Assert.That(atomic,Is.EqualTo(new[]{"retrieval_result|"+id,"display_request|"+id,"display_start|"+id,"display_end|"+id}));Assert.That(whole,Is.EqualTo(atomic));
            }
        }
        [Test,Category("REQ_LES_12"),NUnit.Framework.Property("Requirement","REQ-LES-12")]
        public void RetiredAtomicFeedbackKeyOrSharedIdRefused()
        {
            Assert.That(Load(),Is.Not.Null);
            catalog["feedback"]["atomic"]=new JObject{["id"]="DEMO-feedback-atomic",["text"]="Synthetic atomic feedback"};SealCatalog();Assert.Throws<SessionFault>(()=>Load());
            ((JObject)catalog["feedback"]).Remove("atomic");catalog["feedback"]["timeout"]["id"]="DEMO-feedback-incorrect";SealCatalog();Assert.Throws<SessionFault>(()=>Load());
            ((JObject)catalog["feedback"]).Remove("timeout");SealCatalog();Assert.Throws<SessionFault>(()=>Load());
        }
        [Test,Category("REQ_LES_12"),NUnit.Framework.Property("Requirement","REQ-LES-12")]
        public void FeedbackRefusesAResponseFromAnotherPanelRoleOrMode()
        {
            var loaded=Load();var action=AtomicLesson(loaded,"K-a1","action");var message=MessageLesson(loaded,"K-a1-r1");
            Assert.That(Assert.Throws<SessionFault>(()=>loaded.Feedback(action,Respond(PanelMode.LessonAtomic,PanelRole.Target,"correct",target:"A"))).Code,Is.EqualTo("LESSON_RESPONSE_REFUSED"));
            Assert.That(Assert.Throws<SessionFault>(()=>loaded.Feedback(action,Respond(PanelMode.LessonMessage,PanelRole.Command,"correct","A","REMOVE_ONE"))).Code,Is.EqualTo("LESSON_RESPONSE_REFUSED"));
            Assert.That(Assert.Throws<SessionFault>(()=>loaded.Feedback(message,Respond(PanelMode.LessonAtomic,PanelRole.Action,"correct",action:"REMOVE_ONE"))).Code,Is.EqualTo("LESSON_RESPONSE_REFUSED"));
        }
        sealed class Selection:ITeachingSelections
        {public string PackageSha256{get;set;}public bool OldHashesVerified=>true;public readonly Dictionary<string,int> Counts=new Dictionary<string,int>();public TeachingSelection Get(string atom){Counts[atom]=Counts.TryGetValue(atom,out var n)?n+1:1;return new TeachingSelection("P1",1);}}
        sealed class DictionaryPermit:IPostStudyDictionaryAuthorization
        {internal bool Used;internal string Package,Schedule,Atom;public bool TryConsume(string package,string schedule,string atom){if(Used)return false;Used=true;Package=package;Schedule=schedule;Atom=atom;return true;}}
        [Test]public void ActualProducerBSelectionsAndOneUsePostStudyDictionaryWhenProvisioned()
        {
            string root=Environment.GetEnvironmentVariable("AV_PACKAGE_DEMO_ROOT")??Path.Combine(repository,".local","producer-package-demo");
            if(!Directory.Exists(root))Assert.Ignore("Provision the verified DEMO-only #13 producer artifact; no participant fallback");
            packageRoot=Path.Combine(root,"dyad-demo");var manifest=JObject.Parse(File.ReadAllText(Path.Combine(packageRoot,"manifest.json")));package=PackageLoader.Load(packageRoot,(string)manifest["package_sha256"],true);
            Assert.That(package.Study,Is.EqualTo("B"));Assert.That(package.CombinationsChecked,Is.EqualTo(1536));
            schedule=new VisitSchedule(Hash,package.PackageSha256,"B-C01-M1","W4",true,Array.Empty<ScheduleBlock>());allocationBytes=File.ReadAllBytes(Path.Combine(repository,"schedules","examples","demo-allocation","B","confirmatory-dyads.json"));allocationHash=PcmWave.Hash(allocationBytes);
            catalog["package_sha256"]=package.PackageSha256;SealCatalog();var loaded=Load();var selected=new Selection{PackageSha256=package.PackageSha256};
            var item=new SlotItem("DEMO-B","message_lesson","K-a1-r1",null,"dictionary","teaching",false,24,3,1);var material=loaded.Prepare(item,selected);
            Assert.That(selected.Counts.Values,Is.All.EqualTo(1));Assert.That(material.Wave.PcmSha256,Is.EqualTo(package.CompositeHash(item.ContentId,"P1",1,1)));
            var permit=new DictionaryPermit();var display=loaded.ReadPostStudyDictionaryAtom("K-a1",permit);Assert.That(display.MeaningDisplayId,Is.EqualTo("DEMO-display-K-a1"));Assert.That(permit.Package,Is.EqualTo(package.PackageSha256));Assert.That(permit.Schedule,Is.EqualTo(Hash));Assert.That(permit.Atom,Is.EqualTo("K-a1"));
            Assert.Throws<SessionFault>(()=>loaded.ReadPostStudyDictionaryAtom("K-a1",permit));Assert.Throws<SessionFault>(()=>loaded.ReadPostStudyDictionaryAtom("K-a1-r2",new DictionaryPermit()));Assert.Throws<SessionFault>(()=>loaded.ReadPostStudyDictionaryAtom("K-a1",null));
        }
    }
}
