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
        static MenuOption[] Options(string unused)=>Enumerable.Range(1,3).Select(i=>new MenuOption("DEMO-candidate-"+i,SelectionMenuTests.Wave(21600,i*100))).ToArray();
        static MenuLedgerVerification Verification()=>new MenuLedgerVerification(Options,(_,index,hash)=>index==1&&hash==Hash,_=>"DEMO-meaning");
        static List<MenuEvent> Events()
        {
            var result=new List<MenuEvent>();
            for(int menu=0;menu<4;menu++)
            {
                double start=750+menu*50000;var item=new SlotItem("DEMO-menu-"+menu,"atom_menu",Keys[menu],null,"selection","selection",false,45,8,1);var ctx=new SlotContext(item,start,null);
                var timeline=new MenuTimeline(ctx,Options(Keys[menu]),"DEMO-meaning",result.Add);var plays=new List<(string id,MenuOption option,double at)>();var heard=new HashSet<string>();var done=new HashSet<string>();
                timeline.PlayRequested+=(_,id,option,at)=>plays.Add((id,option,at));timeline.SelectionRequested+=(index,unused)=>timeline.ConfirmSelection(index,Hash,start+32000);timeline.Start(start-750);
                for(double now=start;now<=start+45000;now+=50)
                {timeline.Tick(now);foreach(var play in plays.ToArray()){if(now>=play.at&&heard.Add(play.id))timeline.Onset(play.id,play.at,10,now);if(now>=play.at+play.option.Wave.SampleCount/48d&&done.Add(play.id))timeline.Completed(play.id,now);}}
                Assert.That(timeline.Complete,Is.True);
            }
            return result;
        }
        string Write(IReadOnlyList<MenuEvent> events,bool seal=true)
        {string path=Path.Combine(directory,Guid.NewGuid().ToString("N")+".jsonl");using(var writer=new MenuLedger(path,Binding(),created)){foreach(var row in events)writer.Append(row);if(seal)writer.Seal(Verification());}return path;}
        MenuReplaySequence Load(string path,DateTimeOffset? now=null,MenuLedgerVerification verification=null)=>MenuReplaySequence.Load(path,PcmWave.Hash(File.ReadAllBytes(path)),Binding(),verification??Verification(),now??created.AddHours(1),1000,()=>0);
        [Test]public void SealedIndependentAssetBoundLedgerPreservesAllPlaysSourceIdsAndPauses()
        {
            var events=Events();string path=Write(events);var loaded=Load(path);Assert.That(loaded.SourceSpanMs,Is.EqualTo(195000));
            var item=new SlotItem("DEMO-yoke","atom_menu",Keys[1],null,"selection","selection",false,45,8,1);Assert.That(loaded.MinimumGapBeforeMs(item,46000),Is.EqualTo(5000));
            var replay=loaded.For(item);Assert.That(replay.SourceEvents,Is.EqualTo(events.Where(x=>x.MenuKey==Keys[1]&&x.Kind=="play_request").Select(x=>x.EventId)));Assert.That(replay.OffsetsMs,Is.EqualTo(new[]{5000d,8000,11000,14000,17000,20000,35000,38000}));
            Assert.Throws<SessionFault>(()=>loaded.MinimumGapBeforeMs(item,51001));
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
