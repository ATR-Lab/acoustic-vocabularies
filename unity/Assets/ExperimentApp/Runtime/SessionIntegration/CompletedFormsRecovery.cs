using System;
using AcousticVocab.Assessment;
using AcousticVocab.SessionEngine;
namespace AcousticVocab.SessionIntegration
{
    // A completed visit can resume only its remaining durable ratings. This
    // owner has no audio, content-factory, backend-mode or engine-tick surface.
    public sealed class CompletedFormsRecovery:IDisposable
    {
        readonly FixedSlotEngine engine;readonly Func<AssessmentStages,IDisposable> present;
        IDisposable view;bool failed,closed;
        public AssessmentStages Stages{get;}
        public bool Complete=>!failed&&!closed&&Stages.FormsComplete;
        public CompletedFormsRecovery(VisitSchedule schedule,FixedSlotEngine engine,ISessionClock clock,
            ISessionJournal session,IAssessmentJournal journal,bool wordingReviewed,Func<AssessmentStages,IDisposable> present)
        {
            if(engine==null||schedule==null||engine.Status!=SessionState.Complete||engine.ScheduleSha256!=schedule.Sha256||
                engine.PackageSha256!=schedule.PackageSha256||!wordingReviewed||present==null)throw new SessionFault("JOIN_FORMS_RECOVERY_BINDING");
            this.engine=engine;this.present=present;
            Stages=new AssessmentStages(schedule,session,journal,clock,()=>!closed&&!failed&&engine.Status==SessionState.Complete,wordingReviewed);
            if(!Stages.ProtectedComplete)throw new SessionFault("JOIN_FORMS_RECOVERY_HISTORY");
        }
        public void Pump()
        {
            if(closed||failed||engine.Status!=SessionState.Complete)throw new SessionFault("JOIN_FORMS_RECOVERY_UNAVAILABLE");
            if(Stages.FormsComplete||view!=null)return;
            try{view=present(Stages)??throw new SessionFault("JOIN_FORMS_RECOVERY_VIEW");}
            catch{failed=true;throw;}
        }
        public void Dispose(){if(closed)return;closed=true;var former=view;view=null;former?.Dispose();}
    }
}
