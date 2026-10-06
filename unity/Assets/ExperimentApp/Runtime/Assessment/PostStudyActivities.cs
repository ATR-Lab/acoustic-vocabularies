using System;
using System.Text.RegularExpressions;
using AcousticVocab.ResponsePanel;
using AcousticVocab.Teaching;

namespace AcousticVocab.Assessment
{
    public interface IPostStudyDictionaryView { void Show(TeachingDisplay display);void Hide(); }
    // A provider must load the qualified #56/#66 recording, bind these hashes,
    // and report actual playback completion. A request is never completion.
    public interface IPostStudyExecution
    {
        bool QualifiedAndReady {get;}
        string PackageSha256 {get;}
        string ScheduleSha256 {get;}
        void Start(string target,string action,Action<string> completed);
        void Cancel();
    }
    public sealed class PostStudyActivities:IDisposable
    {
        readonly AssessmentStages stages;
        readonly TeachingCatalog catalog;
        readonly IPostStudyDictionaryView view;
        readonly IPostStudyExecution execution;
        readonly Action<string> fault;
        bool disposed,failed,busy;
        string activeRequest,activeItem,activeKind;
        public PostStudyActivities(AssessmentStages stages,TeachingCatalog catalog,IPostStudyDictionaryView view,IPostStudyExecution execution,Action<string> fault)
        {this.stages=stages??throw new ArgumentNullException(nameof(stages));this.catalog=catalog??throw new ArgumentNullException(nameof(catalog));this.view=view??throw new ArgumentNullException(nameof(view));this.execution=execution;this.fault=fault??throw new ArgumentNullException(nameof(fault));}
        void Ready()
        {if(disposed||failed||busy||!stages.OptionalStarted||stages.OptionalRequestPending)throw new AssessmentFault("ASSESSMENT_OPTIONAL_BLOCKED");stages.RequireSafeBoundary();}
        public void Consult(string atomId)
        {
            Ready();if(atomId==null||!Regex.IsMatch(atomId,@"\A[KQ]-[ar][1-4]\z"))throw new AssessmentFault("ASSESSMENT_OPTIONAL_ATOM");
            stages.RecordOptional("optional_help",atomId,"requested");activeKind="optional_help";activeItem=atomId;busy=true;
            try{var display=catalog.ReadPostStudyDictionaryAtom(atomId,stages.DictionaryPermit(atomId));view.Show(display);stages.RecordOptional("optional_help",atomId,"completed");activeKind=null;activeItem=null;busy=false;}
            catch{Fail("ASSESSMENT_OPTIONAL_HELP_FAILED");throw;}
        }
        public void Execute(string target,string action)
        {
            Ready();if(!PublicCommands.Legal(target,action)||execution==null||!execution.QualifiedAndReady||execution.PackageSha256!=catalog.PackageSha256||execution.ScheduleSha256!=catalog.ScheduleSha256)
                throw new AssessmentFault("ASSESSMENT_OPTIONAL_EXECUTION_UNQUALIFIED");
            string item="execute_"+target+"_"+action,request=Guid.NewGuid().ToString("N");
            stages.RecordOptional("optional_execution",item,"requested");activeKind="optional_execution";activeRequest=request;activeItem=item;busy=true;
            try{view.Hide();execution.Start(target,action,outcome=>Complete(request,outcome));}
            catch{Fail("ASSESSMENT_OPTIONAL_EXECUTION_FAILED");throw;}
        }
        void Complete(string request,string outcome)
        {
            if(disposed||failed||request!=activeRequest)return;
            try
            {
                if(outcome is not ("completed" or "failed" or "cancelled"))throw new AssessmentFault("ASSESSMENT_OPTIONAL_OUTCOME");
                stages.RecordOptional("optional_execution",activeItem,outcome);activeRequest=null;activeItem=null;activeKind=null;busy=false;
                if(outcome=="failed")Fail("ASSESSMENT_OPTIONAL_EXECUTION_FAILED");
            }
            catch{Fail("ASSESSMENT_OPTIONAL_LOG_FAILED");}
        }
        void Stop(){try{execution?.Cancel();}catch{}try{view.Hide();}catch{}}
        void Fail(string code)
        {
            if(failed)return;failed=true;Stop();
            if(busy&&activeKind!=null)try{stages.RecordOptional(activeKind,activeItem,"failed");}catch{}
            fault(code);
        }
        public void Dispose(){if(disposed)return;disposed=true;Stop();if(busy)fault("ASSESSMENT_OPTIONAL_INTERRUPTED");}
    }
}
