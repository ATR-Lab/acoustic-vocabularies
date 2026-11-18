using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using NUnit.Framework;

namespace AcousticVocab.SelectionMenus.Tests
{
    // Requirement trace for the Study B profile and atom menus, the assigned
    // (yoked) replay, interruption, restart and menu logging. Synthetic DEMO
    // tones only: no package, bank, store or participant data is read.
    public sealed class RequirementTraceMenuTests
    {
        const double Onset=750;
        static readonly string Hash=new string('a',64);
        static readonly string Receipt=new string('c',64);
        static readonly DateTimeOffset Created=new DateTimeOffset(2026,1,1,0,0,0,TimeSpan.Zero);
        static readonly double[] AtomPlayOffsets={5000d,8000,11000,14000,17000,20000,35000,38000};
        static readonly double[] ProfilePlayOffsets={6500d,10500,14500,18500,22500,26500,50000,54000};
        static readonly string[] WaveKeys={"K-a3","K-r3","Q-a3","Q-r3"};
        static readonly string[] VisitOneKeys={"profile","K-a1","K-a2","K-r1","K-r2","Q-a1","Q-a2","Q-r1","Q-r2"};
        // Active presses per menu (elapsed ms, stored index). Q-r3 has no press: a default.
        static readonly Dictionary<string,(double at,int index)[]> Presses=new Dictionary<string,(double,int)[]>
        {
            ["profile"]=new[]{(31000d,2)},["K-a3"]=new[]{(23000d,2)},["K-r3"]=new[]{(23000d,1),(30000d,3)},["Q-a3"]=new[]{(25000d,1)},["Q-r3"]=new (double,int)[0]
        };
        string directory;

        [SetUp]public void Before(){directory=Path.Combine(Path.GetTempPath(),"av-menu-trace-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(directory);}
        [TearDown]public void After(){if(directory!=null&&Directory.Exists(directory))Directory.Delete(directory,true);}

        static PcmWave Tone(int samples,int amplitude)
        {
            using var stream=new MemoryStream();using var writer=new BinaryWriter(stream);
            writer.Write(Encoding.ASCII.GetBytes("RIFF"));writer.Write(36+samples*2);writer.Write(Encoding.ASCII.GetBytes("WAVEfmt "));writer.Write(16);writer.Write((short)1);writer.Write((short)1);writer.Write(48000);writer.Write(96000);writer.Write((short)2);writer.Write((short)16);writer.Write(Encoding.ASCII.GetBytes("data"));writer.Write(samples*2);
            for(int i=0;i<samples;i++)writer.Write((short)(i%100<50?amplitude:-amplitude));writer.Flush();return PcmWave.ParseCanonical(stream.ToArray());
        }
        static MenuOption[] Candidates(string key,int samples=0)
        {
            int length=samples>0?samples:key=="profile"?96000:21600,salt=key.Sum(c=>c)%400;
            return Enumerable.Range(1,3).Select(i=>new MenuOption("DEMO-"+key+"-"+i,Tone(length,i*1000+salt))).ToArray();
        }
        static string Meaning(string key)=>key=="profile"?"DEMO-profile-display":"DEMO-meaning-"+key;
        static SlotItem Item(string key,int? slotSeconds=null)
        {bool profile=key=="profile";return new SlotItem("DEMO-trace-"+key,profile?"profile_menu":"atom_menu",profile?null:key,null,"selection","selection",false,slotSeconds??(profile?60:45),8,1);}
        static int SlotMs(string key)=>key=="profile"?60000:45000;
        static int Expected(string key)=>Presses.TryGetValue(key,out var presses)&&presses.Length>0?presses.Last().index:1;
        static MenuLedgerBinding Binding(string role)=>new MenuLedgerBinding(Hash,Hash,Hash,Hash,Hash,Hash,"V2",role,WaveKeys);
        static MenuLedgerVerification Verification()=>new MenuLedgerVerification(key=>Candidates(key),(key,index,receipt)=>receipt==Receipt&&index==Expected(key),Meaning);
        static string[] NewIds(int count)=>Enumerable.Range(0,count).Select(_=>Guid.NewGuid().ToString("N")).ToArray();

        // Drives one MenuTimeline on a 10 ms grid with software onset/completion
        // evidence at the scheduled times. Display boundaries are therefore exact.
        sealed class Menu
        {
            internal readonly string Key;internal readonly SlotContext Context;internal readonly MenuTimeline Timeline;internal readonly MenuOption[] Options;internal readonly List<MenuEvent> Events;
            internal readonly List<(int index,string id,MenuOption option,double at)> Plays=new List<(int,string,MenuOption,double)>();
            internal readonly List<(MenuPhase phase,int? choice,double at)> Views=new List<(MenuPhase,int?,double)>();
            internal readonly List<(int index,bool defaulted)> Selections=new List<(int,bool)>();
            readonly HashSet<string> heard=new HashSet<string>(),done=new HashSet<string>();bool audioStopped;
            internal double Now{get;private set;}
            internal IEnumerable<MenuEvent> Own=>Events.Where(x=>x.AttemptId==Context.Item.TrialId);
            internal Menu(string key,double onset,List<MenuEvent> sink=null,MenuReplay replay=null,MenuOption[] options=null,string meaning=null)
            {
                Key=key;Events=sink??new List<MenuEvent>();Options=options??Candidates(key);Context=new SlotContext(Item(key),onset,null);Now=onset-750;
                Timeline=new MenuTimeline(Context,Options,meaning??Meaning(key),Events.Add,replay);
                Timeline.PlayRequested+=(index,id,option,at)=>Plays.Add((index,id,option,at));
                Timeline.DisplayChanged+=(phase,choice)=>Views.Add((phase,choice,Now));
                Timeline.SelectionRequested+=(index,defaulted)=>{Selections.Add((index,defaulted));Timeline.ConfirmSelection(index,Receipt,Now);};
            }
            internal void Start()=>Timeline.Start(Now);
            internal void Advance(double until){while(Now+10<=until)Step(Now+10);if(Now<until)Step(until);}
            internal void Choose(int index)=>Timeline.Choose(index,Now);
            internal void Interrupt(){audioStopped=true;Timeline.Interrupt(Now);}
            void Step(double t)
            {
                Now=t;Timeline.Tick(t);if(audioStopped)return;
                foreach(var play in Plays.ToArray())
                {
                    if(t>=play.at&&heard.Add(play.id))Timeline.Onset(play.id,play.at,10,t);
                    if(t>=play.at+play.option.Wave.SampleCount/48d&&done.Add(play.id))Timeline.Completed(play.id,t);
                }
            }
        }
        sealed class Wave
        {internal readonly List<MenuEvent> Events=new List<MenuEvent>();internal readonly Dictionary<string,Menu> Menus=new Dictionary<string,Menu>();}

        // Runs the menus in order with a 5 s gap; the active role presses per
        // Presses, an assigned role replays its MenuReplay and never presses.
        static Wave Run(IEnumerable<string> keys,double firstOnset,Func<string,MenuReplay> replay=null,Func<string,string> meaning=null)
        {
            var wave=new Wave();double onset=firstOnset;
            foreach(string key in keys)
            {
                var menu=new Menu(key,onset,wave.Events,replay?.Invoke(key),meaning:meaning?.Invoke(key));wave.Menus.Add(key,menu);menu.Start();
                if(replay==null)foreach(var press in Presses[key]){menu.Advance(onset+press.at);menu.Choose(press.index);}
                menu.Advance(onset+SlotMs(key));Assert.That(menu.Timeline.Complete,Is.True,key);onset+=SlotMs(key)+5000;
            }
            return wave;
        }
        string Write(IEnumerable<MenuEvent> events,bool seal=true)
        {
            string path=Path.Combine(directory,Guid.NewGuid().ToString("N")+".jsonl");
            using(var ledger=new MenuLedger(path,Binding("active"),Created)){foreach(var row in events)ledger.Append(row);if(seal)ledger.Seal(Verification());}
            return path;
        }
        static MenuReplaySequence Load(string path,DateTimeOffset now)=>MenuReplaySequence.Load(path,PcmWave.Hash(File.ReadAllBytes(path)),Binding("active"),Verification(),now,1000,()=>0);
        static byte[] LiveBytes(string path)
        {using var stream=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite);using var copy=new MemoryStream();stream.CopyTo(copy);return copy.ToArray();}
        static string[] LiveLines(string path)
        {
            using var stream=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite);using var reader=new StreamReader(stream);
            var rows=new List<string>();string line;while((line=reader.ReadLine())!=null)rows.Add(line);return rows.ToArray();
        }

        [Test, Category("REQ_MENU_01")]
        public void VisitOneProfileMenuLastsSixtySecondsAndPlaysEachProfileExampleTwice()
        {
            Assert.That(Assert.Throws<SessionFault>(()=>new MenuTimeline(new SlotContext(Item("profile",45),Onset,null),Candidates("profile"),Meaning("profile"),_=>{})).Code,Is.EqualTo("MENU_SLOT"));
            Assert.That(Assert.Throws<SessionFault>(()=>new MenuTimeline(new SlotContext(Item("profile"),Onset,null),Candidates("profile",21600),Meaning("profile"),_=>{})).Code,Is.EqualTo("MENU_DURATION"),"Profile options are the 2 s profile examples");
            Assert.DoesNotThrow(()=>new MenuLedgerBinding(Hash,Hash,Hash,Hash,Hash,Hash,"V1","active",VisitOneKeys));
            Assert.Throws<SessionFault>(()=>new MenuLedgerBinding(Hash,Hash,Hash,Hash,Hash,Hash,"V1","active",VisitOneKeys.Skip(1).Concat(new[]{"profile"})),"Visit 1 opens with the profile menu");
            Assert.Throws<SessionFault>(()=>new MenuLedgerBinding(Hash,Hash,Hash,Hash,Hash,Hash,"V2","active",new[]{"profile","K-a3","K-r3","Q-a3"}),"Only visit 1 has a profile menu");

            var menu=new Menu("profile",Onset);menu.Start();menu.Advance(Onset+59990);
            Assert.That(menu.Timeline.Phase,Is.EqualTo(MenuPhase.Neutral));
            menu.Advance(Onset+60000);
            Assert.That(menu.Timeline.Phase,Is.EqualTo(MenuPhase.Ended));Assert.That(menu.Timeline.Complete,Is.True);
            Assert.That(menu.Views.Single(x=>x.phase==MenuPhase.Ended).at-Onset,Is.EqualTo(60000));
            var examples=menu.Plays.Where(x=>x.index<=6).ToArray();
            Assert.That(examples.Select(x=>x.at-Onset),Is.EqualTo(ProfilePlayOffsets.Take(6)));
            Assert.That(examples.All(x=>x.at-Onset<30000),Is.True,"Every example is heard before the choice window opens");
            Assert.That(examples.GroupBy(x=>x.option.CandidateId).ToDictionary(x=>x.Key,x=>x.Count()),Is.EquivalentTo(menu.Options.ToDictionary(x=>x.CandidateId,_=>2)));
            Assert.That(examples.All(x=>x.option.Wave.SampleCount==96000),Is.True);
        }

        [Test, Category("REQ_MENU_02")]
        public void ActiveMemberPicksTheProfileOnceAndItCannotChangeAfterTheWindow()
        {
            var menu=new Menu("profile",Onset);menu.Start();menu.Advance(Onset+31000);menu.Choose(3);menu.Advance(Onset+60000);
            Assert.That(menu.Selections,Is.EqualTo(new[]{(3,false)}),"One commit request, carrying the active member's choice");
            Assert.That(menu.Timeline.FinalSelection,Is.EqualTo(3));
            Assert.That(Assert.Throws<SessionFault>(()=>menu.Choose(1)).Code,Is.EqualTo("MENU_CHOICE_REFUSED"));
            Assert.That(Assert.Throws<SessionFault>(()=>menu.Timeline.ConfirmSelection(1,Receipt,menu.Now)).Code,Is.EqualTo("MENU_COMMIT_REFUSED"));
            Assert.That(menu.Events.Count(x=>x.Kind=="selection_verified"),Is.EqualTo(1));
        }

        [Test, Category("REQ_MENU_02"), Category("REQ_YOKE_04")]
        public void AssignedReplayOffersNoChoiceInAnyPhaseAndNeverShowsAPendingChoice()
        {
            foreach(string key in new[]{"profile","K-a3"})
            {
                bool profile=key=="profile";var replay=new MenuReplay(profile?ProfilePlayOffsets:AtomPlayOffsets,NewIds(8),2,false,Guid.NewGuid().ToString("N"),Receipt);
                var active=new Menu(key,Onset);var menu=new Menu(key,Onset,replay:replay,options:active.Options);menu.Start();var phases=new List<MenuPhase>();
                foreach(double at in profile?new[]{0d,3000,10000,33000,44990,46000,59000}:new[]{0d,2000,10000,25000,31990,33000,41000})
                {
                    menu.Advance(Onset+at);phases.Add(menu.Timeline.Phase);
                    Assert.That(Assert.Throws<SessionFault>(()=>menu.Choose(1)).Code,Is.EqualTo("MENU_CHOICE_REFUSED"),key+" "+menu.Timeline.Phase);
                }
                Assert.That(phases.Distinct(),Is.EquivalentTo(new[]{MenuPhase.Instructions,MenuPhase.Audition,MenuPhase.Choice,MenuPhase.Selected,MenuPhase.Neutral}));
                menu.Advance(Onset+SlotMs(key));Assert.That(menu.Timeline.Complete,Is.True);
                Assert.That(menu.Selections,Is.Empty,"The assigned member never requests a commit");
                Assert.That(menu.Events.Any(x=>x.Kind=="choice_revised"),Is.False);
                Assert.That(menu.Views.Where(x=>x.phase==MenuPhase.Choice).All(x=>x.choice==null),Is.True);
                Assert.That(menu.Views.Single(x=>x.phase==MenuPhase.Selected).choice,Is.EqualTo(2));
                Assert.That(menu.Plays.Select(x=>x.option.CandidateId),Is.EqualTo(new[]{1,1,2,2,3,3,2,2}.Select(i=>"DEMO-"+key+"-"+i)),"Same options as the active menu");
            }
        }

        [Test, Category("REQ_MENU_03")]
        public void AtomMenuHasTwoPlaysPerOptionATenSecondWindowAndTwoPlaysOfTheChoice()
        {
            var menu=new Menu("K-a3",Onset);menu.Start();
            menu.Advance(Onset+21990);Assert.Throws<SessionFault>(()=>menu.Choose(2),"Window not yet open");
            menu.Advance(Onset+22000);Assert.DoesNotThrow(()=>menu.Choose(1));
            menu.Advance(Onset+31990);Assert.DoesNotThrow(()=>menu.Choose(3));
            menu.Advance(Onset+45000);
            Assert.That(menu.Timeline.Complete,Is.True);Assert.That(menu.Views.Single(x=>x.phase==MenuPhase.Ended).at-Onset,Is.EqualTo(45000));
            Assert.That(menu.Plays.Select(x=>x.at-Onset),Is.EqualTo(AtomPlayOffsets));
            Assert.That(menu.Plays.Take(6).Select(x=>x.option.CandidateId),Is.EqualTo(new[]{1,1,2,2,3,3}.Select(i=>"DEMO-K-a3-"+i)));
            Assert.That(menu.Plays.Skip(6).Select(x=>x.option.CandidateId),Is.EqualTo(new[]{"DEMO-K-a3-3","DEMO-K-a3-3"}));
            var final=menu.Events.Single(x=>x.Kind=="choice_final");Assert.That(final.MonoMs-Onset,Is.EqualTo(32000));Assert.That(final.SelectedIndex,Is.EqualTo(3));
            Assert.That(menu.Views.Where(x=>x.phase==MenuPhase.Choice&&x.choice==null).Select(x=>x.at-Onset).Single(),Is.EqualTo(22000));
            Assert.That(menu.Views.Single(x=>x.phase==MenuPhase.Selected).at-Onset,Is.EqualTo(32000),"Choice window is 22-32 s: 10 s");
            var late=new Menu("K-a3",Onset);late.Start();late.Advance(Onset+32000);
            Assert.That(Assert.Throws<SessionFault>(()=>late.Choose(2)).Code,Is.EqualTo("MENU_CHOICE_REFUSED"));
        }

        [Test, Category("REQ_MENU_04")]
        public void MenuOffersNoPreviewOrReplayBeyondItsEightScheduledPlays()
        {
            var menu=new Menu("Q-r3",Onset);menu.Start();menu.Advance(Onset+22000);
            for(int k=0;k<20;k++){menu.Choose(1+k%3);menu.Advance(menu.Now+400);}
            menu.Advance(Onset+60000);
            Assert.That(menu.Plays.Count,Is.EqualTo(8));Assert.That(menu.Events.Count(x=>x.Kind=="play_request"),Is.EqualTo(8));
            Assert.That(menu.Plays.Select(x=>x.id),Is.EqualTo(menu.Context.AudioRequestIds),"Each of the eight slot requests is used exactly once");
            Assert.That(typeof(IMenuView).GetEvents().Select(x=>x.Name),Is.EqualTo(new[]{"Chosen"}),"The view's only input is a candidate choice");
            Assert.That(typeof(IMenuView).GetEvent("Chosen").EventHandlerType,Is.EqualTo(typeof(Action<int>)));
            var listening=typeof(MenuTimeline).GetMethods(BindingFlags.Public|BindingFlags.Instance|BindingFlags.DeclaredOnly).Where(x=>!x.IsSpecialName).Select(x=>x.Name)
                .Where(x=>new[]{"Play","Preview","Replay","Repeat","Listen","Audition"}.Any(word=>x.IndexOf(word,StringComparison.OrdinalIgnoreCase)>=0)).ToArray();
            Assert.That(listening,Is.Empty);
            var interrupted=new Menu("Q-r3",Onset);interrupted.Start();interrupted.Advance(Onset+6000);interrupted.Interrupt();
            Assert.Throws<SessionFault>(()=>interrupted.Timeline.Start(interrupted.Now),"An interrupted menu cannot be started again");
            interrupted.Advance(Onset+45000);Assert.That(interrupted.Plays.Count,Is.EqualTo(1));
        }

        [TestCase("K-r3",23000,32000)][TestCase("profile",31000,45000)][Category("REQ_MENU_05")]
        public void PressIsAppliedOnlyWhenTheChoiceWindowCloses(string key,double press,double deadline)
        {
            var menu=new Menu(key,Onset);menu.Start();menu.Advance(Onset+press);menu.Choose(2);
            menu.Advance(Onset+deadline-10);
            Assert.That(menu.Selections,Is.Empty);Assert.That(menu.Timeline.FinalSelection,Is.Null);
            Assert.That(menu.Events.Any(x=>x.Kind is "choice_final" or "selection_verified"),Is.False);
            Assert.That(menu.Events.Single(x=>x.Kind=="choice_revised").SelectedIndex,Is.EqualTo(2));
            menu.Advance(Onset+deadline);
            Assert.That(menu.Selections,Is.EqualTo(new[]{(2,false)}));
            var final=menu.Events.Single(x=>x.Kind=="choice_final");
            Assert.That(final.MonoMs-Onset,Is.EqualTo(deadline));Assert.That(final.ExpectedMonoMs-Onset,Is.EqualTo(deadline));Assert.That(final.SelectedIndex,Is.EqualTo(2));Assert.That(final.Defaulted,Is.False);
        }

        [TestCase("profile")][TestCase("Q-a3")][Category("REQ_MENU_06")]
        public void NoValidChoiceAppliesTheStoredFirstOptionAndRecordsADefault(string key)
        {
            var menu=new Menu(key,Onset);menu.Start();menu.Advance(Onset+1000);
            Assert.Throws<SessionFault>(()=>menu.Choose(2),"A press outside the window is not a valid choice");
            menu.Advance(Onset+SlotMs(key));
            Assert.That(menu.Selections,Is.EqualTo(new[]{(1,true)}));
            var final=menu.Events.Single(x=>x.Kind=="choice_final");Assert.That(final.SelectedIndex,Is.EqualTo(1));Assert.That(final.Defaulted,Is.True);
            var verified=menu.Events.Single(x=>x.Kind=="selection_verified");Assert.That(verified.SelectedIndex,Is.EqualTo(1));Assert.That(verified.Defaulted,Is.True);
            Assert.That(menu.Plays.Skip(6).Select(x=>x.option.CandidateId),Is.EqualTo(new[]{menu.Options[0].CandidateId,menu.Options[0].CandidateId}));
            var rows=MenuExposureProjection.Derive(menu.Events);
            Assert.That(rows.All(x=>x.ActiveChoiceOrDefault=="default"),Is.True);
            Assert.That(rows.Where(x=>x.AcceptedOrRejected=="accepted").Select(x=>x.CandidateId).Distinct(),Is.EqualTo(new[]{menu.Options[0].CandidateId}));
        }

        [Test, Category("REQ_MENU_07")]
        public void MenusAcceptAtomKeysAndAtomLengthAudioOnly()
        {
            Assert.DoesNotThrow(()=>Binding("active"));
            Assert.That(Assert.Throws<SessionFault>(()=>new MenuLedgerBinding(Hash,Hash,Hash,Hash,Hash,Hash,"V2","active",new[]{"K-a3-r3","K-r3","Q-a3","Q-r3"})).Code,Is.EqualTo("MENU_LEDGER_BINDING"));
            Assert.That(Assert.Throws<SessionFault>(()=>new MenuLedgerBinding(Hash,Hash,Hash,Hash,Hash,Hash,"V1","active",VisitOneKeys.Take(8).Concat(new[]{"Q-a1-r1"}))).Code,Is.EqualTo("MENU_LEDGER_BINDING"));
            foreach(int samples in new[]{21600,28800,36000,43200})Assert.DoesNotThrow(()=>new MenuTimeline(new SlotContext(Item("K-a3"),Onset,null),Candidates("K-a3",samples),Meaning("K-a3"),_=>{}));
            // A message is action + gap + referent, longer than the longest atom (43,200 samples).
            foreach(int samples in new[]{52800,62400,96000})
                Assert.That(Assert.Throws<SessionFault>(()=>new MenuTimeline(new SlotContext(Item("K-a3"),Onset,null),Candidates("K-a3",samples),Meaning("K-a3"),_=>{})).Code,Is.EqualTo("MENU_DURATION"));
        }

        [Test, Category("REQ_YOKE_01")]
        public void AssignedReplayNeedsASealedActiveLedgerNoOlderThanTwentyFourHours()
        {
            var active=Run(WaveKeys,Onset);string path=Write(active.Events);
            Assert.DoesNotThrow(()=>Load(path,Created));
            Assert.DoesNotThrow(()=>Load(path,Created.AddHours(24)));
            Assert.That(Assert.Throws<SessionFault>(()=>Load(path,Created.AddHours(24).AddMilliseconds(1))).Code,Is.EqualTo("MENU_REPLAY_AGE"));
            Assert.That(Assert.Throws<SessionFault>(()=>Load(path,Created.AddMilliseconds(-1))).Code,Is.EqualTo("MENU_REPLAY_AGE"),"The assigned session cannot precede the active one");
            string unsealed=Write(active.Events,false);Assert.Throws<SessionFault>(()=>Load(unsealed,Created.AddHours(1)));
        }

        [Test, Category("REQ_YOKE_01")]
        [Ignore("Requirement gap: the assigned replay accepts a sealed menu ledger while the active session is still running")]
        public void AssignedReplayRequiresEvidenceThatTheActiveSessionEnded()
        {
            var active=Run(WaveKeys,Onset);string path=Path.Combine(directory,"active-open.jsonl"),copy=Path.Combine(directory,"active-copy.jsonl");
            using(var ledger=new MenuLedger(path,Binding("active"),Created))
            {
                foreach(var row in active.Events)ledger.Append(row);ledger.Seal(Verification());
                // The active session continues with its post-menu blocks; its writer is still open.
                File.WriteAllBytes(copy,LiveBytes(path));
                Assert.Throws<SessionFault>(()=>Load(copy,Created.AddHours(1)),"No assigned replay before the active session has ended");
            }
        }

        [Test, Category("REQ_YOKE_02"), Category("REQ_YOKE_03")]
        public void AssignedReplayRepeatsTheActiveOptionsChoicesMeaningsAndTimingWithEightPlaysPerMeaning()
        {
            var active=Run(WaveKeys,Onset);var sequence=Load(Write(active.Events),Created.AddHours(2));
            var assigned=Run(WaveKeys,Onset+100000,key=>sequence.For(Item(key)));
            var comparison=sequence.CompareYoked(assigned.Events,Binding("yoked"),Verification());
            Assert.That(comparison.Menus,Is.EqualTo(4));Assert.That(comparison.AtomPlays,Is.EqualTo(32));
            foreach(string key in WaveKeys)
            {
                var a=active.Menus[key];var y=assigned.Menus[key];int chosen=Expected(key);
                foreach(var menu in new[]{a,y})
                {
                    Assert.That(menu.Plays.Count,Is.EqualTo(8),key);
                    Assert.That(menu.Plays.Take(6).GroupBy(x=>x.option.CandidateId).Select(x=>x.Count()),Is.EqualTo(new[]{2,2,2}),key+": two plays of each of the three options");
                    Assert.That(menu.Plays.Skip(6).Select(x=>x.option.CandidateId),Is.EqualTo(Enumerable.Repeat("DEMO-"+key+"-"+chosen,2)),key+": two confirmation plays of the choice");
                    Assert.That(menu.Own.Select(x=>x.MeaningDisplayId).Distinct(),Is.EqualTo(new[]{Meaning(key)}),key+": one meaning screen");
                }
                Assert.That(y.Plays.Select(x=>(x.option.CandidateId,x.option.Wave.PcmSha256,x.at-y.Context.OnsetMonoMs)),Is.EqualTo(a.Plays.Select(x=>(x.option.CandidateId,x.option.Wave.PcmSha256,x.at-a.Context.OnsetMonoMs))),key);
                Assert.That(y.Views.Where(x=>x.phase!=MenuPhase.Choice||x.choice==null).Select(x=>(x.phase,x.choice,x.at-y.Context.OnsetMonoMs)),
                    Is.EqualTo(a.Views.Where(x=>x.phase!=MenuPhase.Choice||x.choice==null).Select(x=>(x.phase,x.choice,x.at-a.Context.OnsetMonoMs))),key+": same screens at the same times");
                Assert.That(y.Own.Single(x=>x.Kind=="choice_final").Defaulted,Is.EqualTo(a.Own.Single(x=>x.Kind=="choice_final").Defaulted));
            }
            Assert.That(assigned.Menus.Values.Select(x=>x.Context.OnsetMonoMs-Onset-100000),Is.EqualTo(active.Menus.Values.Select(x=>x.Context.OnsetMonoMs-Onset)),"Same pauses between menus");
            var otherMeaning=Run(WaveKeys,Onset+400000,key=>sequence.For(Item(key)),key=>"DEMO-other-"+key);
            Assert.Throws<SessionFault>(()=>sequence.CompareYoked(otherMeaning.Events,Binding("yoked"),Verification()),"A replay showing other meaning screens does not match");
        }

        [Test, Category("REQ_INT_08")]
        public void InterruptedMenuStopsWithoutExtraPlaysAndLogsAMatchingDeviationThatPausesTheReplay()
        {
            var events=new List<MenuEvent>();var first=new Menu("K-a3",Onset,events);first.Start();
            foreach(var press in Presses["K-a3"]){first.Advance(Onset+press.at);first.Choose(press.index);}
            first.Advance(Onset+45000);
            double onset=Onset+50000;var second=new Menu("K-r3",onset,events);second.Start();second.Advance(onset+11100);second.Interrupt();
            second.Advance(onset+45000);
            Assert.That(second.Plays.Select(x=>x.index),Is.EqualTo(new[]{1,2,3}),"No later or repeated presentation after the interruption");
            Assert.That(second.Timeline.Interrupted,Is.True);Assert.That(second.Timeline.Complete,Is.False);
            var interruption=events.Single(x=>x.Kind=="menu_interrupted");
            Assert.That(interruption.MatchingDeviationId,Is.EqualTo(interruption.EventId));Assert.That(interruption.AttemptId,Is.EqualTo(second.Context.Item.TrialId));
            var rows=MenuExposureProjection.Derive(events);
            Assert.That(rows.Where(x=>x.AttemptId==second.Context.Item.TrialId).Select(x=>(x.MatchingDeviationId,x.AcceptedOrRejected)),Is.EqualTo(Enumerable.Repeat((interruption.EventId,"unresolved"),3)));
            Assert.That(rows.Where(x=>x.AttemptId==first.Context.Item.TrialId).All(x=>x.MatchingDeviationId==null),Is.True);
            string path=Path.Combine(directory,"interrupted.jsonl");
            using(var ledger=new MenuLedger(path,Binding("active"),Created))
            {
                foreach(var row in events)ledger.Append(row);
                var next=new Menu("Q-a3",onset+50000);next.Start();
                Assert.That(Assert.Throws<SessionFault>(()=>ledger.Append(next.Events.Single())).Code,Is.EqualTo("MENU_LEDGER_UNAVAILABLE"),"The collection is paused: no later menu is recorded on this segment");
                Assert.Throws<SessionFault>(()=>ledger.Seal(Verification()));
            }
            Assert.That(LiveLines(path).Length,Is.EqualTo(events.Count+1),"The partial segment is preserved");
            Assert.Throws<SessionFault>(()=>Load(path,Created.AddHours(1)),"The assigned partner cannot replay an interrupted wave");
        }

        [Test, Category("REQ_INT_09")]
        [Ignore("Requirement gap: a restarted Study B visit cannot close its menu stage from the committed menus; it blocks the rest of the visit")]
        public void RestartedVisitClosesItsMenuStageFromTheCommittedMenusWithoutRerunningThem()
        {
            var first=Run(WaveKeys,Onset);Write(first.Events);
            // Relaunch after all four menus were committed: no menu is presented again.
            using var restarted=new MenuLedger(Path.Combine(directory,"restarted.jsonl"),Binding("active"),Created.AddMinutes(30));
            Assert.DoesNotThrow(()=>restarted.Seal(Verification()),"The restarted visit continues from the committed state");
        }

        [Test, Category("REQ_LOG_02")]
        public void SealedMenuLedgerRecordsChoicesDefaultsReceiptsAndEveryPresentation()
        {
            var active=Run(WaveKeys,Onset);string path=Write(active.Events);
            Assert.DoesNotThrow(()=>Load(path,Created.AddHours(1)),"Hash chain, canonical bytes, assets and receipts verify");
            var records=LiveLines(path).Select(x=>MenuJson.Parse(Encoding.UTF8.GetBytes(x))["record"]).ToArray();
            Assert.That((string)records.First()["kind"],Is.EqualTo("header"));Assert.That((string)records.Last()["kind"],Is.EqualTo("sealed"));
            foreach(string key in WaveKeys)
            {
                var rows=records.Where(x=>(string)x["menu_key"]==key).ToArray();int Count(string kind)=>rows.Count(x=>(string)x["kind"]==kind);
                Assert.That(Count("play_request"),Is.EqualTo(8),key);Assert.That(Count("onset_authority"),Is.EqualTo(8),key);Assert.That(Count("play_complete"),Is.EqualTo(8),key);
                Assert.That(Count("display_request")+Count("display_changed"),Is.EqualTo(12),key);
                Assert.That(Count("choice_revised"),Is.EqualTo(Presses[key].Length),key);
                var final=rows.Single(x=>(string)x["kind"]=="choice_final");var receipt=rows.Single(x=>(string)x["kind"]=="selection_verified");
                Assert.That((int)final["selected_index"],Is.EqualTo(Expected(key)));Assert.That((bool)final["defaulted"],Is.EqualTo(Presses[key].Length==0),key);
                Assert.That((string)receipt["receipt_sha256"],Is.EqualTo(Receipt));
                Assert.That(rows.Where(x=>(string)x["kind"]=="play_request").All(x=>(string)x["candidate_id"]!=null&&(string)x["pcm_sha256"]!=null&&(string)x["audio_request_id"]!=null),Is.True);
            }
        }
    }
}
