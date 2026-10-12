using System;
using System.Text.RegularExpressions;
using AcousticVocab.SelectionMenus;
using AcousticVocab.SessionEngine;
using Newtonsoft.Json.Linq;
namespace AcousticVocab.SessionIntegration
{
    public sealed class YokedAnchorRecord
    {
        public string ClockEpoch{get;}public string OperatorRequestId{get;}public long OperatorSequence{get;}public string Command{get;}
        public double RequestedMonoMs{get;}public double AnchorMonoMs{get;}public int LeadMs{get;}
        public string ActiveLedgerSha256{get;}public string ActiveScheduleSha256{get;}
        public string Policy{get;}public System.Collections.Generic.IReadOnlyList<string> MenuKeys{get;}
        internal YokedAnchorRecord(string epoch,string id,long sequence,string command,double now,double anchor,int lead,string ledger,string schedule,string policy="operator_start_plus_lead",System.Collections.Generic.IReadOnlyList<string> keys=null)
        {ClockEpoch=epoch;OperatorRequestId=id;OperatorSequence=sequence;Command=command;RequestedMonoMs=now;AnchorMonoMs=anchor;LeadMs=lead;ActiveLedgerSha256=ledger;ActiveScheduleSha256=schedule;Policy=policy;MenuKeys=keys;}
        public JObject ToJson()
        {
            var value=new JObject{["kind"]="yoked_anchor",["policy"]=Policy,["clock_epoch"]=ClockEpoch,["operator_request_id"]=OperatorRequestId,["operator_sequence"]=OperatorSequence,["command"]=Command,["requested_mono_ms"]=RequestedMonoMs,["anchor_mono_ms"]=AnchorMonoMs,["lead_ms"]=LeadMs,["active_ledger_sha256"]=ActiveLedgerSha256,["active_schedule_sha256"]=ActiveScheduleSha256};
            if(MenuKeys!=null)value["menu_keys"]=new JArray(MenuKeys);return value;
        }
    }
    // No timer or UI callback can bind this authority. The joining owner calls
    // it only from the already-durable, hash/nonce-bound operator Resume hook.
    public sealed class YokedReplayAuthority
    {
        readonly int lead;readonly string epoch,ledger,schedule;readonly Func<double> clock;readonly Action<YokedAnchorRecord> persist;
        readonly Func<MenuReplaySequence> preverify;readonly Func<string> inputs;readonly Func<DateTimeOffset> utcNow;
        bool consumed;string preparedInputs;
        public MenuReplaySequence Replay{get;private set;}
        // Fully verified (ledger chain, candidate audio, store receipts) during the
        // idle, unexposed preflight; unanchored until the operator Start.
        public MenuReplaySequence Prepared{get;private set;}
        public int Verifications{get;private set;}
        public YokedAnchorRecord Record{get;private set;}
        readonly System.Collections.Generic.List<YokedAnchorRecord> blockRecords=new System.Collections.Generic.List<YokedAnchorRecord>();
        public System.Collections.Generic.IReadOnlyList<YokedAnchorRecord> BlockRecords=>blockRecords.AsReadOnly();
        // preverify: the full, hash-pinned Preload; inputs: a cheap fingerprint of
        // every input that could change the verification (pinned ledger file,
        // store state); utcNow: the replay age window is enforced again at Start.
        public YokedReplayAuthority(int leadMs,string clockEpoch,string ledgerSha256,string activeScheduleSha256,Func<double> clock,Action<YokedAnchorRecord> persist,
            Func<MenuReplaySequence> preverify,Func<string> inputs,Func<DateTimeOffset> utcNow)
        {
            if(leadMs<2000||leadMs>60000||!GuidId(clockEpoch)||!Hash(ledgerSha256)||!Hash(activeScheduleSha256)||clock==null||persist==null||preverify==null||inputs==null||utcNow==null)throw new SessionFault("JOIN_YOKED_AUTHORITY");
            lead=leadMs;epoch=clockEpoch;ledger=ledgerSha256;schedule=activeScheduleSha256;this.clock=clock;this.persist=persist;this.preverify=preverify;this.inputs=inputs;this.utcNow=utcNow;
        }
        static bool GuidId(string value)=>value!=null&&Regex.IsMatch(value,@"\A[0-9a-f]{32}\z");
        static bool Hash(string value)=>value!=null&&Regex.IsMatch(value,@"\A[0-9a-f]{64}\z");
        bool Fresh()
        {
            try{var now=utcNow();return Prepared!=null&&preparedInputs!=null&&inputs()==preparedInputs&&now>=Prepared.CreatedUtc&&now-Prepared.CreatedUtc<=TimeSpan.FromHours(24);}
            catch{return false;}
        }
        // True while the pre-verified sequence still matches every pinned input.
        public bool Preverified=>!consumed&&Fresh();
        // Maintainer decision (#247): all heavy verification happens here, at the
        // idle preflight. A changed input invalidates it and it is redone here;
        // a pinned-hash mismatch is refused here, before any Start.
        public void Preverify()
        {
            if(consumed)throw new SessionFault("JOIN_YOKED_ANCHOR_CONSUMED");
            if(Fresh())return;
            preparedInputs=null;string before=inputs();
            Accept(preverify(),before);
        }
        // The current input fingerprint, captured when a staged verification
        // starts so a change during it leaves the result stale.
        public string CurrentInputs()=>inputs();
        // Completes a verification run elsewhere (YokedReplayPreverifier stages it
        // across frames and a worker thread); same checks as Preverify.
        public void Accept(MenuReplaySequence fresh,string inputsAtStart)
        {
            if(consumed)throw new SessionFault("JOIN_YOKED_ANCHOR_CONSUMED");
            preparedInputs=null;if(fresh==null)throw new SessionFault("JOIN_YOKED_REPLAY_MISSING");
            if(fresh.LedgerSha256!=ledger||fresh.Anchored||inputsAtStart==null)throw new SessionFault("JOIN_YOKED_REPLAY_BINDING");
            Verifications++;if(Prepared==null){Prepared=fresh;if(diagnostic!=null)Prepared.AnchorRefused+=diagnostic;}else Prepared.AdoptVerified(fresh);preparedInputs=inputsAtStart;
        }
        Action<Newtonsoft.Json.Linq.JObject> diagnostic;
        // Bounded refusal evidence from the replay (clause and compared values).
        public void ReportRefusalsTo(Action<Newtonsoft.Json.Linq.JObject> sink)
        {if(sink==null||diagnostic!=null)throw new SessionFault("JOIN_YOKED_AUTHORITY");diagnostic=sink;if(Prepared!=null)Prepared.AnchorRefused+=sink;}
        // Start/Commit does no ledger or audio work: it checks the pre-verified
        // inputs are unchanged, records the durable anchor, then anchors.
        public MenuReplaySequence BindForExplicitStart(string requestId,long sequence,string command)
        {
            if(consumed||!GuidId(requestId)||sequence<1||command is not ("start" or "resume"))throw new SessionFault("JOIN_YOKED_ANCHOR_CONSUMED");
            if(!Fresh())throw new SessionFault("JOIN_YOKED_PREVERIFY_STALE");
            consumed=true;double now=clock(),anchor=now+lead;
            if(!double.IsFinite(now)||now<0||!double.IsFinite(anchor)||anchor<=now)throw new SessionFault("JOIN_YOKED_ANCHOR_CLOCK");
            var record=new YokedAnchorRecord(epoch,requestId,sequence,command,now,anchor,lead,ledger,schedule);
            persist(record);Record=record; // durable intent precedes any scheduling
            Prepared.AnchorStart(anchor);Replay=Prepared;return Replay;
        }
        // Each later menu block is anchored at its own explicit boundary resume
        // plus the same lead; the active operator's pause is never replayed.
        // A block with a planned menu keeps its anchor (no record is written).
        public bool AnchorBlockAtResume(string requestId,long sequence,string command,System.Collections.Generic.IReadOnlyList<string> menuKeys)
        {
            if(Replay==null||!GuidId(requestId)||sequence<1||command is not ("start" or "resume"))throw new SessionFault("JOIN_YOKED_BLOCK_ANCHOR");
            if(!Replay.BlockAnchorable(menuKeys))return false;
            double now=clock(),anchor=now+lead;
            if(!double.IsFinite(now)||now<0||!double.IsFinite(anchor)||anchor<=now)throw new SessionFault("JOIN_YOKED_ANCHOR_CLOCK");
            var record=new YokedAnchorRecord(epoch,requestId,sequence,command,now,anchor,lead,ledger,schedule,"block_resume_plus_lead",new System.Collections.Generic.List<string>(menuKeys).AsReadOnly());
            persist(record);blockRecords.Add(record); // durable intent precedes the new schedule
            return Replay.AnchorBlock(menuKeys,anchor);
        }
    }
}
