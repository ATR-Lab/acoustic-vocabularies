using System;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.ResponsePanel;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.Orientation
{
    public enum OrientationStage { NotStarted, Action, Target, Practice, Feedback, Reexplanation, RecordedOutcome, Fault }
    public enum EligibilityCode { PassFirst, PassSecond, Fail }
    public sealed class OrientationOutcome
    {
        public EligibilityCode Code { get; }
        public bool Passed => Code!=EligibilityCode.Fail;
        public bool EngineeringDraft { get; }
        public double RecordedMonoMs { get; }
        public IReadOnlyList<bool> FirstCorrect { get; }
        public IReadOnlyList<bool> SecondCorrect { get; }
        internal OrientationOutcome(EligibilityCode code,bool draft,double now,IEnumerable<bool> first,IEnumerable<bool> second)
        { Code=code;EngineeringDraft=draft;RecordedMonoMs=now;FirstCorrect=Array.AsReadOnly(first.ToArray());SecondCorrect=Array.AsReadOnly(second.ToArray()); }
    }
    // Silent, preallocation logic. No audio, packages, allocation lists or condition inputs.
    public sealed class OrientationFlow
    {
        readonly OrientationPlan plan;readonly Func<double> clock;readonly Action<JObject> persist;
        readonly List<bool> first=new List<bool>(),second=new List<bool>();
        double last=-1, demoStart;int card,index;bool demoComplete;
        string requestId;bool handling;
        public OrientationStage Stage { get; private set; }
        public int Attempt { get; private set; }=1;
        public int Reexplanations { get; private set; }
        public int ItemNumber => index+1;
        public MeaningCard CurrentCard => Stage==OrientationStage.Action?plan.Actions[card]:Stage==OrientationStage.Target?plan.Targets[card]:null;
        public PracticeItem CurrentItem => Stage==OrientationStage.Practice || Stage==OrientationStage.Feedback ? Items[index]:null;
        IReadOnlyList<PracticeItem> Items => Attempt==1?plan.FirstCheck:plan.SecondCheck;
        public bool? LastCorrect { get; private set; }
        public bool DemoComplete => demoComplete;
        public OrientationOutcome Outcome { get; private set; }
        // Necessary eligibility gate only; consent, apparatus/protocol authorization and allocation ownership remain outside this component.
        public bool EligibleOutcomeRecorded => Stage==OrientationStage.RecordedOutcome && Outcome?.Passed==true && !Outcome.EngineeringDraft;
        public event Action<OrientationOutcome> OutcomeRecorded;
        public OrientationFlow(OrientationPlan plan,Func<double> clock,Action<JObject> durableSink)
        { this.plan=plan??throw new ArgumentNullException(nameof(plan));this.clock=clock??throw new ArgumentNullException(nameof(clock));persist=durableSink??throw new ArgumentNullException(nameof(durableSink)); }
        double Now() { double n=clock();if(double.IsNaN(n)||double.IsInfinity(n)||n<0||n<last) { Stage=OrientationStage.Fault;throw new OrientationFault("ORIENTATION_CLOCK"); }return last=n; }
        void Write(string code,double now,JObject payload=null)
        {
            var row=payload??new JObject();row["event"]=code;row["mono_ms"]=now;row["attempt"]=Attempt;row["engineering_draft"]=plan.EngineeringDraft;
            try { persist((JObject)row.DeepClone()); } catch { Stage=OrientationStage.Fault;throw new OrientationFault("ORIENTATION_DURABLE_WRITE_FAILED"); }
        }
        public void Start()
        {
            if(Stage!=OrientationStage.NotStarted) throw new OrientationFault("ORIENTATION_ALREADY_STARTED");
            Write("orientation_started",Now());card=0;Stage=OrientationStage.Action;BeginAction();
        }
        void BeginAction() { demoComplete=false;demoStart=Now();Write("action_screen",demoStart,new JObject { ["action"]=plan.Actions[card].Id,["kinematic_visualization"]=true }); }
        public void CompleteDemo(string action,double observedDurationMs,double nominalDurationMs,double frameToleranceMs)
        {
            if(Stage!=OrientationStage.Action || demoComplete || action!=CurrentCard.Id) throw new OrientationFault("ORIENTATION_DEMO_STATE");
            double now=Now();
            bool finite=new[]{observedDurationMs,nominalDurationMs,frameToleranceMs}.All(x=>!double.IsNaN(x)&&!double.IsInfinity(x));
            if(!finite || nominalDurationMs<=0 || frameToleranceMs<0 || frameToleranceMs>100 || observedDurationMs<nominalDurationMs || observedDurationMs-nominalDurationMs>frameToleranceMs || now-demoStart<nominalDurationMs)
            { Fault("ORIENTATION_DEMO_DURATION");return; }
            Write("kinematic_demo_completed",now,new JObject { ["action"]=action,["observed_duration_ms"]=observedDurationMs,["nominal_duration_ms"]=nominalDurationMs,["tolerance_ms"]=frameToleranceMs });demoComplete=true;
        }
        public void Next()
        {
            if(handling) throw new OrientationFault("ORIENTATION_REENTRANT");
            double now=Now();
            switch(Stage)
            {
                case OrientationStage.Action:
                    if(!demoComplete) throw new OrientationFault("ORIENTATION_DEMO_REQUIRED");
                    if(++card<8) BeginAction();else { card=0;Stage=OrientationStage.Target;Write("target_screen",now,new JObject { ["target"]=CurrentCard.Id }); }break;
                case OrientationStage.Target:
                    if(++card<8) Write("target_screen",now,new JObject { ["target"]=CurrentCard.Id });else { index=0;Stage=OrientationStage.Practice;requestId=null; }break;
                case OrientationStage.Feedback:
                    if(++index<8) { Stage=OrientationStage.Practice;requestId=null; }
                    else EndCheck(now);break;
                case OrientationStage.Reexplanation:
                    if(Reexplanations!=0 || Attempt!=1) throw new OrientationFault("ORIENTATION_REEXPLANATION_LIMIT");
                    Reexplanations=1;Attempt=2;card=0;Write("standard_reexplanation",now);Stage=OrientationStage.Action;BeginAction();break;
                default:throw new OrientationFault("ORIENTATION_NEXT_UNAVAILABLE");
            }
        }
        public PanelRequest OpenPractice()
        {
            if(Stage!=OrientationStage.Practice || requestId!=null) throw new OrientationFault("ORIENTATION_PRACTICE_STATE");
            double now=Now();requestId="orientation-"+Attempt+"-"+index;
            Write("practice_open",now,new JObject { ["item_id"]=CurrentItem.Id,["trial_id"]=requestId,["ordinal"]=index+1 });
            return new PanelRequest(requestId,PanelMode.Practice,PanelRole.Command,now,plan.PracticeWindowMs);
        }
        public void Respond(PanelResponse response)
        {
            if(handling || Stage!=OrientationStage.Practice || response==null || requestId==null || response.Request.TrialId!=requestId || response.Request.Mode!=PanelMode.Practice || response.Request.Role!=PanelRole.Command)
                throw new OrientationFault("ORIENTATION_RESPONSE_STATE");
            handling=true;
            try
            {
                double now=Now();bool correct=response.Code==ResponseCode.Commit && response.Target==CurrentItem.Target && response.Action==CurrentItem.Action;
                Write("practice_response",now,new JObject { ["item_id"]=CurrentItem.Id,["ordinal"]=index+1,["trial_id"]=requestId,["response_code"]=response.Code.ToString(),
                    ["response_target"]=response.Target,["response_action"]=response.Action,["selected_target"]=response.SelectedTarget,["selected_action"]=response.SelectedAction,["response_mono_ms"]=response.ResponseMonoMs,["correct"]=correct });
                (Attempt==1?first:second).Add(correct);LastCorrect=correct;Stage=OrientationStage.Feedback;
            }
            finally { handling=false; }
        }
        void EndCheck(double now)
        {
            var values=Attempt==1?first:second;Write("check_completed",now,new JObject { ["correct"]=values.Count(x=>x),["total"]=values.Count });
            if(values.Count!=8) { Fault("ORIENTATION_CHECK_INCOMPLETE");return; }
            if(values.Any(x=>!x) && Attempt==1) { Stage=OrientationStage.Reexplanation;return; }
            var code=values.All(x=>x)?Attempt==1?EligibilityCode.PassFirst:EligibilityCode.PassSecond:EligibilityCode.Fail;
            var result=new OrientationOutcome(code,plan.EngineeringDraft,now,first,second);
            Write("eligibility_outcome",now,new JObject { ["outcome"]=code.ToString(),["first_correct"]=new JArray(first),["second_correct"]=new JArray(second),["reexplanations"]=Reexplanations,["preallocation"]=true,["learning_result"]=false });
            Outcome=result;Stage=OrientationStage.RecordedOutcome;
            try { OutcomeRecorded?.Invoke(result); } catch { Stage=OrientationStage.Fault;throw new OrientationFault("ORIENTATION_OUTCOME_DELIVERY_FAILED"); }
        }
        public void Fault(string code)
        {
            if(Stage==OrientationStage.Fault)return;Stage=OrientationStage.Fault;
            Write("orientation_fault",Now(),new JObject { ["code"]=OrientationPlan.Id(new JValue(code)) });
        }
    }
}
