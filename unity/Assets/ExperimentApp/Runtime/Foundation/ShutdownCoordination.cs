using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.Foundation
{
    // How one background worker had ended when quit stopped waiting for it.
    public sealed class WorkerOutcome
    {
        public string Name { get; }
        public string Outcome { get; }
        public string FaultType { get; }
        public double WaitedMs { get; }
        internal WorkerOutcome(string name,string outcome,string faultType,double waitedMs){Name=name;Outcome=outcome;FaultType=faultType;WaitedMs=waitedMs;}
        public JObject ToJson()=>new JObject{["worker"]=Name,["outcome"]=Outcome,["fault_type"]=FaultType==null?JValue.CreateNull():new JValue(FaultType),["waited_ms"]=WaitedMs};
    }

    // Background workers that can still own sockets, timers or files when the
    // process quits. Owners still cancel and dispose them; the ledger only lets
    // one quit coordinator bound and record how each worker actually ended.
    public sealed class WorkerLedger
    {
        public static readonly WorkerLedger Process=new WorkerLedger();
        public const int Capacity=64;
        sealed class Entry{internal string Name;internal Task Task;}
        readonly object gate=new object();
        readonly List<Entry> entries=new List<Entry>();
        int untracked;
        public void Track(string name,Task worker)
        {
            if(worker==null)throw new ArgumentNullException(nameof(worker));
            lock(gate)
            {
                // A long visit creates one control client per staged block.
                // At capacity forget finished workers, never live ones.
                if(entries.Count>=Capacity)entries.RemoveAll(x=>x.Task.IsCompleted);
                if(entries.Count>=Capacity){untracked++;return;}
                entries.Add(new Entry{Name=ShutdownBreadcrumbs.Code(name,"worker"),Task=worker});
            }
        }
        public int Count{get{lock(gate)return entries.Count;}}
        public int Untracked{get{lock(gate)return untracked;}}
        // Waits at most budgetMs in total, never per worker. A worker still
        // running at the deadline is recorded as timed_out and left to the
        // runtime; its eventual fault is observed so it cannot surface later.
        public IReadOnlyList<WorkerOutcome> Drain(int budgetMs)
        {
            if(budgetMs<0||budgetMs>5000)throw new ArgumentOutOfRangeException(nameof(budgetMs));
            Entry[] snapshot;lock(gate){snapshot=entries.ToArray();entries.Clear();}
            var watch=Stopwatch.StartNew();var results=new List<WorkerOutcome>(snapshot.Length);
            foreach(var entry in snapshot)
            {
                double started=watch.Elapsed.TotalMilliseconds;
                int remaining=(int)Math.Max(0,Math.Floor(budgetMs-started));
                bool done=entry.Task.IsCompleted;
                if(!done&&remaining>0)
                    try{done=((IAsyncResult)entry.Task).AsyncWaitHandle.WaitOne(remaining);}catch(ObjectDisposedException){done=entry.Task.IsCompleted;}
                double waited=watch.Elapsed.TotalMilliseconds-started;
                if(!done)
                {
                    _=entry.Task.ContinueWith(t=>{_=t.Exception;},CancellationToken.None,TaskContinuationOptions.ExecuteSynchronously,TaskScheduler.Default);
                    results.Add(new WorkerOutcome(entry.Name,"timed_out",null,waited));continue;
                }
                var fault=entry.Task.Exception?.GetBaseException();
                string outcome=entry.Task.Status==TaskStatus.RanToCompletion?"completed":entry.Task.IsCanceled?"cancelled":"faulted";
                results.Add(new WorkerOutcome(entry.Name,outcome,fault==null?null:ShutdownBreadcrumbs.Code(fault.GetType().Name,"Exception"),waited));
            }
            return results;
        }
    }

    // Quit-time stage markers. Each line goes to the Unity native log and,
    // once a private directory is opened, to a write-through file, so the last
    // completed stage survives a later native fault. Bounded codes only: no
    // paths, endpoints, payloads or exception text.
    public static class ShutdownBreadcrumbs
    {
        static readonly object gate=new object();
        static FileStream file;
        static long sequence;
        static readonly int processId=System.Diagnostics.Process.GetCurrentProcess().Id;
        public const string Prefix="SHUTDOWN_BREADCRUMB ";
        static double Now=>(double)Stopwatch.GetTimestamp()/Stopwatch.Frequency*1000;
        internal static string Code(string value,string fallback)=>value!=null&&Regex.IsMatch(value,@"\A[A-Za-z][A-Za-z0-9_]{0,63}\z")?value:fallback;
        public static void Open(string privateDirectory)
        {
            lock(gate)
            {
                if(file!=null)return;
                Directory.CreateDirectory(privateDirectory);
                file=new FileStream(Path.Combine(privateDirectory,"shutdown-"+Guid.NewGuid().ToString("N")+".local.jsonl"),FileMode.CreateNew,FileAccess.Write,FileShare.Read,4096,FileOptions.WriteThrough);
            }
        }
        public static void Stage(string stage,JObject detail=null)
        {
            JObject record;lock(gate)record=new JObject{["event"]="shutdown_stage",["sequence"]=sequence++,["stage"]=Code(stage,"unknown"),
                ["local_mono_ms"]=Now,["managed_thread"]=Thread.CurrentThread.ManagedThreadId,["process_id"]=processId};
            if(detail!=null)record["detail"]=detail.DeepClone();
            string line=record.ToString(Formatting.None);
            UnityEngine.Debug.Log(Prefix+line);
            lock(gate)
            {
                if(file==null)return;
                try{byte[] bytes=Encoding.UTF8.GetBytes(line+"\n");file.Write(bytes,0,bytes.Length);file.Flush(true);}
                catch{try{file.Dispose();}catch{}file=null;}
            }
        }
        public static void Close(){lock(gate){if(file==null)return;try{file.Flush(true);}catch{}file.Dispose();file=null;}}
    }

    // One owner-ordered, run-once quit sequence. Every stage runs even if an
    // earlier stage throws. The only wait is the final drain, bounded in total;
    // it never suppresses or replaces the process exit code.
    public sealed class QuitCoordinator
    {
        public const int DefaultWorkerBudgetMs=500;
        readonly List<KeyValuePair<string,Action>> stages=new List<KeyValuePair<string,Action>>();
        readonly WorkerLedger ledger;readonly int budgetMs;readonly Action<string,JObject> breadcrumb;
        public bool Ran{get;private set;}
        public QuitCoordinator(WorkerLedger ledger,int workerBudgetMs,Action<string,JObject> breadcrumb)
        {
            if(workerBudgetMs<0||workerBudgetMs>2000)throw new ArgumentOutOfRangeException(nameof(workerBudgetMs));
            this.ledger=ledger??throw new ArgumentNullException(nameof(ledger));budgetMs=workerBudgetMs;this.breadcrumb=breadcrumb??throw new ArgumentNullException(nameof(breadcrumb));
        }
        public void Add(string name,Action stage)
        {if(Ran||stage==null)throw new InvalidOperationException("QUIT_STAGE_REFUSED");stages.Add(new KeyValuePair<string,Action>(ShutdownBreadcrumbs.Code(name,"stage"),stage));}
        void Crumb(string stage,JObject detail){try{breadcrumb(stage,detail);}catch{}}
        static string FaultCode(Exception error)=>Regex.IsMatch(error.Message??"",@"\A[A-Z][A-Z0-9_]{0,63}\z")?error.Message:ShutdownBreadcrumbs.Code(error.GetType().Name,"Exception");
        public IReadOnlyList<WorkerOutcome> Run(string trigger)
        {
            if(Ran)return Array.Empty<WorkerOutcome>();Ran=true;
            var watch=Stopwatch.StartNew();
            Crumb("quit_begin",new JObject{["trigger"]=ShutdownBreadcrumbs.Code(trigger,"unknown"),["stages"]=stages.Count});
            foreach(var stage in stages)
            {
                Crumb("stage_begin",new JObject{["stage"]=stage.Key});
                try{stage.Value();Crumb("stage_end",new JObject{["stage"]=stage.Key,["succeeded"]=true,["elapsed_ms"]=watch.Elapsed.TotalMilliseconds});}
                catch(Exception error){Crumb("stage_end",new JObject{["stage"]=stage.Key,["succeeded"]=false,["fault"]=FaultCode(error),["elapsed_ms"]=watch.Elapsed.TotalMilliseconds});}
            }
            Crumb("worker_drain_begin",new JObject{["budget_ms"]=budgetMs,["tracked"]=ledger.Count,["untracked"]=ledger.Untracked});
            IReadOnlyList<WorkerOutcome> outcomes;
            try{outcomes=ledger.Drain(budgetMs);}
            catch(Exception error){Crumb("worker_drain_failed",new JObject{["fault"]=FaultCode(error)});outcomes=Array.Empty<WorkerOutcome>();}
            foreach(var outcome in outcomes)Crumb("worker_outcome",outcome.ToJson());
            Crumb("quit_ready",new JObject{["completed"]=outcomes.Count(x=>x.Outcome=="completed"),["cancelled"]=outcomes.Count(x=>x.Outcome=="cancelled"),
                ["faulted"]=outcomes.Count(x=>x.Outcome=="faulted"),["timed_out"]=outcomes.Count(x=>x.Outcome=="timed_out"),["elapsed_ms"]=watch.Elapsed.TotalMilliseconds});
            return outcomes;
        }
    }
}
