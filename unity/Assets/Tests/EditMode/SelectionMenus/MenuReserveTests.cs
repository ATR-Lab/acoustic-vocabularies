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
    // REQ-BANK-05 without package fixtures: the reserve rule over synthetic menu
    // cells, then the active ledger, seal, replay load and assigned comparison over
    // synthetic waves (the timeline harness of MenuLedgerTests).
    public sealed class MenuReserveTests
    {
        static readonly string Hash=new string('a',64),Recipe=new string('c',64);
        static readonly string[] Keys={"K-a1","K-a2","K-r1","K-r2"};
        const string Missing=LoadedAudioPackage.OptionFileMissing,Changed=LoadedAudioPackage.OptionHashMismatch;
        readonly DateTimeOffset created=new DateTimeOffset(2026,1,1,0,0,0,TimeSpan.Zero);
        readonly List<(string key,int rank)> receipts=new List<(string,int)>();
        string directory;
        [SetUp]public void Before(){directory=Path.Combine(Path.GetTempPath(),"av-reserve-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(directory);receipts.Clear();}
        [TearDown]public void After(){Directory.Delete(directory,true);}

        sealed class History:IMenuPresentationHistory{internal readonly HashSet<string> Heard=new HashSet<string>();public bool Presented(string key)=>Heard.Contains(key);}
        static string Code(TestDelegate action)=>Assert.Throws<SessionFault>(action).Code;
        // Frozen bank cell: shown ranks 1-3 (450 ms) and the reserve (600 ms).
        static PcmWave[] Cell()=>new[]{SelectionMenuTests.Wave(21600,100),SelectionMenuTests.Wave(21600,200),SelectionMenuTests.Wave(21600,300),SelectionMenuTests.Wave(28800,400)};
        static MenuReserveRule.Bank Bank(PcmWave[] frozen)=>new MenuReserveRule.Bank(Hash,(rank,wave)=>wave.FileSha256==frozen[rank-1].FileSha256,
            wave=>wave.FileSha256==frozen[3].FileSha256?("DEMO-BANK.P1.K-a1.4",Recipe):((string,string)?)null);
        static (PcmWave[] cell,string[] reasons) Damaged(PcmWave[] frozen,params (int rank,string reason)[] damage)
        {var cell=(PcmWave[])frozen.Clone();var reasons=new string[4];foreach(var (rank,reason) in damage){cell[rank-1]=null;reasons[rank-1]=reason;}return (cell,reasons);}
        static MenuReserveRule.Result Resolve(string role,(PcmWave[] cell,string[] reasons) cell,MenuReserveRule.Bank bank,IMenuPresentationHistory history,bool replaying=false,MenuAmendment recorded=null)
            =>MenuReserveRule.Resolve(role,"P1","K-a1",cell.cell,cell.reasons,bank,history,replaying,recorded);

        [Test, Category("REQ_BANK_05"), NUnit.Framework.Property("Requirement","REQ-BANK-05")]
        public void ActiveSessionUsesTheReserveForOneUnusableOptionOfAnUnheardMenu()
        {
            var frozen=Cell();var bank=Bank(frozen);
            foreach(string reason in new[]{Missing,Changed})
            {
                var result=Resolve("active",Damaged(frozen,(2,reason)),bank,new History());
                Assert.That(result.Ranks,Is.EqualTo(new[]{1,4,3}));Assert.That(result.Waves.Select(x=>x.PcmSha256),Is.EqualTo(new[]{frozen[0],frozen[3],frozen[2]}.Select(x=>x.PcmSha256)));
                var a=result.Amendment;Assert.That(a.ReplacedRank,Is.EqualTo(2));Assert.That(a.Reason,Is.EqualTo(reason));Assert.That(a.BankSha256,Is.EqualTo(Hash));
                Assert.That(a.ReplacedCandidateId,Is.EqualTo("P1-K-a1-2"));Assert.That(a.ReserveCandidateId,Is.EqualTo("P1-K-a1-4"));Assert.That(a.ReserveOptionId,Is.EqualTo("DEMO-BANK.P1.K-a1.4"));
                Assert.That(a.ReservePcmSha256,Is.EqualTo(frozen[3].PcmSha256));Assert.That(a.ReserveFileSha256,Is.EqualTo(frozen[3].FileSha256));Assert.That(a.ReserveRecipeSha256,Is.EqualTo(Recipe));
            }
            // A cached option intact for the package but not the frozen bank option is unusable too.
            var changed=(PcmWave[])frozen.Clone();changed[0]=SelectionMenuTests.Wave(21600,900);
            var mismatch=Resolve("active",(changed,new string[4]),bank,new History());Assert.That(mismatch.Ranks,Is.EqualTo(new[]{4,2,3}));Assert.That(mismatch.Amendment.Reason,Is.EqualTo(Changed));
            // A usable menu never consults the history and never uses the reserve.
            var whole=Resolve("active",(frozen,new string[4]),bank,null);Assert.That(whole.Amendment,Is.Null);Assert.That(whole.Ranks,Is.EqualTo(new[]{1,2,3}));
        }

        [Test, Category("REQ_BANK_05"), NUnit.Framework.Property("Requirement","REQ-BANK-05")]
        public void AfterAnyPresentationOrInTheAssignedSessionSubstitutionIsRefused()
        {
            var frozen=Cell();var bank=Bank(frozen);var damaged=Damaged(frozen,(1,Missing));var heard=new History();heard.Heard.Add("K-a1");
            Assert.That(Code(()=>Resolve("active",damaged,bank,heard)),Is.EqualTo("MENU_RESERVE_AFTER_PRESENTATION"));
            Assert.That(Code(()=>Resolve("yoked",damaged,bank,new History())),Is.EqualTo("MENU_RESERVE_ASSIGNED_REFUSED"));
            // A menu recorded without an amendment is reproduced, never decided again.
            Assert.That(Code(()=>Resolve("yoked",damaged,bank,null,true)),Is.EqualTo("MENU_RESERVE_ASSIGNED_REFUSED"));
            Assert.That(Code(()=>Resolve("active",damaged,bank,null,true)),Is.EqualTo("MENU_RESERVE_AFTER_PRESENTATION"));
            Assert.That(Code(()=>Resolve("active",damaged,bank,null)),Is.EqualTo("MENU_RESERVE_HISTORY_UNAVAILABLE"));
            Assert.That(Code(()=>Resolve("active",damaged,null,new History())),Is.EqualTo("MENU_RESERVE_BANK_UNQUALIFIED"));
        }

        [Test, Category("REQ_BANK_05"), NUnit.Framework.Property("Requirement","REQ-BANK-05")]
        public void TwoUnusableOptionsOrAnUnusableReserveFailClosed()
        {
            var frozen=Cell();var bank=Bank(frozen);
            Assert.That(Code(()=>Resolve("active",Damaged(frozen,(1,Missing),(3,Changed)),bank,new History())),Is.EqualTo("MENU_RESERVE_MULTIPLE_UNUSABLE"));
            Assert.That(Code(()=>Resolve("active",Damaged(frozen,(2,Missing),(4,Missing)),bank,new History())),Is.EqualTo("MENU_RESERVE_UNUSABLE"));
            Assert.That(Code(()=>Resolve("active",Damaged(frozen,(2,Missing),(4,Changed)),bank,new History())),Is.EqualTo("MENU_RESERVE_UNUSABLE"));
            var foreign=Damaged(frozen,(2,Missing));foreign.cell[3]=SelectionMenuTests.Wave(28800,700);
            Assert.That(Code(()=>Resolve("active",foreign,bank,new History())),Is.EqualTo("MENU_RESERVE_UNUSABLE"),"reserve differs from the bank");
            var duplicate=Damaged(frozen,(2,Missing));duplicate.cell[3]=frozen[0];var same=(PcmWave[])frozen.Clone();same[3]=frozen[0];
            Assert.That(Code(()=>Resolve("active",duplicate,Bank(same),new History())),Is.EqualTo("MENU_RESERVE_UNUSABLE"),"reserve repeats a remaining option");
            var length=Damaged(frozen,(2,Missing));length.cell[3]=SelectionMenuTests.Wave(20000,400);var odd=(PcmWave[])frozen.Clone();odd[3]=length.cell[3];
            Assert.That(Code(()=>Resolve("active",length,Bank(odd),new History())),Is.EqualTo("MENU_RESERVE_UNUSABLE"),"reserve is not an atom duration");
        }

        [Test, Category("REQ_BANK_05"), NUnit.Framework.Property("Requirement","REQ-BANK-05")]
        public void RecordedAmendmentIsReproducedExactlyWithoutTheReplacedOption()
        {
            var frozen=Cell();var bank=Bank(frozen);var active=Resolve("active",Damaged(frozen,(2,Missing)),bank,new History()).Amendment;
            // The replaying device's own copy of the replaced option is never used.
            foreach(var cell in new[]{(frozen,new string[4]),Damaged(frozen,(2,Missing)),Damaged(frozen,(2,Changed))})
                foreach(string role in new[]{"yoked","active"})
                {
                    var replayed=Resolve(role,cell,bank,null,true,active);
                    Assert.That(replayed.Ranks,Is.EqualTo(new[]{1,4,3}));Assert.That(replayed.Amendment.SameAs(active),Is.True);Assert.That(replayed.Waves[1].FileSha256,Is.EqualTo(frozen[3].FileSha256));
                }
            var wrongReserve=new MenuAmendment(Hash,"P1","K-a1",2,Missing,"DEMO-BANK.P1.K-a1.4",frozen[0].PcmSha256,frozen[0].FileSha256,Recipe);
            var otherMenu=new MenuAmendment(Hash,"P1","K-a2",2,Missing,"DEMO-BANK.P1.K-a1.4",frozen[3].PcmSha256,frozen[3].FileSha256,Recipe);
            foreach(var record in new[]{wrongReserve,otherMenu})Assert.That(Code(()=>Resolve("yoked",(frozen,new string[4]),bank,null,true,record)),Is.EqualTo("MENU_RESERVE_RECORD_MISMATCH"));
            Assert.That(Code(()=>Resolve("yoked",(frozen,new string[4]),null,null,true,active)),Is.EqualTo("MENU_RESERVE_BANK_UNQUALIFIED"));
            Assert.That(Code(()=>Resolve("yoked",Damaged(frozen,(1,Missing)),bank,null,true,active)),Is.EqualTo("MENU_RESERVE_ASSIGNED_REFUSED"));
            Assert.That(Code(()=>Resolve("yoked",Damaged(frozen,(2,Missing),(4,Missing)),bank,null,true,active)),Is.EqualTo("MENU_RESERVE_UNUSABLE"));
        }

        // Ledger harness: four V2 atom menus, one of them amended.
        static MenuLedgerBinding Binding(string role="active",string bank=null)=>new MenuLedgerBinding(Hash,bank??Hash,Hash,Hash,Hash,Hash,"V2",role,Keys);
        static MenuOption[] Shown(string key)=>Enumerable.Range(1,3).Select(i=>new MenuOption(MenuAmendment.Candidate("P1",key,i),SelectionMenuTests.Wave(21600,i*100))).ToArray();
        static MenuOption Reserve(string key)=>new MenuOption(MenuAmendment.Candidate("P1",key,4),SelectionMenuTests.Wave(28800,400));
        static MenuOption[] Amended(string key,int rank){var options=Shown(key);options[rank-1]=Reserve(key);return options;}
        static MenuAmendment Amendment(string key,int rank)=>new MenuAmendment(Hash,"P1",key,rank,Missing,"DEMO-BANK.P1."+key+".4",Reserve(key).Wave.PcmSha256,Reserve(key).Wave.FileSha256,Recipe);
        MenuLedgerVerification Verification(bool reserve=true)=>new MenuLedgerVerification(Shown,(key,rank,hash)=>{receipts.Add((key,rank));return hash==Hash;},_=>"DEMO-meaning",
            reserve?(key,a)=>a.SameAs(Amendment(key,a.ReplacedRank))?Amended(key,a.ReplacedRank):null:(Func<string,MenuAmendment,MenuOption[]>)null);
        static SlotItem Item(int menu)=>new SlotItem("DEMO-menu-"+menu,"atom_menu",Keys[menu],null,"selection","selection",false,45,8,1);
        static List<MenuEvent> Events(MenuReplaySequence replay=null,string amendedKey="K-a1",int amendedRank=2,int choose=2)
        {
            var result=new List<MenuEvent>();double start=750;
            for(int menu=0;menu<Keys.Length;menu++)
            {
                var item=Item(menu);var ctx=new SlotContext(item,start,null);double clock=start-750;bool amended=Keys[menu]==amendedKey;
                var timeline=new MenuTimeline(ctx,amended?Amended(Keys[menu],amendedRank):Shown(Keys[menu]),"DEMO-meaning",result.Add,replay?.For(item),()=>clock,amended?Amendment(Keys[menu],amendedRank):null);
                var plays=new List<(string id,MenuOption option,double at)>();var heard=new HashSet<string>();var done=new HashSet<string>();
                timeline.PlayRequested+=(_,id,option,at)=>plays.Add((id,option,at));if(replay==null)timeline.SelectionRequested+=(index,unused)=>timeline.ConfirmSelection(index,Hash,start+32000);timeline.Start(start-750);
                for(double now=start;now<=start+45020;now+=10)
                {
                    clock=now;timeline.Tick(now);if(replay==null&&amended&&now==start+23000)timeline.Choose(choose,now);
                    foreach(var play in plays.ToArray()){if(now>=play.at&&heard.Add(play.id))timeline.Onset(play.id,play.at,10,now);if(now>=play.at+play.option.Wave.SampleCount/48d&&done.Add(play.id))timeline.Completed(play.id,now);}
                }
                Assert.That(timeline.Complete,Is.True);start+=50000;
            }
            return result;
        }
        string Write(IReadOnlyList<MenuEvent> events,MenuLedgerVerification seal=null,MenuLedgerBinding binding=null)
        {string path=Path.Combine(directory,Guid.NewGuid().ToString("N")+".jsonl");using(var writer=new MenuLedger(path,binding??Binding(),created)){foreach(var row in events)writer.Append(row);if(seal!=null)writer.Seal(seal);}return path;}

        [Test, Category("REQ_BANK_05"), NUnit.Framework.Property("Requirement","REQ-BANK-05")]
        public void AmendmentIsJournaledBeforeAnyAudioAndTheAssignedReplayReproducesIt()
        {
            var events=Events();var menu=events.Where(x=>x.MenuKey=="K-a1").ToList();
            Assert.That(menu.Select(x=>x.Kind).Take(3),Is.EqualTo(new[]{"menu_start",MenuAmendment.Kind,"display_request"}));
            Assert.That(menu.FindIndex(x=>x.Kind==MenuAmendment.Kind),Is.LessThan(menu.FindIndex(x=>x.Kind=="play_request")));
            Assert.That(menu.Where(x=>x.Kind=="play_request").Select(x=>x.CandidateId),Is.EqualTo(new[]{"P1-K-a1-1","P1-K-a1-1","P1-K-a1-4","P1-K-a1-4","P1-K-a1-3","P1-K-a1-3","P1-K-a1-4","P1-K-a1-4"}));
            string path=Write(events,Verification());Assert.That(receipts,Does.Contain(("K-a1",4)),"the selected reserve slot is verified as rank 4");
            var row=File.ReadAllLines(path).Select(x=>(JObject)JObject.Parse(x)["record"]).Single(x=>(string)x["kind"]==MenuAmendment.Kind);
            Assert.That((int)row["replaced_rank"],Is.EqualTo(2));Assert.That((string)row["reason"],Is.EqualTo(Missing));Assert.That((string)row["reserve_file_sha256"],Is.EqualTo(Reserve("K-a1").Wave.FileSha256));Assert.That((string)row["reserve_pcm_sha256"],Is.EqualTo(Reserve("K-a1").Wave.PcmSha256));
            var replay=MenuReplaySequence.Load(path,PcmWave.Hash(File.ReadAllBytes(path)),Binding(),Verification(),created.AddHours(1),1000,()=>0);
            Assert.That(replay.For(Item(0)).Amendment.SameAs(Amendment("K-a1",2)),Is.True);Assert.That(replay.For(Item(1)).Amendment,Is.Null);
            Assert.That(replay.CompareYoked(Events(replay),Binding("yoked"),Verification()).AudioPlays,Is.EqualTo(32));
            // The assigned session cannot drop, change or invent the amendment.
            Assert.That(Code(()=>new MenuTimeline(new SlotContext(Item(0),750,null),Shown("K-a1"),"DEMO-meaning",_=>{},replay.For(Item(0)))),Is.EqualTo("MENU_YOKED_AMENDMENT"));
            Assert.That(Code(()=>new MenuTimeline(new SlotContext(Item(1),750,null),Amended("K-a2",2),"DEMO-meaning",_=>{},replay.For(Item(1)),null,Amendment("K-a2",2))),Is.EqualTo("MENU_YOKED_AMENDMENT"));
            Assert.That(Code(()=>replay.CompareYoked(Events(replay).Where(x=>x.Kind!=MenuAmendment.Kind).ToList(),Binding("yoked"),Verification())),Is.EqualTo("MENU_YOKED_AMENDMENT"));
        }

        [Test, Category("REQ_BANK_05"), NUnit.Framework.Property("Requirement","REQ-BANK-05")]
        public void LedgerRefusesALateOrUnverifiedAmendment()
        {
            var events=Events();
            // Without an independent reserve authority, an amended ledger cannot seal.
            string path=Path.Combine(directory,"unverified.jsonl");
            using(var ledger=new MenuLedger(path,Binding(),created)){foreach(var e in events)ledger.Append(e);Assert.That(Code(()=>ledger.Seal(Verification(false))),Is.EqualTo("MENU_LEDGER_ASSETS"));}
            // A record for another bank, or one placed after a display event, is refused.
            using(var ledger=new MenuLedger(Path.Combine(directory,"foreign.jsonl"),Binding(bank:new string('b',64)),created)){foreach(var e in events)ledger.Append(e);Assert.That(Code(()=>ledger.Seal(Verification())),Is.EqualTo("MENU_LEDGER_AMENDMENT"));}
            var rows=events.Select(MenuJson.Event).ToList();var amended=rows.Single(x=>(string)x["kind"]==MenuAmendment.Kind);rows.Remove(amended);
            amended["mono_ms"]=rows[2]["mono_ms"];rows.Insert(3,amended);
            Assert.That(Code(()=>MenuReplaySequence.ValidateRecords(rows,Binding(),Verification())),Is.EqualTo("MENU_LEDGER_AMENDMENT"));
            // The live segment refuses an amendment after any event of that menu.
            using(var ledger=new MenuLedger(Path.Combine(directory,"late.jsonl"),Binding(),created))
            {
                var ctx=new SlotContext(Item(0),750,null);double clock=0;var timeline=new MenuTimeline(ctx,Shown("K-a1"),"DEMO-meaning",ledger.Append,null,()=>clock);timeline.Start(0);
                clock=760;timeline.Tick(760);Assert.That(ledger.Presented("K-a1"),Is.True);Assert.That(ledger.Presented("K-a2"),Is.False);
                Assert.That(Code(()=>ledger.Append(new MenuEvent(MenuAmendment.Kind,ctx,770,meaningDisplayId:"DEMO-meaning",amendment:Amendment("K-a1",2)))),Is.EqualTo("MENU_LEDGER_AMENDMENT"));
            }
        }

        [Test, Category("REQ_BANK_05"), NUnit.Framework.Property("Requirement","REQ-BANK-05")]
        public void RetainedLedgerSegmentsCountAsPresentationsAndUnverifiableOnesFailClosed()
        {
            // An interrupted earlier segment presented K-a1 before the live one began.
            string retained=Path.Combine(directory,"interrupted.jsonl");
            using(var ledger=new MenuLedger(retained,Binding(),created)){var timeline=new MenuTimeline(new SlotContext(Item(0),750,null),Shown("K-a1"),"DEMO-meaning",ledger.Append);timeline.Start(0);timeline.Interrupt(10);}
            byte[] bytes=File.ReadAllBytes(retained);var frozen=Cell();var damaged=Damaged(frozen,(1,Missing));
            using var live=new MenuLedger(Path.Combine(directory,"live.jsonl"),Binding(),created.AddMinutes(5));
            var history=new MenuPresentationHistory(Binding(),new[]{bytes},live);
            Assert.That(history.Presented("K-a1"),Is.True);Assert.That(history.Presented("K-a2"),Is.False);
            Assert.That(Code(()=>Resolve("active",damaged,Bank(frozen),history)),Is.EqualTo("MENU_RESERVE_AFTER_PRESENTATION"));
            Assert.That(MenuReserveRule.Resolve("active","P1","K-a2",damaged.cell,damaged.reasons,Bank(frozen),history,false,null).Ranks,Is.EqualTo(new[]{4,2,3}));
            var torn=bytes.Take(bytes.Length-1).ToArray();var edited=(byte[])bytes.Clone();edited[bytes.Length-20]^=1;
            foreach(var (segment,binding) in new[]{(torn,Binding()),(edited,Binding()),(bytes,Binding(bank:new string('b',64)))})
            {
                var invalid=new MenuPresentationHistory(binding,new[]{segment},live);
                Assert.That(Code(()=>invalid.Presented("K-a2")),Is.EqualTo("MENU_RESERVE_HISTORY_INVALID"));
                Assert.That(Code(()=>MenuReserveRule.Resolve("active","P1","K-a2",damaged.cell,damaged.reasons,Bank(frozen),invalid,false,null)),Is.EqualTo("MENU_RESERVE_HISTORY_INVALID"));
            }
        }
    }
}
