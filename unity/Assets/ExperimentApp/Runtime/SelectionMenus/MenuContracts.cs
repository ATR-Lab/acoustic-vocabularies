using System;
using System.Linq;
using System.Text.RegularExpressions;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;

namespace AcousticVocab.SelectionMenus
{
    public enum MenuPhase { Hidden, Instructions, Audition, Choice, Selected, Neutral, Ended }
    public sealed class MenuOption
    {
        public string CandidateId {get;}
        public PcmWave Wave {get;}
        internal MenuOption(string id,PcmWave wave)
        {MenuRules.Require(MenuRules.Id(id)&&wave!=null&&MenuRules.Hash(wave.FileSha256)&&MenuRules.Hash(wave.PcmSha256),"MENU_OPTION");CandidateId=id;Wave=wave;}
    }
    // Only a strict verified active-ledger loader constructs this object.
    // Its offsets are actual recorded host times relative to the active start.
    public sealed class MenuReplay
    {
        internal readonly double[] OffsetsMs;
        internal readonly string[] SourceEvents;
        internal readonly int SelectedIndex;
        internal readonly bool Defaulted;
        internal readonly string SelectionEventId,SelectionReceiptSha256;
        internal MenuReplay(double[] offsets,string[] events,int selected,bool defaulted,string choiceEvent,string receipt)
        {
            MenuRules.Require(offsets!=null&&offsets.Length==8&&offsets.All(MenuRules.Finite)&&offsets.All(x=>x>=0)&&offsets.Zip(offsets.Skip(1),(a,b)=>b>a).All(x=>x)&&
                events!=null&&events.Length==8&&events.All(MenuRules.Guid)&&events.Distinct().Count()==8&&selected>=1&&selected<=3&&(!defaulted||selected==1)&&MenuRules.Guid(choiceEvent)&&MenuRules.Hash(receipt),"MENU_REPLAY_INVALID");
            OffsetsMs=(double[])offsets.Clone();SourceEvents=(string[])events.Clone();SelectedIndex=selected;Defaulted=defaulted;SelectionEventId=choiceEvent;SelectionReceiptSha256=receipt;
        }
    }
    public sealed class MenuEvent
    {
        public string EventId {get;}=System.Guid.NewGuid().ToString("N");
        public string Kind {get;}
        public string AttemptId {get;}
        public string OpportunityId {get;}
        public string MenuKey {get;}
        public string MeaningDisplayId {get;}
        public double SlotStartMonoMs {get;}
        public double MonoMs {get;}
        public double? ExpectedMonoMs {get;}
        public double? OnsetUncertaintyMs {get;}
        public string AudioRequestId {get;}
        public int? PresentationIndex {get;}
        public string CandidateId {get;}
        public string PcmSha256 {get;}
        public string FileSha256 {get;}
        public string YokedSourceEventId {get;}
        public int? SelectedIndex {get;}
        public bool? Defaulted {get;}
        public MenuPhase? Phase {get;}
        public string ReceiptSha256 {get;}
        internal MenuEvent(string kind,SlotContext context,double now,double? expected=null,int? play=null,MenuOption option=null,string source=null,int? selected=null,bool? defaulted=null,MenuPhase? phase=null,string receipt=null,double? uncertainty=null,string meaningDisplayId=null)
        {MeaningDisplayId=meaningDisplayId;OnsetUncertaintyMs=uncertainty;Kind=kind;AttemptId=context.Item.TrialId;OpportunityId=context.OpportunityId;MenuKey=context.Item.TrialType=="profile_menu"?"profile":context.Item.ContentId;SlotStartMonoMs=context.OnsetMonoMs;MonoMs=now;ExpectedMonoMs=expected;PresentationIndex=play;AudioRequestId=play.HasValue?context.AudioRequestIds[play.Value-1]:null;CandidateId=option?.CandidateId;PcmSha256=option?.Wave.PcmSha256;FileSha256=option?.Wave.FileSha256;YokedSourceEventId=source;SelectedIndex=selected;Defaulted=defaulted;Phase=phase;ReceiptSha256=receipt;}
    }
    internal static class MenuRules
    {
        internal static bool Finite(double value)=>!double.IsNaN(value)&&!double.IsInfinity(value);
        internal static bool Id(string value)=>value!=null&&Regex.IsMatch(value,@"\A[A-Za-z0-9][A-Za-z0-9._-]{0,79}\z");
        internal static bool Hash(string value)=>value!=null&&Regex.IsMatch(value,@"\A[0-9a-f]{64}\z");
        internal static bool Guid(string value)=>value!=null&&Regex.IsMatch(value,@"\A[0-9a-f]{32}\z");
        internal static void Require(bool okay,string code){if(!okay)throw new SessionFault(code);}
    }
}
