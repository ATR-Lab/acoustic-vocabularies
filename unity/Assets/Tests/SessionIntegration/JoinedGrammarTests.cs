using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using AcousticVocab.DataLogging;
using AcousticVocab.OperatorConsole;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using AcousticVocab.Teaching;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
namespace AcousticVocab.SessionIntegration.Tests
{
    // Synthetic callback observations test ordering only, never acoustic evidence.
    public sealed class JoinedGrammarTests
    {
        static T New<T>(params object[] args)=>(T)Activator.CreateInstance(typeof(T),BindingFlags.Instance|BindingFlags.NonPublic,null,args,null);
        static readonly string Hash=new string('a',64),Registry=new string('b',64),Method=new string('c',64),Explanation=new string('9',64);
        static JObject Review()=>new JObject{["version"]=1,["approved"]=true,["methodology_sha256"]=Method,["registry_sha256"]=Registry,["explanation_sha256"]=Explanation,["boundary"]="before_first_teaching_block",["repeat_policy"]="once_per_visit_no_partial_replay",["scheduling_lead_ms"]=750,["completion_authority"]="software_delivery_only",["labels"]=new JObject{["ready"]="READY",["action"]="Action",["target"]="Target"}};
        static JoinedGrammarReview ReadReview(JObject p){byte[] bytes=Encoding.UTF8.GetBytes(p.ToString());return JoinedGrammarReview.Load(bytes,PcmWave.Hash(bytes),Registry,Method);}
        sealed class Fixture:IDisposable
        {
            internal readonly string Path=System.IO.Path.Combine(Environment.CurrentDirectory,".local","grammar-join-tests",Guid.NewGuid().ToString("N"));
            internal readonly DataIdentity Identity=new DataIdentity(new string('1',32),"synthetic-person","DEMO","station-01","engineering",Hash);
            internal readonly GrammarAssets Assets=New<GrammarAssets>(null,New<PcmWave>(new byte[192002],Explanation,null,null),New<PcmWave>(new byte[30720],new string('d',64),null,null),New<PcmWave>(new byte[27648],new string('e',64),null,null),Registry);
            internal readonly JoinedGrammarReview Review=ReadReview(JoinedGrammarTests.Review());
            internal DataJournal Journal;internal JoinedGrammarStage Stage;internal double Time=1000;
            internal Fixture(){Journal=new DataJournal(Path,Identity,new string('2',32),()=>Time);Stage=new JoinedGrammarStage(Journal,Assets,Review,Hash,()=>Time);}
            internal OperatorRequest Command()=>OperatorRequest.Parse(Encoding.UTF8.GetBytes(new JObject{["version"]=1,["session_nonce"]=new string('3',32),["request_id"]=Guid.NewGuid().ToString("N"),["sequence"]=1,["command"]="start",["run_sheet_manifest_sha256"]=Hash,["schedule_sha256"]=Hash}.ToString(Newtonsoft.Json.Formatting.None)));
            internal void Flow(string kind,string id)=>Stage.Observe(new JObject{["kind"]=kind,["audio_request_id"]=id,["host_mono_ms"]=Time,["registry_sha256"]=Registry,["calibration_only"]=false});
            internal void Audio(string id,PcmWave wave,string code,long callbacks=1,long? samples=null)
            {
                var timing=New<AudioScheduleTiming>(Time/1000,Time/1000+.75,5.0,(double?)(Time/1000+.75),(double?)5,(double?)0);
                Stage.Audio(New<AudioPlaybackEvent>(code,id,wave.PcmSha256,null,null,timing,Time/1000,samples??wave.SampleCount,callbacks,callbacks>0?(double?)5:null));
            }
            internal void Play(PcmWave wave,string id=null)
            {id??=Guid.NewGuid().ToString("N");Time+=1000;Flow("grammar_request",id);Audio(id,wave,"AUDIO_REQUESTED",0,0);Time+=2000;Audio(id,wave,"AUDIO_PLAYBACK_COMPLETED");Flow("grammar_complete",id);}
            public void Dispose()=>Journal.Dispose();
        }
        [Test]public void AllOrderedPlaysAndDurableFinishRecoverWithoutTrialOrExposureRows()
        {
            using var f=new Fixture();f.Stage.Begin(f.Command());f.Play(f.Assets.Explanation);Assert.That(f.Stage.Complete,Is.False);f.Play(f.Assets.Ready);Assert.That(f.Stage.Complete,Is.False);f.Play(f.Assets.Clicks);Assert.That(f.Stage.Complete,Is.False);f.Stage.Finish();Assert.That(f.Stage.Complete,Is.True);
            f.Journal.Dispose();using var recovered=new DataJournal(f.Path,f.Identity,new string('4',32),()=>0);
            var stage=new JoinedGrammarStage(recovered,f.Assets,f.Review,Hash,()=>0);Assert.That(stage.Complete,Is.True);int before=recovered.Records.Count;stage.Finish();Assert.That(recovered.Records.Count,Is.EqualTo(before));
            Assert.Throws<SessionFault>(()=>stage.Begin(f.Command()));
            recovered.Dispose();var snapshot=DataJournal.Verify(f.Path,f.Identity);var tables=DataDeriver.Derive(snapshot,f.Identity);Assert.That(tables.Trials,Is.Empty);Assert.That(tables.Exposures,Is.Empty);
            var bundle=ExportBundle.Create(f.Path,f.Path+"-export",f.Identity,ExportHeaders.Provisional());bundle.VerifyAll();Assert.That(bundle.HeadersQualified,Is.False);
        }
        [TestCase(0)][TestCase(1)][TestCase(2)][TestCase(3)]public void AnyUnfinishedHistoryRefusesAutomaticReplayOnRecovery(int completed)
        {
            using var f=new Fixture();f.Stage.Begin(f.Command());if(completed>0)f.Play(f.Assets.Explanation);if(completed>1)f.Play(f.Assets.Ready);if(completed>2)f.Play(f.Assets.Clicks);f.Journal.Dispose();
            using var recovered=new DataJournal(f.Path,f.Identity,new string('4',32),()=>0);Assert.That(Assert.Throws<SessionFault>(()=>new JoinedGrammarStage(recovered,f.Assets,f.Review,Hash,()=>0)).Code,Is.EqualTo("JOIN_GRAMMAR_RECOVERY_REQUIRED"));
        }
        [TestCase(0,0)][TestCase(1,1)]public void MissingOrIncompleteCallbackCoverageCannotComplete(int callbacks,int missingSamples)
        {
            using var f=new Fixture();f.Stage.Begin(f.Command());string id=Guid.NewGuid().ToString("N");f.Flow("grammar_request",id);f.Audio(id,f.Assets.Explanation,"AUDIO_REQUESTED",0,0);
            Assert.Throws<SessionFault>(()=>f.Audio(id,f.Assets.Explanation,"AUDIO_PLAYBACK_COMPLETED",callbacks,f.Assets.Explanation.SampleCount-missingSamples));Assert.That(f.Stage.Complete,Is.False);
        }
        [Test]public void UnknownFieldsWrongWavePrematureFinishAndChangedReviewCannotGrantCompletion()
        {
            using var f=new Fixture();f.Stage.Begin(f.Command());Assert.Throws<SessionFault>(()=>f.Stage.Finish());
            using var wrong=new Fixture();wrong.Stage.Begin(wrong.Command());string id=Guid.NewGuid().ToString("N");wrong.Flow("grammar_request",id);Assert.Throws<SessionFault>(()=>wrong.Audio(id,wrong.Assets.Clicks,"AUDIO_REQUESTED"));
            using var shape=new Fixture();shape.Stage.Begin(shape.Command());var row=shape.Journal.Records.Single().Payload;row["trusted"]=true;Assert.Throws<DataFault>(()=>new EventDraft("grammar_stage",null,row));
            shape.Journal.Dispose();using var recovered=new DataJournal(shape.Path,shape.Identity,new string('4',32),()=>0);Assert.Throws<SessionFault>(()=>new JoinedGrammarStage(recovered,shape.Assets,shape.Review,new string('f',64),()=>0));
        }
        [Test]public void FailedDurableStartCannotBeRetriedOrReportStarted()
        {
            using var f=new Fixture();typeof(DataJournal).GetField("BeforeDurableFlush",BindingFlags.NonPublic|BindingFlags.Instance).SetValue(f.Journal,(Action)(()=>throw new IOException("synthetic")));
            Assert.Throws<DataFault>(()=>f.Stage.Begin(f.Command()));Assert.That(f.Stage.Started,Is.False);Assert.That(f.Stage.Complete,Is.False);Assert.Throws<SessionFault>(()=>f.Stage.Begin(f.Command()));
        }
        [Test]public void ExplicitInterruptionRemainsConsumedAndCannotReplay()
        {
            using var f=new Fixture();f.Stage.Begin(f.Command());f.Flow("grammar_interrupted",null);Assert.Throws<SessionFault>(()=>f.Stage.Begin(f.Command()));Assert.That(f.Stage.Complete,Is.False);
        }
        [Test]public void PriorConsumedTeachingCannotMigrateWithoutFinishedGrammarHistory()
        {
            using var f=new Fixture();
            SessionRecord Record(string id,bool consumed)=>New<SessionRecord>("state_after",new string('5',32),Hash,id,null,0,0,0.0,(double?)0,(ItemState?)ItemState.CueRequested,AudibleStatus.Uncertain,consumed,false,true,null,null,null,id,Array.Empty<string>());
            Assert.DoesNotThrow(()=>f.Stage.RequireBeforeTeachingHistory(new[]{Record("lesson",false),Record("assessment",true)},new[]{"lesson"}));
            Assert.That(Assert.Throws<SessionFault>(()=>f.Stage.RequireBeforeTeachingHistory(new[]{Record("lesson",true)},new[]{"lesson"})).Code,Is.EqualTo("JOIN_GRAMMAR_HISTORY_MISSING"));
            f.Stage.Begin(f.Command());f.Play(f.Assets.Explanation);f.Play(f.Assets.Ready);f.Play(f.Assets.Clicks);f.Stage.Finish();
            Assert.DoesNotThrow(()=>f.Stage.RequireBeforeTeachingHistory(new[]{Record("lesson",true)},new[]{"lesson"}));
        }
        [TestCase("approved")][TestCase("methodology_sha256")][TestCase("registry_sha256")][TestCase("explanation_sha256")][TestCase("scheduling_lead_ms")][TestCase("completion_authority")]
        public void ReviewMustAuthorizeExactlyTheExistingReservedSequence(string field)
        {var row=Review();row[field]=field=="approved"?(JToken)false:field=="scheduling_lead_ms"?(JToken)751:(JToken)"different";Assert.Throws<SessionFault>(()=>ReadReview(row));}
        [Test]public void ReviewCannotIncludeUnrecognizedAuthorityOrNewLabels()
        {var row=Review();row["physical_pass"]=true;Assert.Throws<SessionFault>(()=>ReadReview(row));row=Review();row["labels"]["ready"]="START";Assert.Throws<SessionFault>(()=>ReadReview(row));}

        [Test, Category("REQ_GRAM_02"), NUnit.Framework.Property("Requirement", "REQ-GRAM-02")]
        public void OnlyTheReviewedExplanationRecordingCanBeginAndTheReviewCannotScopeIt()
        {
            using var f=new Fixture();Assert.That(f.Review.ExplanationSha256,Is.EqualTo(Explanation));Assert.That(f.Assets.Explanation.FileSha256,Is.EqualTo(f.Review.ExplanationSha256));
            var other=ReadReview(new JObject(Review()){["explanation_sha256"]=new string('8',64)});
            Assert.That(Assert.Throws<SessionFault>(()=>new JoinedGrammarStage(f.Journal,f.Assets,other,Hash,()=>0)).Code,Is.EqualTo("GRAMMAR_EXPLANATION_HASH"));
            var reserved=New<GrammarAssets>(null,null,f.Assets.Ready,f.Assets.Clicks,Registry);
            Assert.That(Assert.Throws<SessionFault>(()=>new JoinedGrammarStage(f.Journal,reserved,f.Review,Hash,()=>0)).Code,Is.EqualTo("GRAMMAR_EXPLANATION_MISSING"));
            var missing=Review();missing.Remove("explanation_sha256");Assert.That(Assert.Throws<SessionFault>(()=>ReadReview(missing)).Code,Is.EqualTo("JOIN_GRAMMAR_REVIEW"));
            // One recording for everyone: the review cannot name a study, role or lesson format.
            foreach(var (field,value) in new[]{("study","A"),("role","yoked"),("lesson_format","structured")})
            {var scoped=Review();scoped[field]=value;Assert.That(Assert.Throws<SessionFault>(()=>ReadReview(scoped)).Code,Is.EqualTo("JOIN_GRAMMAR_REVIEW"),field);}
        }

        [Test, Category("REQ_GRAM_03"), NUnit.Framework.Property("Requirement", "REQ-GRAM-03")]
        public void ExplanationRequestOnsetAndCompletionAreJournaledWithItsHashBeforeTeachingCanStart()
        {
            using var f=new Fixture();f.Stage.Begin(f.Command());string id=Guid.NewGuid().ToString("N");var wave=f.Assets.Explanation;
            f.Time+=1000;f.Flow("grammar_request",id);f.Audio(id,wave,"AUDIO_REQUESTED",0,0);f.Time+=750;f.Audio(id,wave,"AUDIO_ONSET_ESTIMATED");
            f.Time+=2000;f.Audio(id,wave,"AUDIO_PLAYBACK_COMPLETED");f.Flow("grammar_complete",id);
            var rows=f.Journal.Records.Where(r=>r.Kind=="grammar_stage").Select(r=>r.Payload).Where(p=>(string)p["phase"]=="explanation").ToArray();
            Assert.That(rows.Select(p=>(string)p["kind"]),Is.EqualTo(new[]{"request","audio","audio","audio","completed"}));
            var audio=rows.Where(p=>(string)p["kind"]=="audio").Select(p=>(JObject)p["audio"]).ToArray();
            Assert.That(audio.Select(a=>(string)a["code"]),Is.EqualTo(new[]{"AUDIO_REQUESTED","AUDIO_ONSET_ESTIMATED","AUDIO_PLAYBACK_COMPLETED"}));
            Assert.That(audio.Select(a=>(string)a["waveform_sha256"]).Distinct(),Is.EqualTo(new[]{Explanation}));Assert.That(audio.Select(a=>(string)a["pcm_sha256"]).Distinct(),Is.EqualTo(new[]{wave.PcmSha256}));
            Assert.That(audio[1]["onset_estimate_mono_ms"].Type,Is.Not.EqualTo(JTokenType.Null));Assert.That((long)audio[2]["delivered_samples"],Is.EqualTo(wave.SampleCount));
            Assert.That(rows.All(p=>(string)p["review_sha256"]==f.Review.Sha256&&(string)p["audio_request_id"]==id),Is.True);
            // The explanation alone never completes the stage, so the teaching gate stays closed.
            Assert.That(f.Stage.Complete,Is.False);Assert.Throws<SessionFault>(()=>f.Stage.Finish());
        }

        [Test, Category("REQ_GRAM_03"), NUnit.Framework.Property("Requirement", "REQ-GRAM-03")]
        public void ReadyWithoutAPriorExplanationIsRefusedSoTheVisitCannotReachLessons()
        {
            using var f=new Fixture();f.Stage.Begin(f.Command());var started=f.Journal.Records.Single().Payload;
            var ready=new JObject(started){["kind"]="request",["phase"]="ready",["audio_request_id"]=Guid.NewGuid().ToString("N"),["operator_command"]=null};
            f.Journal.Append(new EventDraft("grammar_stage",null,ready));f.Journal.Dispose();
            using var recovered=new DataJournal(f.Path,f.Identity,new string('4',32),()=>0);
            Assert.That(Assert.Throws<SessionFault>(()=>new JoinedGrammarStage(recovered,f.Assets,f.Review,Hash,()=>0)).Code,Is.EqualTo("GRAMMAR_EXPLANATION_MISSING"));
        }
    }
}
