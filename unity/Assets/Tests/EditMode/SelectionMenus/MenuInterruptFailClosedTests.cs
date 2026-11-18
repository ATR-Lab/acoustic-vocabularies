using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using AcousticVocab.SessionEngine;
using AcousticVocab.SessionIntegration;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.SelectionMenus.Tests
{
    // Decided rule for an active selection menu interrupted mid-play: no replay.
    // The visit's menu ledger stays fail-closed, the partial segment is kept as
    // recorded with its matching deviation, and the assigned partner has no
    // sealed sequence to replay, so neither member hears a reconstructed menu.
    public sealed class MenuInterruptFailClosedTests
    {
        const string ThirdPlay="third_play",ChoiceWindow="choice_window";
        const double MenuMs=45000,PauseMs=5000;
        static readonly string Hash=new string('a',64);
        static readonly string[] Keys={"K-a1","K-a2","K-r1","K-r2"};
        readonly DateTimeOffset created=new DateTimeOffset(2026,1,1,0,0,0,TimeSpan.Zero);
        string directory;
        [SetUp]public void Before(){directory=Path.Combine(Path.GetTempPath(),"av-menu-interrupt-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(directory);}
        [TearDown]public void After(){Directory.Delete(directory,true);}
        static MenuLedgerBinding Binding(string role="active")=>new MenuLedgerBinding(Hash,Hash,Hash,Hash,Hash,Hash,"V2",role,Keys);
        static MenuOption[] Options()=>Enumerable.Range(1,3).Select(i=>new MenuOption("DEMO-candidate-"+i,SelectionMenuTests.Wave(21600,i*100))).ToArray();
        static MenuLedgerVerification Verification()=>new MenuLedgerVerification(_=>Options(),(_,index,hash)=>index==1&&hash==Hash,_=>"DEMO-meaning");
        static double Start(int slot)=>750+slot*(MenuMs+PauseMs);
        MenuReplaySequence Load(string path,double anchor=1000)=>MenuReplaySequence.Load(path,PcmWave.Hash(File.ReadAllBytes(path)),Binding(),Verification(),created.AddHours(1),anchor,()=>0);

        // Drives one active atom menu in 10 ms steps with prompt onsets and completions.
        sealed class Menu
        {
            internal readonly MenuTimeline Timeline;internal readonly List<string> Plays=new List<string>();
            readonly List<(string id,double at,double end)> pending=new List<(string,double,double)>();
            readonly HashSet<string> heard=new HashSet<string>(),done=new HashSet<string>();
            readonly double start;double clock;
            internal Menu(string trialId,string key,double start,Action<MenuEvent> sink)
            {
                this.start=start;var item=new SlotItem(trialId,"atom_menu",key,null,"selection","selection",false,45,8,1);
                Timeline=new MenuTimeline(new SlotContext(item,start,null),Options(),"DEMO-meaning",sink);
                Timeline.PlayRequested+=(_,id,option,at)=>{Plays.Add(id);pending.Add((id,at,at+option.Wave.SampleCount/48d));};
                Timeline.SelectionRequested+=(index,_)=>Timeline.ConfirmSelection(index,Hash,clock);
            }
            internal void Begin(){clock=start-750;Timeline.Start(clock);}
            internal void RunTo(double elapsedMs)
            {
                while(clock+10<=start+elapsedMs)
                {
                    clock+=10;Timeline.Tick(clock);if(Timeline.Interrupted)continue;
                    foreach(var play in pending.ToArray())
                    {
                        if(clock>=play.at&&heard.Add(play.id))Timeline.Onset(play.id,play.at,10,clock);
                        if(clock>=play.end&&done.Add(play.id))Timeline.Completed(play.id,clock);
                    }
                }
            }
            internal void Choose(int index)=>Timeline.Choose(index,clock);
            internal void Interrupt()=>Timeline.Interrupt(clock);
        }

        // Completes the first menu of a V2 visit, then interrupts the second at the
        // given point. `whileOpen` runs before the visit ledger is closed.
        List<MenuEvent> InterruptedVisit(string point,string path,Action<MenuLedger,Action<MenuEvent>,Menu> whileOpen=null)
        {
            var rows=new List<MenuEvent>();
            using var ledger=new MenuLedger(path,Binding(),created);
            Action<MenuEvent> sink=row=>{ledger.Append(row);rows.Add(row);};
            var first=new Menu("DEMO-menu-0",Keys[0],Start(0),sink);first.Begin();first.RunTo(MenuMs);Assert.That(first.Timeline.Complete,Is.True);
            var second=new Menu("DEMO-menu-1",Keys[1],Start(1),sink);second.Begin();
            if(point==ThirdPlay)
            {
                second.RunTo(11200);
                Assert.That(second.Plays.Count,Is.EqualTo(3),"Interrupted after the third play's onset, before it completed");
            }
            else
            {
                second.RunTo(25000);second.Choose(2);second.RunTo(27000);
                Assert.That(second.Timeline.Phase,Is.EqualTo(MenuPhase.Choice));Assert.That(second.Plays.Count,Is.EqualTo(6));
            }
            second.Interrupt();
            Assert.That(second.Timeline.Interrupted,Is.True);Assert.That(second.Timeline.Complete,Is.False);
            whileOpen?.Invoke(ledger,sink,second);
            return rows;
        }

        [TestCase(ThirdPlay)][TestCase(ChoiceWindow)]
        [Category("REQ_INT_08")][Category("REQ_INT_01")][Category("REQ_MENU_04")]
        public void InterruptedMenuEndsTheActiveVisitLedgerAndNothingReplays(string point)
        {
            string path=Path.Combine(directory,"active.jsonl");
            var rows=InterruptedVisit(point,path,(ledger,sink,interrupted)=>
            {
                string head=ledger.ChainHead;int plays=interrupted.Plays.Count;
                interrupted.RunTo(MenuMs+1000);
                Assert.That(interrupted.Plays.Count,Is.EqualTo(plays),"The interrupted timeline requests no further play");
                Assert.That(Assert.Throws<SessionFault>(()=>interrupted.Timeline.Start(Start(3))).Code,Is.EqualTo("MENU_START"),"The interrupted timeline cannot restart");
                var replay=new Menu("DEMO-menu-1-replay",Keys[1],Start(2),sink);
                Assert.That(Assert.Throws<SessionFault>(()=>replay.Begin()).Code,Is.EqualTo("MENU_LEDGER_UNAVAILABLE"),"The same menu is never replayed in this visit");
                var next=new Menu("DEMO-menu-2",Keys[2],Start(3),sink);
                Assert.That(Assert.Throws<SessionFault>(()=>next.Begin()).Code,Is.EqualTo("MENU_LEDGER_UNAVAILABLE"),"The visit's remaining menus cannot run");
                Assert.That(replay.Plays,Is.Empty);Assert.That(next.Plays,Is.Empty);
                Assert.Throws<SessionFault>(()=>ledger.Seal(Verification()),"An interrupted visit ledger cannot seal");
                Assert.That(ledger.ChainHead,Is.EqualTo(head),"Refused attempts write nothing");
            });
            var interruption=rows.Last();
            Assert.That(interruption.Kind,Is.EqualTo("menu_interrupted"));
            Assert.That(rows.Count(x=>x.Kind=="menu_start"),Is.EqualTo(2));
            Assert.That(rows.Count(x=>x.Kind=="play_request"),Is.EqualTo(8+(point==ThirdPlay?3:6)));
        }

        [TestCase(ThirdPlay)][TestCase(ChoiceWindow)]
        [Category("REQ_INT_08")][Category("REQ_INT_05")][Category("REQ_LOG_02")]
        public void PartialSegmentStaysRecordedWithItsMatchingDeviation(string point)
        {
            string path=Path.Combine(directory,"active.jsonl");var rows=InterruptedVisit(point,path);var interruption=rows.Last();
            Assert.That(interruption.MatchingDeviationId,Is.EqualTo(interruption.EventId));
            var lines=File.ReadAllLines(path).Select(x=>(JObject)JObject.Parse(x)["record"]).ToArray();
            Assert.That((string)lines[0]["kind"],Is.EqualTo("header"));
            Assert.That(lines.Skip(1).Select(x=>(string)x["event_id"]),Is.EqualTo(rows.Select(x=>x.EventId)),"Every event up to the interruption stays on disk, in order");
            Assert.That((string)lines.Last()["kind"],Is.EqualTo("menu_interrupted"));
            Assert.That((string)lines.Last()["matching_deviation_id"],Is.EqualTo(interruption.EventId));
            Assert.That(lines.Any(x=>((string)x["kind"]).StartsWith("sealed",StringComparison.Ordinal)),Is.False);

            var partial=rows.Where(x=>x.AttemptId==interruption.AttemptId).ToArray();
            Assert.That(partial.Any(x=>x.Kind=="choice_final"),Is.False,"No selection is applied for an interrupted menu");
            if(point==ChoiceWindow)Assert.That(partial.Count(x=>x.Kind=="choice_revised"&&x.SelectedIndex==2),Is.EqualTo(1),"The pending choice stays recorded");
            var exposure=MenuExposureProjection.Derive(rows).Where(x=>x.AttemptId==interruption.AttemptId).ToArray();
            Assert.That(exposure.Length,Is.EqualTo(point==ThirdPlay?3:6),"Each requested play remains an exposure");
            Assert.That(exposure.All(x=>x.MatchingDeviationId==interruption.EventId&&x.AcceptedOrRejected=="unresolved"&&x.ActiveChoiceOrDefault=="unresolved"),Is.True);
            if(point==ThirdPlay)
            {
                Assert.That(exposure.Take(2).All(x=>x.PlaybackStatus=="software_completed"),Is.True);
                Assert.That(exposure[2].PlaybackStatus,Is.EqualTo("onset_observed"),"The play cut off after onset is kept and flagged");
            }
            else Assert.That(exposure.All(x=>x.PlaybackStatus=="software_completed"),Is.True);
        }

        [TestCase(ThirdPlay)][TestCase(ChoiceWindow)]
        [Category("REQ_INT_08")][Category("REQ_YOKE_02")][Category("REQ_MENU_04")]
        public void AssignedPartnerCannotReplayTheInterruptedOrAReconstructedSequence(string point)
        {
            string path=Path.Combine(directory,"active.jsonl");var rows=InterruptedVisit(point,path);var interruption=rows.Last();
            string pin=PcmWave.Hash(File.ReadAllBytes(path));
            var refused=Assert.Throws<SessionFault>(()=>Load(path));
            Assert.That(new[]{"MENU_LEDGER_SCHEMA","MENU_LEDGER_UNSEALED"},Does.Contain(refused.Code),"The segment ends in an interruption, not a seal");

            var anchors=new List<YokedAnchorRecord>();int loads=0;
            var authority=new YokedReplayAuthority(5000,Guid.NewGuid().ToString("N"),pin,Hash,()=>1000,anchors.Add,anchor=>{loads++;return MenuReplaySequence.Load(path,pin,Binding(),Verification(),created.AddHours(1),anchor,()=>1000);});
            Assert.Throws<SessionFault>(()=>authority.BindForExplicitStart(Guid.NewGuid().ToString("N"),1,"start"));
            Assert.That(authority.Replay,Is.Null);Assert.That(loads,Is.EqualTo(1));Assert.That(anchors.Count,Is.EqualTo(1));
            Assert.That(Assert.Throws<SessionFault>(()=>authority.BindForExplicitStart(Guid.NewGuid().ToString("N"),2,"resume")).Code,Is.EqualTo("JOIN_YOKED_ANCHOR_CONSUMED"));
            Assert.That(loads,Is.EqualTo(1),"A refused partner admission is not retried against the same segment");

            // A spliced sequence that keeps the partial menu and re-runs it in full
            // is still an extra presentation and cannot be sealed for replay.
            string spliced=Path.Combine(directory,"spliced.jsonl");
            using(var ledger=new MenuLedger(spliced,Binding(),created))
            {
                foreach(var row in rows.Where(x=>x.Kind!="menu_interrupted"))ledger.Append(row);
                Action<MenuEvent> sink=ledger.Append;int slot=2;
                foreach(var (trial,key) in new[]{("DEMO-menu-1-rerun",Keys[1]),("DEMO-menu-2",Keys[2]),("DEMO-menu-3",Keys[3])})
                {var menu=new Menu(trial,key,Start(slot++),sink);menu.Begin();menu.RunTo(MenuMs);Assert.That(menu.Timeline.Complete,Is.True);}
                Assert.Throws<SessionFault>(()=>ledger.Seal(Verification()));
            }
            Assert.Throws<SessionFault>(()=>Load(spliced));
            Assert.That(rows.Count(x=>x.AttemptId==interruption.AttemptId&&x.Kind=="menu_start"),Is.EqualTo(1));
        }
    }
}
