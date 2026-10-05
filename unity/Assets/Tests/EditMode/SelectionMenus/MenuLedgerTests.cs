using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.SelectionMenus.Tests
{
    public sealed class MenuLedgerTests
    {
        static readonly string Hash=new string('a',64);
        static readonly string[] Keys={"K-a1","K-a2","K-r1","K-r2"};
        readonly DateTimeOffset created=new DateTimeOffset(2026,1,1,0,0,0,TimeSpan.Zero);
        string directory;
        [SetUp]public void Before(){directory=Path.Combine(Path.GetTempPath(),"av-menu-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(directory);}
        [TearDown]public void After(){Directory.Delete(directory,true);}
        static MenuLedgerBinding Binding(string role="active")=>new MenuLedgerBinding(Hash,Hash,Hash,Hash,Hash,Hash,"V2",role,Keys);
        static MenuOption[] Options(string unused)=>Enumerable.Range(1,3).Select(i=>new MenuOption("DEMO-candidate-"+i,SelectionMenuTests.Wave(unused=="profile"?96000:21600,i*100))).ToArray();
        static MenuLedgerVerification Verification()=>new MenuLedgerVerification(Options,(_,index,hash)=>index==1&&hash==Hash,_=>"DEMO-meaning");
        static List<MenuEvent> Events(MenuReplaySequence replay=null,string[] keys=null,double observedDelay=0)
        {
            var result=new List<MenuEvent>();keys??=Keys;double start=750;
            for(int menu=0;menu<keys.Length;menu++)
            {
                bool profile=keys[menu]=="profile";var item=new SlotItem("DEMO-menu-"+menu,profile?"profile_menu":"atom_menu",profile?null:keys[menu],null,"selection","selection",false,profile?60:45,8,1);var ctx=new SlotContext(item,start,null);double clock=start-750;
                var timeline=new MenuTimeline(ctx,Options(keys[menu]),"DEMO-meaning",result.Add,replay?.For(item),()=>clock+observedDelay);var plays=new List<(string id,MenuOption option,double at)>();var heard=new HashSet<string>();var done=new HashSet<string>();
                timeline.PlayRequested+=(_,id,option,at)=>plays.Add((id,option,at));if(replay==null)timeline.SelectionRequested+=(index,unused)=>timeline.ConfirmSelection(index,Hash,start+(profile?45000:32000));timeline.Start(start-750);
                for(double now=start;now<=start+item.SlotSeconds*1000+20;now+=10)
                {clock=now;timeline.Tick(now);foreach(var play in plays.ToArray()){if(now>=play.at&&heard.Add(play.id))timeline.Onset(play.id,play.at,10,now);if(now>=play.at+play.option.Wave.SampleCount/48d&&done.Add(play.id))timeline.Completed(play.id,now);}}
                Assert.That(timeline.Complete,Is.True);start+=item.SlotSeconds*1000+5000;
            }
            return result;
        }
        string Write(IReadOnlyList<MenuEvent> events,bool seal=true,MenuLedgerBinding binding=null)
        {string path=Path.Combine(directory,Guid.NewGuid().ToString("N")+".jsonl");using(var writer=new MenuLedger(path,binding??Binding(),created)){foreach(var row in events)writer.Append(row);if(seal)writer.Seal(Verification());}return path;}
        MenuReplaySequence Load(string path,DateTimeOffset? now=null,MenuLedgerVerification verification=null)=>MenuReplaySequence.Load(path,PcmWave.Hash(File.ReadAllBytes(path)),Binding(),verification??Verification(),now??created.AddHours(1),1000,()=>0);
        [Test]public void SealedIndependentAssetBoundLedgerPreservesAllPlaysSourceIdsAndPauses()
        {
            var events=Events();string path=Write(events);var loaded=Load(path);Assert.That(loaded.SourceSpanMs,Is.EqualTo(195000));
            var item=new SlotItem("DEMO-yoke","atom_menu",Keys[1],null,"selection","selection",false,45,8,1);Assert.That(loaded.MinimumGapBeforeMs(item,46000),Is.EqualTo(5000));
            var replay=loaded.For(item);Assert.That(replay.SourceEvents,Is.EqualTo(events.Where(x=>x.MenuKey==Keys[1]&&x.Kind=="play_request").Select(x=>x.EventId)));Assert.That(replay.OffsetsMs,Is.EqualTo(new[]{5000d,8000,11000,14000,17000,20000,35000,38000}));
            Assert.Throws<SessionFault>(()=>loaded.MinimumGapBeforeMs(item,51001));
        }
        [Test]public void ActiveAndYokedMatchAllFilesCountsChoicesAndSourceEvents()
        {var replay=Load(Write(Events()));var rows=Events(replay);var comparison=replay.CompareYoked(rows,Binding("yoked"),Verification());Assert.That(comparison.Menus,Is.EqualTo(4));Assert.That(comparison.AtomPlays,Is.EqualTo(32));Assert.That(rows.All(x=>x.YokedSourceEventId!=null),Is.True);Assert.That(rows.Any(x=>x.Kind=="choice_revised"),Is.False);
         using var ledger=new MenuLedger(Path.Combine(directory,"yoked.jsonl"),Binding("yoked"),created.AddHours(1));foreach(var row in rows)ledger.Append(row);Assert.That(ledger.SealYoked(replay,Verification()).AudioPlays,Is.EqualTo(32));}
        [Test]public void WrongYokedMeaningBindingOrSourceEventCannotMatch()
        {var replay=Load(Write(Events()));var rows=Events(replay).Select(MenuJson.Event).ToList();rows[0]["yoked_source_event_id"]=Guid.NewGuid().ToString("N");Assert.Throws<SessionFault>(()=>replay.CompareYokedRecords(rows,Binding("yoked"),Verification()));}
        [Test]public void RecordedDisplayOffsetsRemainAuthoritativeInYokedRun()
        {var active=Events(observedDelay:10);var replay=Load(Write(active));var yoked=Events(replay);var compared=replay.CompareYoked(yoked,Binding("yoked"),Verification());Assert.That(compared.AudioPlays,Is.EqualTo(32));Assert.That(yoked.First(x=>x.Kind=="display_changed").MonoMs-yoked.First().SlotStartMonoMs,Is.EqualTo(10));}
        [TestCase(false,2,true)][TestCase(false,19,false)][TestCase(true,2,true)][TestCase(true,19,false)]
        public void ActiveAndYokedOnsetsAreBoundedByTheirOwnUncertainty(bool yoked,double residual,bool accepted)
        {
            var replay=Load(Write(Events()));var rows=Events(yoked?replay:null).Select(MenuJson.Event).ToList();var onset=rows.First(x=>(string)x["kind"]=="onset_authority");onset["expected_mono_ms"]=(double)onset["expected_mono_ms"]+residual;onset["onset_uncertainty_ms"]=2;
            TestDelegate validate=()=>{if(yoked)replay.CompareYokedRecords(rows,Binding("yoked"),Verification());else MenuReplaySequence.ValidateRecords(rows,Binding(),Verification());};if(accepted)Assert.DoesNotThrow(validate);else Assert.Throws<SessionFault>(validate);
        }
        [Test]public void AllThreeVisitPairsPreserve128AtomAnd8ProfilePlaysPerPerson()
        {
            int atom=0,profile=0;foreach(string visit in new[]{"V1","V2","V3"})
            {
                string[] keys=visit=="V1"?new[]{"profile","K-a1","K-a2","K-r1","K-r2","Q-a1","Q-a2","Q-r1","Q-r2"}:visit=="V2"?new[]{"K-a3","K-r3","Q-a3","Q-r3"}:new[]{"K-a4","K-r4","Q-a4","Q-r4"};
                var binding=new MenuLedgerBinding(Hash,Hash,Hash,Hash,Hash,Hash,visit,"active",keys);string path=Write(Events(keys:keys),binding:binding);var replay=MenuReplaySequence.Load(path,PcmWave.Hash(File.ReadAllBytes(path)),binding,Verification(),created.AddHours(1),1000,()=>0);
                var yoked=new MenuLedgerBinding(Hash,Hash,Hash,Hash,Hash,Hash,visit,"yoked",keys);var report=replay.CompareYoked(Events(replay,keys),yoked,Verification());atom+=report.AtomPlays;profile+=report.ProfilePlays;
            }
            Assert.That(atom,Is.EqualTo(128));Assert.That(profile,Is.EqualTo(8));
        }
        [Test]public void ExposureHandoffRetainsRejectedCandidatesAndPauseFacts()
        {var rows=MenuExposureProjection.Derive(Events());Assert.That(rows.Count,Is.EqualTo(32));Assert.That(rows.Count(x=>x.AcceptedOrRejected=="rejected"),Is.EqualTo(16));Assert.That(rows.All(x=>x.ActiveChoiceOrDefault=="default"&&x.PlaybackStatus=="software_completed"),Is.True);Assert.That(rows.Skip(8).All(x=>x.PauseMs==5000),Is.True);}
        [Test]public void PauseBeforeCueNeedsNewSegmentAndNeverPretendsCompleteReplay()
        {
            var item=new SlotItem("DEMO-pause","atom_menu",Keys[0],null,"selection","selection",false,45,8,1);var context=new SlotContext(item,1000,null);var rows=new List<MenuEvent>();var timeline=new MenuTimeline(context,Options(Keys[0]),"DEMO-meaning",rows.Add);
            timeline.Interrupt(0);Assert.That(rows.Single().MatchingDeviationId,Is.EqualTo(rows.Single().EventId));Assert.That(MenuExposureProjection.Derive(rows),Is.Empty);
            string path=Path.Combine(directory,"partial.jsonl");using(var ledger=new MenuLedger(path,Binding(),created)){ledger.Append(rows.Single());Assert.Throws<SessionFault>(()=>ledger.Seal(Verification()));Assert.Throws<SessionFault>(()=>ledger.Append(rows.Single()));}
            Assert.That(File.Exists(path),Is.True);Assert.Throws<SessionFault>(()=>Load(path));Assert.DoesNotThrow(()=>Load(Write(Events())));
        }
        [Test]public void PartialOrExpiredOrFutureOrWrongReceiptLedgerCannotReplay()
        {
            var events=Events();Assert.Throws<SessionFault>(()=>Load(Write(events,false)));var path=Write(events);
            Assert.Throws<SessionFault>(()=>Load(path,created.AddHours(24).AddMilliseconds(1)));Assert.Throws<SessionFault>(()=>Load(path,created.AddMilliseconds(-1)));
            Assert.Throws<SessionFault>(()=>Load(path,verification:new MenuLedgerVerification(Options,(_,__,___)=>false,_=>"DEMO-meaning")));
        }
        [Test]public void WrongIndependentCandidateBytesCannotBeAuthorizedByValidChain()
        {var path=Write(Events());Assert.Throws<SessionFault>(()=>Load(path,verification:new MenuLedgerVerification(_=>Enumerable.Range(1,3).Select(i=>new MenuOption("DEMO-candidate-"+i,SelectionMenuTests.Wave(21600,i*200))).ToArray(),(_,__,___)=>true,_=>"DEMO-meaning")));}
        [TestCase("missing_completion")][TestCase("reused_audio")][TestCase("wrong_choice")][TestCase("early_highlight")][TestCase("onset_late")][TestCase("changed_pcm")][TestCase("gap_compression")]
        public void SelfConsistentStorageCannotHideInvalidPresentationEvidence(string mutation)
        {
            var records=Events().Select(MenuJson.Event).ToList();var play=records.First(x=>(string)x["kind"]=="play_request");
            if(mutation=="missing_completion")records.Remove(records.First(x=>(string)x["kind"]=="play_complete"));
            if(mutation=="reused_audio"){string id=(string)play["audio_request_id"];foreach(var row in records.Where(x=>(string)x["menu_key"]==Keys[1]&&x["presentation_index"].Type==JTokenType.Integer&&(int)x["presentation_index"]==1))row["audio_request_id"]=id;}
            if(mutation=="wrong_choice")records.First(x=>(string)x["kind"]=="choice_final")["selected_index"]=2;
            if(mutation=="early_highlight")records.First(x=>(string)x["kind"]=="display_changed"&&(string)x["phase"]=="Choice")["selected_index"]=1;
            if(mutation=="onset_late")records.First(x=>(string)x["kind"]=="onset_authority")["expected_mono_ms"]=(double)play["expected_mono_ms"]+21;
            if(mutation=="changed_pcm")play["pcm_sha256"]=new string('b',64);
            if(mutation=="gap_compression")foreach(var row in records.Where(x=>(string)x["menu_key"]==Keys[1]))row["slot_start_mono_ms"]=1000;
            Assert.Throws<SessionFault>(()=>MenuReplaySequence.ValidateRecords(records,Binding(),Verification()));
        }
        [Test]public void EnvelopeByteTamperAndNoncanonicalRepresentationAreRejected()
        {
            var path=Write(Events());string expected=PcmWave.Hash(File.ReadAllBytes(path));File.AppendAllText(path," ");
            Assert.Throws<SessionFault>(()=>MenuReplaySequence.Load(path,expected,Binding(),Verification(),created,1000,()=>0));
            Assert.Throws<SessionFault>(()=>Load(path));
        }
        [Test]public void SchemaRejectsCoercionAndExtraPrivateField()
        {
            var record=MenuJson.Event(Events().First());record["attempt_id"]=123;Assert.Throws<SessionFault>(()=>MenuJson.CheckEvent(record));
            record=MenuJson.Event(Events().First());record["answer"]="not allowed";Assert.Throws<SessionFault>(()=>MenuJson.CheckEvent(record));
        }
    }
}
