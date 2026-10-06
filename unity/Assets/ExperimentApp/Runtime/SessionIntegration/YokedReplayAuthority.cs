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
        internal YokedAnchorRecord(string epoch,string id,long sequence,string command,double now,double anchor,int lead,string ledger,string schedule)
        {ClockEpoch=epoch;OperatorRequestId=id;OperatorSequence=sequence;Command=command;RequestedMonoMs=now;AnchorMonoMs=anchor;LeadMs=lead;ActiveLedgerSha256=ledger;ActiveScheduleSha256=schedule;}
        public JObject ToJson()=>new JObject{["kind"]="yoked_anchor",["policy"]="operator_start_plus_lead",["clock_epoch"]=ClockEpoch,["operator_request_id"]=OperatorRequestId,["operator_sequence"]=OperatorSequence,["command"]=Command,["requested_mono_ms"]=RequestedMonoMs,["anchor_mono_ms"]=AnchorMonoMs,["lead_ms"]=LeadMs,["active_ledger_sha256"]=ActiveLedgerSha256,["active_schedule_sha256"]=ActiveScheduleSha256};
    }
    // No timer or UI callback can bind this authority. The joining owner calls
    // it only from the already-durable, hash/nonce-bound operator Resume hook.
    public sealed class YokedReplayAuthority
    {
        readonly int lead;readonly string epoch,ledger,schedule;readonly Func<double> clock;readonly Action<YokedAnchorRecord> persist;readonly Func<double,MenuReplaySequence> load;
        bool consumed;
        public MenuReplaySequence Replay{get;private set;}
        public YokedAnchorRecord Record{get;private set;}
        public YokedReplayAuthority(int leadMs,string clockEpoch,string ledgerSha256,string activeScheduleSha256,Func<double> clock,Action<YokedAnchorRecord> persist,Func<double,MenuReplaySequence> load)
        {
            if(leadMs<2000||leadMs>60000||!GuidId(clockEpoch)||!Hash(ledgerSha256)||!Hash(activeScheduleSha256)||clock==null||persist==null||load==null)throw new SessionFault("JOIN_YOKED_AUTHORITY");
            lead=leadMs;epoch=clockEpoch;ledger=ledgerSha256;schedule=activeScheduleSha256;this.clock=clock;this.persist=persist;this.load=load;
        }
        static bool GuidId(string value)=>value!=null&&Regex.IsMatch(value,@"\A[0-9a-f]{32}\z");
        static bool Hash(string value)=>value!=null&&Regex.IsMatch(value,@"\A[0-9a-f]{64}\z");
        public MenuReplaySequence BindForExplicitStart(string requestId,long sequence,string command)
        {
            if(consumed||!GuidId(requestId)||sequence<1||command is not ("start" or "resume"))throw new SessionFault("JOIN_YOKED_ANCHOR_CONSUMED");
            consumed=true;double now=clock(),anchor=now+lead;
            if(!double.IsFinite(now)||now<0||!double.IsFinite(anchor)||anchor<=now)throw new SessionFault("JOIN_YOKED_ANCHOR_CLOCK");
            var record=new YokedAnchorRecord(epoch,requestId,sequence,command,now,anchor,lead,ledger,schedule);
            persist(record);Record=record; // durable intent precedes asset load or scheduling
            Replay=load(anchor)??throw new SessionFault("JOIN_YOKED_REPLAY_MISSING");return Replay;
        }
    }
}
