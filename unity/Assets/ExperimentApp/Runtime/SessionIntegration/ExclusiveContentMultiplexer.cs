using System;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
namespace AcousticVocab.SessionIntegration
{
    // Register cleanup before side effects that could throw. Partial creators
    // are disposed through this scope even when no factory is returned.
    public sealed class ModuleConstructionScope:IDisposable
    {
        readonly List<Action> cleanup=new List<Action>();readonly List<IDisposable> owned=new List<IDisposable>();bool closed;
        public void RegisterCleanup(Action action){if(closed||action==null)throw new SessionFault("SESSION_SCOPE_CLOSED");cleanup.Add(action);}
        public T Own<T>(T value)where T:IDisposable{if(value==null)throw new SessionFault("SESSION_MODULE_MISSING");if(!owned.Any(x=>ReferenceEquals(x,value))){RegisterCleanup(value.Dispose);owned.Add(value);}return value;}
        public void Dispose(){if(closed)return;closed=true;Exception first=null;for(int i=cleanup.Count-1;i>=0;i--)try{cleanup[i]();}catch(Exception e){first??=e;}cleanup.Clear();if(first!=null)throw new SessionFault("SESSION_MODULE_DISPOSE_FAILED");}
    }
    public sealed class ExclusiveContentMultiplexer:ISlotContentFactory,ISessionContentPump,ISlotStartPlan,IDisposable
    {
        readonly VisitSchedule schedule;readonly ISessionClock clock;readonly IReadOnlyDictionary<string,Func<ModuleConstructionScope,ISlotContentFactory>> routes;readonly Action<SlotContext> prepared;
        ISlotContentFactory current;ModuleConstructionScope scope;string block;double tailEnd,last=-1;bool failed,closed;FixedSlotEngine engine;
        public string ActiveBlock=>block;public bool Failed=>failed;public double RetainedTailEndMs=>tailEnd;
        public ExclusiveContentMultiplexer(VisitSchedule schedule,ISessionClock clock,IReadOnlyDictionary<string,Func<ModuleConstructionScope,ISlotContentFactory>> routes,Action<SlotContext> prepared=null)
        {
            this.schedule=schedule??throw new ArgumentNullException(nameof(schedule));this.clock=clock??throw new ArgumentNullException(nameof(clock));
            if(routes==null||schedule.Blocks.Any(b=>!routes.ContainsKey(b.Name)||routes[b.Name]==null)||routes.Count!=schedule.Blocks.Count)throw new SessionFault("SESSION_MODULE_ROUTES");
            this.routes=new Dictionary<string,Func<ModuleConstructionScope,ISlotContentFactory>>(routes);this.prepared=prepared;
        }
        double Now(){double n=clock.NowMs;if(double.IsNaN(n)||double.IsInfinity(n)||n<0||n<last){failed=true;throw new SessionFault("SESSION_MODULE_CLOCK");}last=n;return n;}
        void Need(bool value,string code){if(!value){failed=true;throw new SessionFault(code);}}
        // Called by the sole command owner after its durable resume request and
        // before ConfirmResume. It never advances or auto-resumes an engine.
        public void PrepareBlockAtBoundary(FixedSlotEngine value)
        {
            Need(!closed&&!failed&&value!=null&&value.NeedsOperatorConfirmation&&value.ScheduleSha256==schedule.Sha256&&value.PackageSha256==schedule.PackageSha256,"SESSION_MODULE_BOUNDARY");
            if(engine!=null)Need(ReferenceEquals(engine,value),"SESSION_MODULE_ENGINE");engine=value;
            double now=Now();Need(now>=tailEnd,"SESSION_MODULE_TAIL_ACTIVE");string next=value.CurrentBlock;Need(next!=null&&routes.ContainsKey(next),"SESSION_MODULE_BLOCK");
            if(current!=null&&block==next)return;
            var old=scope;current=null;scope=null;block=null;
            try{old?.Dispose();var candidate=new ModuleConstructionScope();try{var factory=routes[next](candidate);Need(factory is IDisposable,"SESSION_MODULE_LIFETIME");candidate.Own((IDisposable)factory);current=factory;scope=candidate;block=next;}catch{candidate.Dispose();throw;}}
            catch{failed=true;throw new SessionFault("SESSION_MODULE_CREATION_FAILED");}
        }
        void Available(){Need(!closed&&!failed&&current!=null&&engine!=null&&engine.Status==SessionState.Running&&engine.CurrentBlock==block,"SESSION_MODULE_UNPREPARED");}
        public double MinimumGapBeforeMs(SlotItem item,double baseOnsetMonoMs)
        {
            Available();double now=Now();Need(item!=null&&!double.IsNaN(baseOnsetMonoMs)&&!double.IsInfinity(baseOnsetMonoMs)&&baseOnsetMonoMs>=now,"SESSION_MODULE_STALE_ANCHOR");
            return current is ISlotStartPlan plan?plan.MinimumGapBeforeMs(item,baseOnsetMonoMs):0;
        }
        public ISlotContent Create(SlotItem item){Available();return new Content(this,current.Create(item)??throw new SessionFault("SESSION_MODULE_MISSING"));}
        public void Pump(){if(closed||failed)return;Now();if(current is ISessionContentPump pump)try{pump.Pump();}catch{failed=true;throw;}}
        public void Dispose(){if(closed)return;closed=true;var old=scope;scope=null;current=null;block=null;try{old?.Dispose();}catch{failed=true;throw;}}
        sealed class Content:ISlotContent
        {
            readonly ExclusiveContentMultiplexer owner;readonly ISlotContent inner;
            internal Content(ExclusiveContentMultiplexer owner,ISlotContent inner){this.owner=owner;this.inner=inner;}
            public void Prepare(SlotContext c){owner.Available();owner.prepared?.Invoke(c);inner.Prepare(c);}
            public SlotReadiness Readiness=>inner.Readiness;public bool ResetComplete=>inner.ResetComplete;
            public void RequestCue(SlotContext c,INovelSlotAuthorization p){owner.Available();owner.tailEnd=Math.Max(owner.tailEnd,c.EndMonoMs);inner.RequestCue(c,p);}
            public void OpenResponse(SlotContext c)=>inner.OpenResponse(c);public void CloseResponse(SlotContext c)=>inner.CloseResponse(c);public void RequestReset(SlotContext c)=>inner.RequestReset(c);public void Interrupt(string code)=>inner.Interrupt(code);
        }
    }
}
