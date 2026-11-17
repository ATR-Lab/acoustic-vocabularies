using System;
using System.Collections.Generic;
using System.Linq;
using NUnit.Framework;

namespace AcousticVocab.SelectionMenus.Tests
{
    // The optional visible countdown comes from the menu timeline only and is the
    // same for the active and assigned roles at the same slot times.
    public sealed class MenuCountdownTests
    {
        static readonly string Hash=new string('b',64);
        static string Id()=>Guid.NewGuid().ToString("N");
        static string[] Ids(int count)=>Enumerable.Range(0,count).Select(_=>Id()).ToArray();

        // Drives one run with the same tick times as SelectionMenuTests.Run.Finish
        // and records what the countdown and the final selection are after each tick.
        static List<(double elapsed,int? seconds,int? final,MenuPhase phase)> Drive(SelectionMenuTests.Run run,int? choose)
        {
            var trace=new List<(double,int?,int?,MenuPhase)>();var heard=new HashSet<string>();var done=new HashSet<string>();
            bool profile=run.Context.Item.TrialType=="profile_menu";double onset=run.Context.OnsetMonoMs;
            for(double now=50;now<=run.Context.EndMonoMs;now+=50)
            {
                run.Timeline.Tick(now);
                if(choose.HasValue&&now==onset+(profile?31000:23000))run.Timeline.Choose(choose.Value,now);
                foreach(var play in run.Plays.ToArray())
                {if(now>=play.at&&heard.Add(play.id))run.Timeline.Onset(play.id,play.at,10,now);if(now>=play.at+play.option.Wave.SampleCount/48d&&done.Add(play.id))run.Timeline.Completed(play.id,now);}
                trace.Add((now-onset,run.Timeline.CountdownSeconds,run.Timeline.FinalSelection,run.Timeline.Phase));
            }
            return trace;
        }

        static MenuReplay AssignedReplay(bool profile,int selected)
        {
            // Recorded active display changes trail the nominal boundaries by a frame;
            // the countdown must not follow them.
            var audio=profile?new[]{6500d,10500,14500,18500,22500,26500,50000,54000}:new[]{5000d,8000,11000,14000,17000,20000,35000,38000};
            var display=(profile?new[]{0d,6000,30000,45000,58000,60000}:new[]{0d,4000,22000,32000,40000,45000}).Select(x=>x+12).ToArray();
            return new MenuReplay(audio,Ids(8),selected,false,Id(),Hash,display,Ids(6),Ids(6),Id(),Id());
        }

        [TestCase(true)][TestCase(false)]
        [Category("REQ_MENU_08"),NUnit.Framework.Property("Requirement","REQ-MENU-08")]
        public void ActiveAndAssignedShowTheSameSecondsAtTheSameSlotTimes(bool profile)
        {
            var active=Drive(new SelectionMenuTests.Run(profile),2);var assigned=Drive(new SelectionMenuTests.Run(profile,AssignedReplay(profile,3)),null);
            Assert.That(active.Select(x=>x.elapsed),Is.EqualTo(assigned.Select(x=>x.elapsed)));
            Assert.That(assigned.Select(x=>x.seconds),Is.EqualTo(active.Select(x=>x.seconds)),"Identical countdown for both roles");
            Assert.That(active.Last().final,Is.EqualTo(2));Assert.That(assigned.Last().final,Is.EqualTo(3),"Different choices, same countdown");
            Assert.That(active.Where(x=>x.seconds.HasValue).Select(x=>x.seconds.Value).Distinct().Count(),Is.GreaterThan(10));
        }

        [TestCase(true)][TestCase(false)]
        [Category("REQ_MENU_08"),NUnit.Framework.Property("Requirement","REQ-MENU-08")]
        public void SecondsCountDownEachShownPhaseAndNeverMoveTheDeadline(bool profile)
        {
            var trace=Drive(new SelectionMenuTests.Run(profile),2).ToDictionary(x=>x.elapsed);
            var expected=profile
                ?new Dictionary<double,int?>{{-50,null},{0,6},{5950,1},{6000,24},{29950,1},{30000,15},{44950,1},{45000,13},{57950,1},{58000,null},{59950,null}}
                :new Dictionary<double,int?>{{-50,null},{0,4},{3950,1},{4000,18},{21950,1},{22000,10},{31950,1},{32000,8},{39950,1},{40000,null},{44950,null}};
            foreach(var pair in expected)Assert.That(trace[pair.Key].seconds,Is.EqualTo(pair.Value),"elapsed "+pair.Key);
            double deadline=profile?45000:32000;
            Assert.That(trace[deadline-50].final,Is.Null,"No choice takes effect before the deadline");Assert.That(trace[deadline].final,Is.EqualTo(2));
            Assert.That(trace.Values.Where(x=>x.phase is MenuPhase.Hidden or MenuPhase.Neutral or MenuPhase.Ended).All(x=>x.seconds==null),Is.True,"Nothing is shown while the menu is hidden");
            Assert.That(trace.Values.Where(x=>x.seconds.HasValue).All(x=>x.seconds.Value>=1),Is.True);
        }

        [Test,Category("REQ_MENU_08"),NUnit.Framework.Property("Requirement","REQ-MENU-08")]
        public void PreparedOrInterruptedMenuHasNoCountdown()
        {
            var run=new SelectionMenuTests.Run(false);Assert.That(run.Timeline.CountdownSeconds,Is.Null,"Started before its onset");
            run.Timeline.Tick(750);Assert.That(run.Timeline.CountdownSeconds,Is.EqualTo(4));
            run.Timeline.Tick(2000);Assert.That(run.Timeline.CountdownSeconds,Is.EqualTo(3));
            run.Timeline.Interrupt(2100);Assert.That(run.Timeline.CountdownSeconds,Is.Null);
        }
    }
}
