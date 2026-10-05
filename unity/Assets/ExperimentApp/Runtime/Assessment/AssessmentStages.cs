using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;

namespace AcousticVocab.Assessment
{
    public sealed class AssessmentFault : Exception
    {
        public string Code { get; }
        public AssessmentFault(string code) : base(code!=null&&Regex.IsMatch(code,@"\A[A-Z][A-Z0-9_]{0,63}\z")?code:"ASSESSMENT_FAULT") { Code=Message; }
    }

    public sealed class AssessmentRecord
    {
        public string EventKind { get; }
        public string ScheduleSha256 { get; }
        public double HostMonoMs { get; }
        public string ClockEpoch { get; }
        public string Stage { get; }
        public string ItemId { get; }
        public int? Value { get; }
        public string OutcomeCode { get; }
        public AssessmentRecord(string kind,string schedule,double mono,string stage,string item=null,int? value=null,string outcome=null,string clockEpoch=null)
        {
            clockEpoch??=Guid.NewGuid().ToString("N");
            if(!new[]{"forms_started","rating","forms_completed","optional_started","optional_help","optional_execution"}.Contains(kind)||
                schedule==null||!Regex.IsMatch(schedule,@"\A[0-9a-f]{64}\z")||double.IsNaN(mono)||double.IsInfinity(mono)||mono<0||
                !Regex.IsMatch(clockEpoch,@"\A[0-9a-f]{32}\z")||stage is not ("forms" or "post_w4_optional")||item!=null&&!Regex.IsMatch(item,@"\A[A-Za-z][A-Za-z0-9_-]{0,63}\z")||
                outcome!=null&&!new[]{"requested","completed","failed","cancelled"}.Contains(outcome))
                throw new AssessmentFault("ASSESSMENT_RECORD_INVALID");
            EventKind=kind;ScheduleSha256=schedule;HostMonoMs=mono;ClockEpoch=clockEpoch;Stage=stage;ItemId=item;Value=value;OutcomeCode=outcome;
        }
    }

    public interface IAssessmentJournal
    {
        IReadOnlyList<AssessmentRecord> Records { get; }
        void Append(AssessmentRecord record);
    }

    public sealed class RatingItem
    {
        public string Id { get; }
        public string Question { get; }
        public string LowLabel { get; }
        public string HighLabel { get; }
        public int Minimum { get; }
        public int Maximum { get; }
        internal RatingItem(string id,string question,int low,int high,string lowLabel,string highLabel)
        { Id=id;Question=question;Minimum=low;Maximum=high;LowLabel=lowLabel;HighLabel=highLabel; }
    }

    // Proposed wording is public engineering material, not a methodology sign-off.
    public static class RatingPlan
    {
        public static IReadOnlyList<RatingItem> For(string study,string visit)
        {
            RatingItem difficulty=new RatingItem("difficulty","How difficult were the sounds to understand?",1,7,"Not at all difficult","Extremely difficult");
            RatingItem pleasant=new RatingItem("pleasantness","How pleasant were the sounds?",1,7,"Not at all pleasant","Extremely pleasant");
            if(study=="A"&&visit=="D0")return Array.AsReadOnly(new[]{difficulty,pleasant});
            if(study=="A"&&visit=="D7")return Array.AsReadOnly(new[]{new RatingItem("usability","How easy was the system to use?",1,7,"Very difficult","Very easy"),difficulty});
            if(study=="B"&&new[]{"V1","V2","V3","W1","W4"}.Contains(visit))return Array.AsReadOnly(new[]{
                new RatingItem("ownership","How much did the sound vocabulary feel like your own?",1,7,"Not at all","Very much"),
                new RatingItem("preference_fit","How well did the sounds fit your preferences?",1,7,"Not at all","Very well"),
                new RatingItem("influence","How much influence did you have over the sounds?",1,7,"None","Very much"),pleasant,
                new RatingItem("mental_demand","How mentally demanding was the task?",0,10,"Not at all demanding","Extremely demanding")});
            throw new AssessmentFault("ASSESSMENT_VISIT_INVALID");
        }
    }

    public sealed class AssessmentStages
    {
        readonly VisitSchedule schedule;
        readonly ISessionJournal session;
        readonly IAssessmentJournal journal;
        readonly ISessionClock clock;
        readonly bool wordingReviewed;
        readonly Func<bool> safeBoundary;
        readonly string clockEpoch=Guid.NewGuid().ToString("N");
        readonly Dictionary<string,double> epochClocks=new Dictionary<string,double>(StringComparer.Ordinal);
        readonly HashSet<string> ratings=new HashSet<string>(StringComparer.Ordinal);
        double lastTime=-1;
        bool failed;
        string pendingOptionalKind,pendingOptionalItem;
        public bool FormsStarted { get; private set; }
        public bool FormsComplete { get; private set; }
        public bool OptionalStarted { get; private set; }
        public bool OptionalRequestPending=>pendingOptionalKind!=null;
        public IReadOnlyList<RatingItem> RatingItems { get; }
        public RatingItem CurrentRating => FormsStarted&&!FormsComplete?RatingItems.FirstOrDefault(x=>!ratings.Contains(x.Id)):null;
        public bool FinalDelayed => schedule.Study=="A"&&schedule.Visit=="D7"||schedule.Study=="B"&&schedule.Visit=="W4";
        public AssessmentStages(VisitSchedule schedule,ISessionJournal session,IAssessmentJournal journal,ISessionClock clock,Func<bool> safeBoundary,bool wordingReviewed=false)
        {
            this.schedule=schedule??throw new ArgumentNullException(nameof(schedule));this.session=session??throw new ArgumentNullException(nameof(session));
            this.journal=journal??throw new ArgumentNullException(nameof(journal));this.clock=clock??throw new ArgumentNullException(nameof(clock));this.wordingReviewed=wordingReviewed;
            this.safeBoundary=safeBoundary??throw new ArgumentNullException(nameof(safeBoundary));
            RatingItems=RatingPlan.For(schedule.Study,schedule.Visit);
            foreach(var record in journal.Records)Apply(record);
        }
        void Need(bool value,string code) { if(failed||!value)throw new AssessmentFault(failed?"ASSESSMENT_JOURNAL_FAILED":code); }
        double Now()
        {
            double now=clock.NowMs;Need(!double.IsNaN(now)&&!double.IsInfinity(now)&&now>=0&&now>=lastTime,"ASSESSMENT_CLOCK_INVALID");lastTime=now;return now;
        }
        bool Completed(string phase)
        {
            var ids=schedule.Blocks.SelectMany(b=>b.Items).Where(x=>phase=="protected"?x.Phase=="protected"||x.Phase=="pre_test":x.Phase==phase).Select(x=>x.TrialId).ToArray();
            if(ids.Length==0)return false;
            var done=new HashSet<string>(session.Records.Where(x=>x.ScheduleSha256==schedule.Sha256&&x.Event=="state_after"&&x.State==ItemState.Done)
                .Select(x=>x.OpportunityId??x.RetryOf??x.TrialId));
            return ids.All(done.Contains);
        }
        public bool ProtectedComplete => Completed("protected");
        public bool ValidityComplete => FinalDelayed&&Completed("validity");
        public void RequireSafeBoundary()=>Need(safeBoundary(),"ASSESSMENT_BOUNDARY_REQUIRED");
        void Write(string kind,string stage,string item=null,int? value=null,string outcome=null)
        {
            var record=new AssessmentRecord(kind,schedule.Sha256,Now(),stage,item,value,outcome,clockEpoch);
            try { journal.Append(record); } catch { failed=true;throw new AssessmentFault("ASSESSMENT_JOURNAL_FAILED"); }
            Apply(record);
        }
        void Apply(AssessmentRecord record)
        {
            Need(record!=null&&record.ScheduleSha256==schedule.Sha256,"ASSESSMENT_HISTORY_SCOPE");
            Need(!epochClocks.TryGetValue(record.ClockEpoch,out double previous)||record.HostMonoMs>=previous,"ASSESSMENT_CLOCK_INVALID");epochClocks[record.ClockEpoch]=record.HostMonoMs;
            switch(record.EventKind)
            {
                case "forms_started":
                    Need(ProtectedComplete&&!FormsStarted&&record.Stage=="forms"&&record.ItemId==null&&record.Value==null&&record.OutcomeCode==null,"ASSESSMENT_HISTORY_ORDER");FormsStarted=true;break;
                case "rating":
                    var item=CurrentRating;Need(item!=null&&record.Stage=="forms"&&record.ItemId==item.Id&&record.Value>=item.Minimum&&record.Value<=item.Maximum&&record.OutcomeCode==null,"ASSESSMENT_RATING_INVALID");
                    ratings.Add(item.Id);break;
                case "forms_completed":
                    Need(FormsStarted&&!FormsComplete&&ratings.Count==RatingItems.Count&&record.Stage=="forms"&&record.ItemId==null&&record.Value==null&&record.OutcomeCode==null,"ASSESSMENT_HISTORY_ORDER");FormsComplete=true;break;
                case "optional_started":
                    Need(!OptionalStarted&&FormsComplete&&ValidityComplete&&schedule.Study=="B"&&schedule.Visit=="W4"&&record.Stage=="post_w4_optional"&&record.ItemId==null&&record.Value==null&&record.OutcomeCode==null,"ASSESSMENT_HISTORY_ORDER");OptionalStarted=true;break;
                case "optional_help":
                case "optional_execution":
                    Need(OptionalStarted&&record.Stage=="post_w4_optional"&&record.ItemId!=null&&record.Value==null&&CanOptionalRecord(record.EventKind,record.ItemId,record.OutcomeCode),"ASSESSMENT_HISTORY_ORDER");
                    if(record.OutcomeCode=="requested"){pendingOptionalKind=record.EventKind;pendingOptionalItem=record.ItemId;}else{pendingOptionalKind=null;pendingOptionalItem=null;}break;
            }
        }
        public void BeginForms()
        {
            Need(safeBoundary()&&ProtectedComplete&&!FormsStarted&&(schedule.Demo||wordingReviewed),"ASSESSMENT_FORMS_BLOCKED");
            Write("forms_started","forms");
        }
        public void Rate(string itemId,int value)
        {
            var item=CurrentRating;Need(safeBoundary()&&item!=null&&item.Id==itemId&&value>=item.Minimum&&value<=item.Maximum,"ASSESSMENT_RATING_INVALID");
            Write("rating","forms",itemId,value);
            if(ratings.Count==RatingItems.Count)CompleteForms();
        }
        public void CompleteForms()
        { Need(safeBoundary()&&ProtectedComplete&&FormsStarted&&!FormsComplete&&ratings.Count==RatingItems.Count,"ASSESSMENT_FORMS_BLOCKED");Write("forms_completed","forms"); }
        public void RequireValidity()
        { Need(FinalDelayed&&ProtectedComplete&&FormsComplete,"ASSESSMENT_VALIDITY_BLOCKED"); }
        public ISpeechSlotAuthorization SpeechPermit(SlotContext context,SpeechBank bank,Func<bool> currentCue)
        {
            RequireValidity();Need(context.Item.TrialType=="speech"&&schedule.Blocks.SelectMany(b=>b.Items).Any(x=>x.TrialId==context.OpportunityId&&x.TrialType==context.Item.TrialType&&x.ContentId==context.Item.ContentId&&x.Phase=="validity")&&
                bank!=null&&bank.Reviewed&&!bank.Demo&&!schedule.Demo&&bank.Study==schedule.Study&&bank.Set==schedule.SetName&&
                bank.SpeechListSha256==schedule.SpeechListSha256&&currentCue!=null,"ASSESSMENT_SPEECH_SCOPE");
            return new SpeechAuthority(this,context.Item.ContentId,bank.ManifestSha256,currentCue);
        }
        sealed class SpeechAuthority : ISpeechSlotAuthorization
        {
            readonly AssessmentStages owner;readonly string speech,manifest;readonly Func<bool> cue;bool used;
            internal SpeechAuthority(AssessmentStages owner,string speech,string manifest,Func<bool> cue){this.owner=owner;this.speech=speech;this.manifest=manifest;this.cue=cue;}
            public bool Consume(string id,string hash,string list)
            {
                if(used||id!=speech||hash!=manifest||list!=owner.schedule.SpeechListSha256||!cue())return false;
                owner.RequireValidity();used=true;return true;
            }
        }
        public void BeginOptional()
        {
            Need(safeBoundary()&&schedule.Study=="B"&&schedule.Visit=="W4"&&FormsComplete&&ValidityComplete&&!OptionalStarted,"ASSESSMENT_OPTIONAL_BLOCKED");
            Write("optional_started","post_w4_optional");
        }
        public void RecordOptional(string kind,string itemId,string outcome)
        {
            Need(safeBoundary()&&OptionalStarted&&kind is ("optional_help" or "optional_execution")&&CanOptionalRecord(kind,itemId,outcome),"ASSESSMENT_OPTIONAL_BLOCKED");
            Write(kind,"post_w4_optional",itemId,null,outcome);
        }
        bool CanOptionalRecord(string kind,string item,string outcome)=>item!=null&&(outcome=="requested"?pendingOptionalKind==null:
            outcome is ("completed" or "failed" or "cancelled")&&pendingOptionalKind==kind&&pendingOptionalItem==item);
        public void CancelInterruptedOptional()
        {
            Need(safeBoundary()&&OptionalStarted&&OptionalRequestPending,"ASSESSMENT_OPTIONAL_BLOCKED");
            Write(pendingOptionalKind,"post_w4_optional",pendingOptionalItem,null,"cancelled");
        }
    }
}
