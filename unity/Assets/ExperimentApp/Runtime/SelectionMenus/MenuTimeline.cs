using System;
using System.Linq;
using AcousticVocab.SessionEngine;

namespace AcousticVocab.SelectionMenus
{
    // No cursor/input state is copied into a yoked view. Only the verified final
    // selection becomes visible at the deadline; active revisions stay private.
    public sealed class MenuTimeline
    {
        readonly SlotContext context;readonly MenuOption[] options;readonly MenuReplay replay;readonly Action<MenuEvent> persist;readonly Func<double> observed;
        readonly double[] offsets,displayOffsets;readonly bool profile;readonly string meaningDisplayId;
        readonly bool[] requested=new bool[8],completed=new bool[8],onset=new bool[8];
        double last=-1;int choice,final;bool started,failed,finalized,committed,defaulted;
        public MenuPhase Phase {get;private set;}=MenuPhase.Hidden;
        public bool Yoked=>replay!=null;
        public bool Complete=>Phase==MenuPhase.Ended&&!failed&&committed&&completed.All(x=>x);
        public bool Interrupted=>failed;
        public int? FinalSelection=>finalized?final:(int?)null;
        public event Action<int,string,MenuOption,double> PlayRequested;
        public event Action<MenuPhase,int?> DisplayChanged;
        public event Action<int,bool> SelectionRequested;
        public MenuTimeline(SlotContext context,MenuOption[] options,string meaningDisplayId,Action<MenuEvent> durableSink,MenuReplay replay=null,Func<double> observedClock=null)
        {
            MenuRules.Require(context.Item!=null&&context.Item.Phase=="selection"&&!context.Item.Heldout&&context.Item.Plays==8&&context.RetryOf==null&&
                (context.Item.TrialType=="profile_menu"&&context.Item.SlotSeconds==60||context.Item.TrialType=="atom_menu"&&context.Item.SlotSeconds==45),"MENU_SLOT");
            MenuRules.Require(options!=null&&options.Length==3&&options.All(x=>x!=null)&&options.Select(x=>x.CandidateId).Distinct().Count()==3&&options.Select(x=>x.Wave.PcmSha256).Distinct().Count()==3&&durableSink!=null,"MENU_OPTIONS");
            MenuRules.Require(MenuRules.Id(meaningDisplayId),"MENU_DISPLAY_ID");this.meaningDisplayId=meaningDisplayId;
            this.context=context;this.options=(MenuOption[])options.Clone();this.persist=durableSink;this.replay=replay;observed=observedClock;profile=context.Item.TrialType=="profile_menu";
            offsets=replay==null?(profile?new[]{6500d,10500,14500,18500,22500,26500,50000,54000}:new[]{5000d,8000,11000,14000,17000,20000,35000,38000}):(double[])replay.OffsetsMs.Clone();
            displayOffsets=replay?.DisplayOffsetsMs??(profile?new[]{0d,6000,30000,45000,58000,60000}:new[]{0d,4000,22000,32000,40000,45000});
            MenuRules.Require(options.All(x=>profile?x.Wave.SampleCount==96000:new[]{21600,28800,36000,43200}.Contains(x.Wave.SampleCount)),"MENU_DURATION");
            if(replay!=null)
            {
                double[] nominal=profile?new[]{6500d,10500,14500,18500,22500,26500,50000,54000}:new[]{5000d,8000,11000,14000,17000,20000,35000,38000};
                MenuRules.Require(offsets.Zip(nominal,(actual,wanted)=>Math.Abs(actual-wanted)<=20).All(x=>x),"MENU_REPLAY_TIMING");
            }
        }
        double ChoiceOpen=>profile?30000:22000;
        double ChoiceDeadline=>profile?45000:32000;
        double NeutralStart=>profile?58000:40000;
        void Clock(double now){MenuRules.Require(MenuRules.Finite(now)&&now>=0&&now>=last,"MENU_CLOCK");last=now;}
        void Emit(string kind,double now,double? expected=null,int? play=null,MenuOption option=null,string source=null,int? selected=null,bool? isDefault=null,MenuPhase? phase=null,string receipt=null,double? uncertainty=null)
        {
            double stamp=observed==null?now:observed();MenuRules.Require(MenuRules.Finite(stamp)&&stamp>=now,"MENU_CLOCK");
            persist(new MenuEvent(kind,context,stamp,expected,play,option,source,selected,isDefault,phase,receipt,uncertainty,meaningDisplayId));
        }
        public void Start(double now){Clock(now);MenuRules.Require(!started&&!failed&&now<=context.OnsetMonoMs,"MENU_START");started=true;Emit("menu_start",now,context.OnsetMonoMs,source:replay?.StartEventId);Tick(now);}
        public void Choose(int index,double now)
        {
            Clock(now);double elapsed=now-context.OnsetMonoMs;
            MenuRules.Require(started&&!failed&&!Yoked&&!finalized&&index>=1&&index<=3&&elapsed>=ChoiceOpen&&elapsed<ChoiceDeadline,"MENU_CHOICE_REFUSED");
            Emit("choice_revised",now,selected:index);choice=index;DisplayChanged?.Invoke(MenuPhase.Choice,index);
        }
        public void ConfirmSelection(int index,string receiptSha256,double now)
        {
            Clock(now);MenuRules.Require(finalized&&!committed&&!failed&&index==final&&MenuRules.Hash(receiptSha256),"MENU_COMMIT_REFUSED");
            Emit("selection_verified",now,selected:final,isDefault:defaulted,source:replay?.VerificationEventId,receipt:receiptSha256);committed=true;
        }
        public void Tick(double now)
        {
            Clock(now);if(!started||failed||Phase==MenuPhase.Ended)return;double elapsed=now-context.OnsetMonoMs;
            if(elapsed>=ChoiceDeadline&&!finalized)
            {
                MenuRules.Require(elapsed<offsets[6]-150,"MENU_DECISION_LATE");
                final=replay?.SelectedIndex??(choice==0?1:choice);defaulted=replay?.Defaulted??choice==0;
                Emit("choice_final",now,context.OnsetMonoMs+ChoiceDeadline,source:replay?.SelectionEventId,selected:final,isDefault:defaulted);finalized=true;
                if(Yoked)ConfirmSelection(final,replay.SelectionReceiptSha256,now);else SelectionRequested?.Invoke(final,defaulted);
            }
            MenuPhase wanted=elapsed<displayOffsets[0]?MenuPhase.Hidden:elapsed<displayOffsets[1]?MenuPhase.Instructions:elapsed<displayOffsets[2]?MenuPhase.Audition:
                elapsed<displayOffsets[3]?MenuPhase.Choice:elapsed<displayOffsets[4]?MenuPhase.Selected:elapsed<displayOffsets[5]?MenuPhase.Neutral:MenuPhase.Ended;
            if(wanted!=Phase)
            {
                MenuRules.Require((int)wanted<=(int)Phase+1,"MENU_DISPLAY_BOUNDARY_MISSED");
                if(wanted==MenuPhase.Ended)MenuRules.Require(committed&&completed.All(x=>x),"MENU_INCOMPLETE");
                Emit("display_request",now,source:replay?.DisplayRequestEvents?[(int)wanted-1],phase:wanted,selected:wanted==MenuPhase.Selected?final:(int?)null);
                Phase=wanted;DisplayChanged?.Invoke(Phase,Phase==MenuPhase.Selected?final:(int?)null);Emit("display_changed",now,source:replay?.DisplayChangedEvents?[(int)Phase-1],phase:Phase,selected:Phase==MenuPhase.Selected?final:(int?)null);
            }
            for(int i=0;i<8;i++)
            {
                double at=context.OnsetMonoMs+offsets[i];
                if(requested[i]&&!onset[i]&&now>at+250)throw new SessionFault("MENU_ONSET_MISSING");
                if(requested[i]||now<at-750)continue;
                MenuRules.Require(now<=at-150&&(i==0||completed[i-1])&&(i<6||committed),"MENU_SCHEDULE_LATE");
                var option=options[i<6?i/2:final-1];Emit("play_request",now,at,i+1,option,replay?.SourceEvents[i]);requested[i]=true;
                PlayRequested?.Invoke(i+1,context.AudioRequestIds[i],option,at);
            }
        }
        public void Onset(string id,double estimateMs,double uncertaintyMs,double now)
        {
            Clock(now);int i=context.AudioRequestIds.ToList().IndexOf(id);
            MenuRules.Require(!failed&&i>=0&&requested[i]&&!onset[i]&&MenuRules.Finite(estimateMs)&&MenuRules.Finite(uncertaintyMs)&&uncertaintyMs>=0&&uncertaintyMs<=20&&Math.Abs(estimateMs-context.OnsetMonoMs-offsets[i])<=uncertaintyMs+.000001,"MENU_ONSET_AUTHORITY");
            Emit("onset_authority",now,estimateMs,i+1,options[i<6?i/2:final-1],replay?.SourceEvents[i],uncertainty:uncertaintyMs);onset[i]=true;
        }
        public void Completed(string id,double now)
        {
            Clock(now);int i=context.AudioRequestIds.ToList().IndexOf(id);MenuRules.Require(!failed&&i>=0&&requested[i]&&onset[i]&&!completed[i],"MENU_COMPLETION");
            Emit("play_complete",now,play:i+1,option:options[i<6?i/2:final-1],source:replay?.SourceEvents[i]);completed[i]=true;
        }
        public void Interrupt(double now)
        {Clock(now);if(failed)return;failed=true;Phase=MenuPhase.Ended;try{Emit("menu_interrupted",now);}finally{DisplayChanged?.Invoke(MenuPhase.Hidden,null);}}
    }
}
