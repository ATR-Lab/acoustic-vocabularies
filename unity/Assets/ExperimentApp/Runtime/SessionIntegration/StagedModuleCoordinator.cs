using System;
using System.Collections.Generic;
using AcousticVocab.SessionEngine;
namespace AcousticVocab.SessionIntegration
{
    public interface IModulePreflight
    {
        void Pump();
        bool Ready{get;}
        ISlotContentFactory Commit(ModuleConstructionScope scope);
    }
    // Pump only prepares authority; ConfirmResume remains an explicit mailbox
    // command. Candidate resources belong to the mux capability from creation.
    public sealed class StagedModuleCoordinator:IDisposable
    {
        readonly FixedSlotEngine engine;readonly ExclusiveContentMultiplexer modules;
        readonly IReadOnlyDictionary<string,Func<ModuleConstructionScope,IModulePreflight>> routes;
        ExclusiveContentMultiplexer.BoundaryPreparation ticket;IModulePreflight candidate;
        bool failed,closed;
        public bool Ready=>!failed&&!closed&&engine.NeedsOperatorConfirmation&&candidate!=null&&candidate.Ready;
        public StagedModuleCoordinator(FixedSlotEngine engine,ExclusiveContentMultiplexer modules,IReadOnlyDictionary<string,Func<ModuleConstructionScope,IModulePreflight>> routes)
        {this.engine=engine??throw new ArgumentNullException(nameof(engine));this.modules=modules??throw new ArgumentNullException(nameof(modules));this.routes=routes??throw new ArgumentNullException(nameof(routes));}
        public void Pump()
        {
            if(closed||failed)return;
            try
            {
                if(engine.Status is SessionState.Stopped or SessionState.Complete or SessionState.Faulted){Cancel();return;}
                if(!engine.NeedsOperatorConfirmation)return;
                if(candidate==null&&modules.CanPrepare(engine))
                {
                    if(!routes.TryGetValue(engine.CurrentBlock,out var create))throw new SessionFault("SESSION_MODULE_ROUTES");
                    ticket=modules.BeginPreparation(engine);candidate=create(ticket.Scope)??throw new SessionFault("SESSION_PREFLIGHT_MISSING");
                }
                candidate?.Pump();
            }
            catch{failed=true;try{Cancel();}finally{engine.Fault("SESSION_PREFLIGHT_FAILED");}throw;}
        }
        // Forms/instructions may retain a staged but not-yet-committed view.
        // Keep its real control heartbeat alive without acquiring another lease.
        public void PumpPending()
        {
            if(closed||failed||candidate==null)return;
            try{candidate.Pump();}catch{failed=true;try{Cancel();}finally{engine.Fault("SESSION_PREFLIGHT_FAILED");}throw;}
        }
        public void CommitForResume(FixedSlotEngine value)
        {
            if(!ReferenceEquals(value,engine)||!Ready)throw new SessionFault("SESSION_PREFLIGHT_NOT_READY");
            try{modules.CommitPreparation(ticket,candidate.Commit);ticket=null;candidate=null;}
            catch{failed=true;throw;}
        }
        void Cancel(){var old=ticket;ticket=null;candidate=null;if(old!=null)modules.CancelPreparation(old);}
        public void Dispose(){if(closed)return;closed=true;Cancel();}
    }
}
