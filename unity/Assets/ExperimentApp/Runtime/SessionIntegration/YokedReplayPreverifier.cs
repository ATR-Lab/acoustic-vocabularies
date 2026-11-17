using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using AcousticVocab.SelectionMenus;
using AcousticVocab.SessionEngine;

namespace AcousticVocab.SessionIntegration
{
    // Attempts 033/034: a single synchronous Preverify stalled the idle preflight
    // 423-455 ms. The same verification is staged so no frame carries it:
    //   1. main thread, one menu key per Pump: read and hash-verify that key's
    //      candidate audio (the package's own pinned checks);
    //   2. worker thread: the ledger file hash pin, canonical chain and record
    //      validation over those already-verified, immutable candidates;
    //   3. main thread, in order: every store selection receipt the ledger names
    //      is verified against the store, then the authority accepts the result.
    // The authority is not Preverified until step 3, so the preflight is not ready
    // and an operator Start cannot coincide with any of it. Every pin and refusal
    // is unchanged; a failure surfaces on the main thread from Pump.
    public sealed class YokedReplayPreverifier
    {
        readonly YokedReplayAuthority authority;readonly IReadOnlyList<string> keys;
        readonly Func<string,(MenuOption[] options,string meaning)> prepare;readonly Func<string,int,string,bool> receipt;
        readonly Func<MenuLedgerVerification,MenuReplaySequence> preload;readonly Func<string> materialInputs;
        readonly Dictionary<string,(MenuOption[] options,string meaning)> materials=new Dictionary<string,(MenuOption[],string)>(StringComparer.Ordinal);
        string materialsFor,inputsAtStart;Task<MenuReplaySequence> worker;ConcurrentQueue<(string key,int index,string receipt)> receipts;
        public int MaterialsPrepared=>materials.Count;
        public bool Running=>worker!=null;
        // prepare: one key's verified candidates and meaning ID (main thread);
        // receipt: the store's receipt check (main thread); preload: the pinned
        // Preload given a verification (runs on a worker); materialInputs: what
        // the prepared candidates depend on (the committed profile).
        public YokedReplayPreverifier(YokedReplayAuthority authority,IReadOnlyList<string> menuKeys,Func<string,(MenuOption[] options,string meaning)> prepare,
            Func<string,int,string,bool> receipt,Func<MenuLedgerVerification,MenuReplaySequence> preload,Func<string> materialInputs,Action accepted=null)
        {
            if(authority==null||menuKeys==null||menuKeys.Count==0||prepare==null||receipt==null||preload==null||materialInputs==null)throw new SessionFault("JOIN_YOKED_PREVERIFIER");
            this.authority=authority;keys=menuKeys.ToArray();this.prepare=prepare;this.receipt=receipt;this.preload=preload;this.materialInputs=materialInputs;this.accepted=accepted;
        }
        readonly Action accepted;
        // One bounded step per call. Returns true once the authority holds a
        // current pre-verified replay.
        public bool Pump()
        {
            if(authority.Preverified&&worker==null)return true;
            if(worker!=null)
            {
                if(!worker.IsCompleted)return false;
                var done=worker;var recorded=receipts;worker=null;receipts=null;
                if(done.IsFaulted){var error=done.Exception.GetBaseException();if(error is SessionFault)throw error;throw new SessionFault("JOIN_YOKED_PREVERIFY_FAILED");}
                if(done.IsCanceled)throw new SessionFault("JOIN_YOKED_PREVERIFY_FAILED");
                foreach(var row in recorded)if(!receipt(row.key,row.index,row.receipt))throw new SessionFault("MENU_LEDGER_RECEIPT");
                authority.Accept(done.Result,inputsAtStart);accepted?.Invoke();return authority.Preverified;
            }
            string forMaterials=materialInputs();
            if(forMaterials!=materialsFor){materials.Clear();materialsFor=forMaterials;}
            var next=keys.FirstOrDefault(k=>!materials.ContainsKey(k));
            if(next!=null)
            {
                var value=prepare(next);if(value.options==null||value.options.Length!=3||value.options.Any(x=>x==null))throw new SessionFault("MENU_LEDGER_ASSETS");
                materials.Add(next,(value.options.ToArray(),value.meaning));return false;
            }
            inputsAtStart=authority.CurrentInputs();var snapshot=new Dictionary<string,(MenuOption[] options,string meaning)>(materials,StringComparer.Ordinal);
            var queue=new ConcurrentQueue<(string,int,string)>();receipts=queue;
            var verification=new MenuLedgerVerification(k=>snapshot.TryGetValue(k,out var m)?m.options.ToArray():null,(k,i,r)=>{queue.Enqueue((k,i,r));return true;},k=>snapshot.TryGetValue(k,out var m)?m.meaning:null);
            worker=Task.Run(()=>preload(verification));return false;
        }
    }
}
