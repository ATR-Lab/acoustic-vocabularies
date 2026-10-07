using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using AcousticVocab.ResponsePanel;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.DataLogging.Tests
{
    // Hardware ports use an explicit controlled clock. Package loading, engine,
    // panel state, adapters, journal verification and export are production code.
    // No acoustic callback is invented: every request remains uncertain/consumed.
    public sealed class CommandIntegrityTests
    {
        static T New<T>(params object[] args)=>(T)Activator.CreateInstance(typeof(T),BindingFlags.Instance|BindingFlags.NonPublic,null,args,null);
        sealed class Clock : ISessionClock { public double Time; public double NowMs=>Time; }
        sealed class Case
        {
            internal string Id,Message,Kind,Action,Target;internal bool Heldout;
        }
        sealed class Factory : ISlotContentFactory,ISessionContentPump,IDisposable
        {
            readonly LoadedAudioPackage package;readonly Dictionary<string,Case> cases;readonly Clock clock;
            readonly DataJournal journal;readonly AudioDataAdapter audio;readonly PanelDataAdapter panel;
            readonly Dictionary<string,AudioRequestContext> bindings=new Dictionary<string,AudioRequestContext>();
            readonly GameObject host;internal FixedSlotEngine Engine;Content current;
            internal Factory(LoadedAudioPackage package,IEnumerable<Case> cases,Clock clock,DataJournal journal)
            {
                this.package=package;this.cases=cases.ToDictionary(c=>c.Id);this.clock=clock;this.journal=journal;
                host=new GameObject("DEMO integrity inactive adapter host");host.SetActive(false);
                audio=new AudioDataAdapter(journal,host.AddComponent<AudioPlayer>(),e=>bindings[e.AudioId],false);
                panel=new PanelDataAdapter(journal,host.AddComponent<ResponsePanelController>(),id=>new EventContext(id,id));
            }
            public ISlotContent Create(SlotItem item)=>current=new Content(this,cases[item.TrialId]);
            public void Pump()=>current?.State?.Tick();
            public void Dispose(){panel.Dispose();audio.Dispose();UnityEngine.Object.DestroyImmediate(host);}
            sealed class Content : ISlotContent
            {
                readonly Factory owner;readonly Case item;internal ResponseState State;
                internal Content(Factory owner,Case item){this.owner=owner;this.item=item;}
                public SlotReadiness Readiness=>new SlotReadiness(true,true,true,true,true,true,true,true);
                public bool ResetComplete{get;private set;}
                public void Prepare(SlotContext context){}
                public void RequestCue(SlotContext context,INovelSlotAuthorization permit)
                {
                    // The real engine alone supplies each one-use novel permission.
                    var wave=item.Heldout?owner.package.ComposeApprovedNovel(item.Message,permit):owner.package.ReadTrainedMessage(item.Message);
                    string id=context.AudioRequestIds.Single();
                    owner.bindings.Add(id,new AudioRequestContext(new EventContext(context.OpportunityId,item.Id,id),id,wave.FileSha256));
                    var timing=New<AudioScheduleTiming>(owner.clock.Time/1000,context.OnsetMonoMs/1000,context.OnsetMonoMs/1000,(double?)null,(double?)null,(double?)null);
                    owner.audio.Record(New<AudioPlaybackEvent>("AUDIO_REQUESTED",id,wave.PcmSha256,wave.ActionPcmSha256,wave.ReferentPcmSha256,timing,owner.clock.Time/1000,0L,0L,(double?)null));
                }
                public void OpenResponse(SlotContext context)
                {
                    State=new ResponseState(()=>owner.clock.Time,owner.panel.Process);
                    State.Responded+=r=>{owner.panel.Response(r);owner.Engine.RecordResponse(r.Code==ResponseCode.Commit?"commit":r.Code==ResponseCode.DontKnow?"dont_know":"timeout");};
                    State.Open(new PanelRequest(item.Id,PanelMode.FullMessage,PanelRole.Command,context.OnsetMonoMs));
                    Assert.That(State.CanCommit,Is.False);
                    if(item.Kind=="timeout")return;
                    if(item.Kind=="dont_know"){State.DontKnow();return;}
                    string action=item.Kind=="wrong_action"?PublicCommands.Actions.First(a=>PublicCommands.Legal(item.Target,a)&&a!=item.Action):item.Action;
                    string target=item.Kind=="wrong_target"?PublicCommands.Targets.First(t=>PublicCommands.Legal(t,item.Action)&&t!=item.Target):item.Target;
                    Assert.That(State.SelectTarget(target),Is.True);Assert.That(State.SelectAction(action),Is.True);Assert.That(State.Commit(),Is.True);
                }
                public void CloseResponse(SlotContext context){State?.Tick();}
                public void RequestReset(SlotContext context){ResetComplete=true;}
                public void Interrupt(string code){State?.Abort();}
            }
        }
        [TestCase(0)][TestCase(1)][TestCase(2)]
        public void All32ProducerTuplesSurviveEnginePanelJournalAndExport(int permutationIndex)
        {
            string index=Environment.GetEnvironmentVariable("AV_INTEGRITY_FIXTURES");
            if(string.IsNullOrEmpty(index))Assert.Ignore("Run tools/prepare_integrity_fixtures.py and supply independently pinned AV_INTEGRITY_FIXTURES");
            byte[] indexBytes=File.ReadAllBytes(index);
            Assert.That(PcmWave.Hash(indexBytes),Is.EqualTo(Environment.GetEnvironmentVariable("AV_INTEGRITY_FIXTURES_SHA256")));
            var fixtures=JObject.Parse(Encoding.UTF8.GetString(indexBytes));Assert.That((string)fixtures["scope"],Is.EqualTo("DEMO_INTEGRITY"));
            var fixture=fixtures["packages"][permutationIndex];string root=Path.GetDirectoryName(index),folder=(string)fixture["directory"];
            Assert.That(folder,Does.Match(@"\Apackage-[0-2]\z"));string packagePath=Path.Combine(root,folder);
            var package=PackageLoader.Load(packagePath,(string)fixture["package_sha256"],true);Assert.That(package.Demo,Is.True);
            var answers=JObject.Parse(File.ReadAllText(Path.Combine(packagePath,"answers.json")));
            var cases=new List<Case>();var plan=new JArray();int ordinal=0;
            foreach(var row in (JArray)answers["messages"])
                foreach(string kind in new[]{"correct","wrong_action","wrong_target","dont_know","timeout"})
                {
                    var c=new Case{Id="DEMO-case-"+(ordinal++),Message=(string)row["message_id"],Kind=kind,Action=(string)row["semantic_action"],Target=(string)row["semantic_referent"],Heldout=(string)row["status"]=="heldout"};
                    cases.Add(c);plan.Add(new JObject{["opportunity_id"]=c.Id,["message_id"]=c.Message,["case"]=kind});
                    if(c.Heldout)Assert.Throws<NovelSlotException>(()=>package.ReadTrainedMessage(c.Message));
                }
            Assert.That(cases.Count,Is.EqualTo(160));Assert.That(cases.Select(c=>c.Action+"/"+c.Target).Distinct().Count(),Is.EqualTo(32));
            string output=Environment.GetEnvironmentVariable("AV_INTEGRITY_RESULTS");Assert.That(output,Is.Not.Null.And.Not.Empty);
            string run=Path.Combine(output,folder);Assert.That(Directory.Exists(run),Is.False,"Use a fresh output root");Directory.CreateDirectory(run);
            string raw=Path.Combine(run,"raw");var clock=new Clock();
            var identity=new DataIdentity(Guid.NewGuid().ToString("N"),"DEMO-INTEGRITY","DEMO","DEMO-STATION","DEMO-protocol",new string('a',64));
            var slots=cases.Select(c=>New<SlotItem>(c.Id,c.Heldout?"novel":"trained",c.Message,null,null,"protected",c.Heldout,14,1,1)).ToArray();
            var schedule=New<VisitSchedule>(PcmWave.Hash(Encoding.UTF8.GetBytes(plan.ToString())),package.PackageSha256,"DEMO-INTEGRITY","DEMO",true,new[]{New<ScheduleBlock>("DEMO-32-tuples",slots)},"A","DEMO",null);
            using(var journal=new DataJournal(raw,identity,Guid.NewGuid().ToString("N"),()=>clock.Time))
            using(var factory=new Factory(package,cases,clock,journal))
            {
                var engine=new FixedSlotEngine(schedule,clock,new SessionDataJournal(journal),factory);factory.Engine=engine;
                foreach(var c in cases)journal.Append(DataObservations.Opportunity(c.Id,"primary","DEMO-masked",schedule.Sha256));
                engine.ConfirmResume();engine.Tick();
                while(engine.Status==SessionState.Running&&clock.Time<2250000){clock.Time+=50;engine.Tick();}
                Assert.That(engine.Status,Is.EqualTo(SessionState.Complete));
            }
            var snapshot=DataJournal.Verify(raw,identity);var tables=DataDeriver.Derive(snapshot,identity);
            Assert.That(tables.Trials.Count,Is.EqualTo(160));Assert.That(tables.Exposures.Count,Is.EqualTo(160));
            Assert.That(tables.Trials.All(r=>r["interrupted"]=="false"),Is.True);
            Assert.That(tables.Exposures.All(r=>r["callback_observed"]=="false"&&r["audible_status"]=="uncertain"&&r["exposure_consumed"]=="true"),Is.True);
            var bundle=ExportBundle.Create(raw,Path.Combine(run,"export"),identity,ExportHeaders.Provisional());
            var report=new JObject{["scope"]="controlled_clock_real_modules_no_acoustic_evidence",["package_sha256"]=package.PackageSha256,["export_sha256"]=bundle.ManifestSha256,["cases"]=plan};
            byte[] reportBytes=Encoding.UTF8.GetBytes(report.ToString()+"\n");
            File.WriteAllBytes(Path.Combine(run,"mapping.local.json"),reportBytes);
            File.WriteAllText(Path.Combine(run,"mapping.local.json.sha256"),PcmWave.Hash(reportBytes)+"\n",new UTF8Encoding(false));
        }
    }
}
