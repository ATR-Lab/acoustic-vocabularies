using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using NUnit.Framework;

namespace AcousticVocab.SelectionMenus.Tests
{
    public sealed class SelectionMenuTests
    {
        static readonly string Hash=new string('a',64);
        static PcmWave Wave(int samples,int tone)
        {
            using var stream=new MemoryStream();using var writer=new BinaryWriter(stream);
            writer.Write(Encoding.ASCII.GetBytes("RIFF"));writer.Write(36+samples*2);writer.Write(Encoding.ASCII.GetBytes("WAVEfmt "));writer.Write(16);writer.Write((short)1);writer.Write((short)1);writer.Write(48000);writer.Write(96000);writer.Write((short)2);writer.Write((short)16);writer.Write(Encoding.ASCII.GetBytes("data"));writer.Write(samples*2);
            for(int i=0;i<samples;i++)writer.Write((short)(i%100<50?tone:-tone));writer.Flush();return PcmWave.ParseCanonical(stream.ToArray());
        }
        internal sealed class Run
        {
            internal readonly SlotContext Context;internal readonly MenuTimeline Timeline;internal readonly List<MenuEvent> Events=new List<MenuEvent>();
            internal readonly List<(int index,string id,MenuOption option,double at)> Plays=new List<(int,string,MenuOption,double)>();
            internal readonly List<(MenuPhase phase,int? choice)> Views=new List<(MenuPhase,int?)>();
            internal Run(bool profile,MenuReplay replay=null)
            {
                var item=new SlotItem("DEMO-menu",profile?"profile_menu":"atom_menu",profile?null:"K-a1",null,"selection","selection",false,profile?60:45,8,1);
                Context=new SlotContext(item,750,null);var options=Enumerable.Range(1,3).Select(x=>new MenuOption("DEMO-candidate-"+x,Wave(profile?96000:21600,x*100))).ToArray();
                Timeline=new MenuTimeline(Context,options,Events.Add,replay);Timeline.PlayRequested+=(index,id,option,at)=>Plays.Add((index,id,option,at));Timeline.DisplayChanged+=(phase,choice)=>Views.Add((phase,choice));
                Timeline.SelectionRequested+=(index,unused)=>Timeline.ConfirmSelection(index,Hash,profile?45750:32750);Timeline.Start(0);
            }
            internal void Finish(int? choose=null,bool revise=false)
            {
                var heard=new HashSet<string>();var done=new HashSet<string>();bool profile=Context.Item.TrialType=="profile_menu";
                for(double now=50;now<=Context.EndMonoMs;now+=50)
                {
                    Timeline.Tick(now);
                    if(choose.HasValue&&now==750+(profile?31000:23000))Timeline.Choose(choose.Value,now);
                    if(revise&&now==750+(profile?44000:31000))Timeline.Choose(3,now);
                    foreach(var play in Plays.ToArray())
                    {if(now>=play.at&&heard.Add(play.id))Timeline.Onset(play.id,play.at,10,now);if(now>=play.at+play.option.Wave.SampleCount/48d&&done.Add(play.id))Timeline.Completed(play.id,now);}
                }
            }
        }
        [TestCase(false)][TestCase(true)]public void EightPlaysAtFixedOffsetsAndDefaultFirstStoredChoice(bool profile)
        {
            var run=new Run(profile);run.Finish();Assert.That(run.Timeline.Complete,Is.True);
            Assert.That(run.Plays.Select(x=>x.at-750),Is.EqualTo(profile?new[]{6500d,10500,14500,18500,22500,26500,50000,54000}:new[]{5000d,8000,11000,14000,17000,20000,35000,38000}));
            Assert.That(run.Plays.Select(x=>x.option.CandidateId),Is.EqualTo(new[]{"DEMO-candidate-1","DEMO-candidate-1","DEMO-candidate-2","DEMO-candidate-2","DEMO-candidate-3","DEMO-candidate-3","DEMO-candidate-1","DEMO-candidate-1"}));
            var final=run.Events.Single(x=>x.Kind=="choice_final");Assert.That(final.Defaulted,Is.True);Assert.That(final.SelectedIndex,Is.EqualTo(1));Assert.That(run.Events.Count(x=>x.Kind=="play_complete"),Is.EqualTo(8));
        }
        [TestCase(false)][TestCase(true)]public void ActiveCanReviseUntilDeadlineWithoutAdditionalListening(bool profile)
        {var run=new Run(profile);run.Finish(2,true);Assert.That(run.Timeline.FinalSelection,Is.EqualTo(3));Assert.That(run.Plays.Count,Is.EqualTo(8));Assert.That(run.Events.Single(x=>x.Kind=="choice_final").Defaulted,Is.False);}
        [Test]public void YokedHasNoIntermediateSelectionAndUsesOnlyStoredFinalChoice()
        {
            var ids=Enumerable.Range(0,8).Select(_=>Guid.NewGuid().ToString("N")).ToArray();var replay=new MenuReplay(new[]{5000d,8000,11000,14000,17000,20000,35000,38000},ids,2,false,Guid.NewGuid().ToString("N"),Hash);
            var run=new Run(false,replay);run.Finish();Assert.That(run.Views.Where(x=>x.phase==MenuPhase.Choice).All(x=>x.choice==null),Is.True);
            Assert.That(run.Events.Any(x=>x.Kind=="choice_revised"),Is.False);Assert.That(run.Events.Where(x=>x.Kind=="play_request").Select(x=>x.YokedSourceEventId),Is.EqualTo(ids));
            Assert.That(run.Timeline.FinalSelection,Is.EqualTo(2));Assert.Throws<SessionFault>(()=>run.Timeline.Choose(1,45750));
        }
        [Test]public void LateSchedulingDoesNotBackfillMissedPlay()
        {var run=new Run(false);run.Timeline.Tick(750);run.Timeline.Tick(4750);Assert.Throws<SessionFault>(()=>run.Timeline.Tick(5700));Assert.That(run.Plays,Is.Empty);}
        [Test]public void InterruptionRetainsPartialEventsAndStopsAllLaterPlays()
        {var run=new Run(false);run.Timeline.Tick(750);run.Timeline.Tick(4750);run.Timeline.Tick(5000);run.Timeline.Interrupt(5100);run.Timeline.Tick(50000);Assert.That(run.Timeline.Interrupted,Is.True);Assert.That(run.Timeline.Complete,Is.False);Assert.That(run.Plays.Count,Is.EqualTo(1));Assert.That(run.Events.Last().Kind,Is.EqualTo("menu_interrupted"));}
        [Test]public void ReplayCannotTimeCompressFailedTimingIntoNominalSchedule()
        {var offsets=new[]{5000d,8000,11000,14000,17000,20000,35000,38500};var replay=new MenuReplay(offsets,Enumerable.Range(0,8).Select(_=>Guid.NewGuid().ToString("N")).ToArray(),1,true,Guid.NewGuid().ToString("N"),Hash);Assert.Throws<SessionFault>(()=>new Run(false,replay));}
    }
}
