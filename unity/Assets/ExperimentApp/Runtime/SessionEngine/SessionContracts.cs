using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Text.RegularExpressions;
using AcousticVocab.StudyAudio;

namespace AcousticVocab.SessionEngine
{
    public sealed class SessionFault : Exception
    {
        public string Code { get; }
        public SessionFault(string code) : base(code!=null && Regex.IsMatch(code,@"\A[A-Z][A-Z0-9_]{0,63}\z")?code:"SESSION_FAULT") { Code=Message; }
    }
    public enum ItemState { Loaded, Ready, CueRequested, ResponseOpen, Closed, Reset, Done }
    public enum SessionState { AwaitingOperator, Running, Paused, Stopped, Complete, Faulted }
    public enum AudibleStatus { NotRequested, Uncertain, ConfirmedAudible, ConfirmedNoOnset, NoCue }

    // An opaque content handle and timing contract. Hidden intended tuples and
    // randomization seeds never appear in this value or session status events.
    public sealed class SlotItem
    {
        public string TrialId { get; }
        public string TrialType { get; }
        public string ContentId { get; }
        public string Role { get; }
        public string Presentation { get; }
        public string Phase { get; }
        public bool Heldout { get; }
        public int SlotSeconds { get; }
        public int Plays { get; }
        public int Pass { get; }
        internal SlotItem(string id,string type,string content,string role,string presentation,string phase,bool heldout,int slot,int plays,int pass)
        { TrialId=id;TrialType=type;ContentId=content;Role=role;Presentation=presentation;Phase=phase;Heldout=heldout;SlotSeconds=slot;Plays=plays;Pass=pass; }
        internal SlotItem Retry(string newId) => new SlotItem(newId,TrialType,ContentId,Role,Presentation,Phase,Heldout,SlotSeconds,Plays,Pass);
        public bool Protected => Phase=="protected" || Phase=="pre_test" || Phase=="validity";
        public double ResponseOpensSeconds => TrialType=="atomic_lesson"?6:TrialType=="message_lesson"?8:0;
        public double ResponseClosesSeconds => TrialType=="atomic"?7:TrialType=="atomic_lesson"?13:TrialType=="message_lesson"?17:
            TrialType=="profile_menu" || TrialType=="atom_menu"?SlotSeconds:12;
    }
    public sealed class ScheduleBlock
    {
        public string Name { get; }
        public IReadOnlyList<SlotItem> Items { get; }
        internal ScheduleBlock(string name,SlotItem[] items) { Name=name;Items=Array.AsReadOnly((SlotItem[])items.Clone()); }
    }
    public sealed class VisitSchedule
    {
        public string Sha256 { get; }
        public string PackageSha256 { get; }
        public string PersonSlot { get; }
        public string Visit { get; }
        public bool Demo { get; }
        public IReadOnlyList<ScheduleBlock> Blocks { get; }
        internal VisitSchedule(string hash,string package,string person,string visit,bool demo,ScheduleBlock[] blocks)
        { Sha256=hash;PackageSha256=package;PersonSlot=person;Visit=visit;Demo=demo;Blocks=Array.AsReadOnly((ScheduleBlock[])blocks.Clone()); }
    }

    // All gates are independently provided by the integration. A rendered
    // neutral does not manufacture a backend reset acknowledgement.
    public readonly struct SlotReadiness
    {
        public readonly bool HashVerified,AudioPreloaded,ResetAcknowledged,RendererReady,PanelIdle,FocusOk,InputOk,ModeAcknowledged;
        public bool Ready => HashVerified && AudioPreloaded && ResetAcknowledged && RendererReady && PanelIdle && FocusOk && InputOk && ModeAcknowledged;
        public SlotReadiness(bool hash,bool preload,bool resetAck,bool renderer,bool panel,bool focus,bool input,bool modeAck)
        { HashVerified=hash;AudioPreloaded=preload;ResetAcknowledged=resetAck;RendererReady=renderer;PanelIdle=panel;FocusOk=focus;InputOk=input;ModeAcknowledged=modeAck; }
    }
    public readonly struct SlotContext
    {
        public SlotItem Item { get; }
        public double OnsetMonoMs { get; }
        public double EndMonoMs => OnsetMonoMs+Item.SlotSeconds*1000;
        public string RetryOf { get; }
        public string OpportunityId => RetryOf??Item.TrialId;
        public IReadOnlyList<string> AudioRequestIds { get; }
        public SlotContext(SlotItem item,double onset,string retryOf)
        {
            Item=item;OnsetMonoMs=onset;RetryOf=retryOf;
            var ids=new string[item.Plays];for(int i=0;i<ids.Length;i++)ids[i]=Guid.NewGuid().ToString("N");
            AudioRequestIds=Array.AsReadOnly(ids);
        }
    }
    // Content modules implement lessons/test/menu internals. The engine owns
    // order, timing, permission, durable exposure and fault/retry decisions.
    // These callbacks run only on the host main thread.
    public interface ISlotContent
    {
        void Prepare(SlotContext context);
        SlotReadiness Readiness { get; }
        // False when RequestReset is issued, true only after a new matching
        // acknowledgement and neutral renderer verification, never the cue's
        // earlier readiness receipt. Lesson modules may defer this until their
        // final scheduled demonstration/play has finished.
        bool ResetComplete { get; }
        void RequestCue(SlotContext context,INovelSlotAuthorization novelAuthorization);
        void OpenResponse(SlotContext context);
        void CloseResponse(SlotContext context);
        void RequestReset(SlotContext context);
        void Interrupt(string boundedCode);
    }
    public interface ISlotContentFactory { ISlotContent Create(SlotItem item); }
    // One owner pumps input deadlines, transport and retired presentation tails
    // before the engine evaluates a boundary. A factory multiplexer delegates
    // to its active modules here instead of relying on MonoBehaviour order.
    public interface ISessionContentPump { void Pump(); }
    // Optional verified timing plan. The base is the engine's proposed onset;
    // replay implementations derive extra delay from a stored ledger and an
    // explicitly installed session anchor, never live choice/UI state.
    public interface ISlotStartPlan { double MinimumGapBeforeMs(SlotItem item,double baseOnsetMonoMs); }
    public interface ISessionClock { double NowMs { get; } }
    public interface ISessionJournal
    {
        IReadOnlyList<SessionRecord> Records { get; }
        void Append(SessionRecord record);
    }
    public sealed class SessionRecord
    {
        public string Event { get; }
        public string ClockEpoch { get; }
        public string ScheduleSha256 { get; }
        public string TrialId { get; }
        public string RetryOf { get; }
        public int BlockIndex { get; }
        public int ItemIndex { get; }
        public double MonoMs { get; }
        public double? ScheduledOnsetMonoMs { get; }
        public ItemState? State { get; }
        public AudibleStatus AudibleStatus { get; }
        public bool ExposureConsumed { get; }
        public bool ResetOk { get; }
        public bool FocusOk { get; }
        public string TechnicalFaultCode { get; }
        public string ResponseCode { get; }
        public string EvidenceSha256 { get; }
        public string OpportunityId { get; }
        public IReadOnlyList<string> AudioRequestIds { get; }
        internal SessionRecord(string kind,string epoch,string hash,string trial,string retry,int block,int item,double mono,double? onset,ItemState? state,
            AudibleStatus audible,bool consumed,bool reset,bool focus,string fault,string response,string evidence=null,string opportunity=null,IEnumerable<string> audioRequests=null)
        { Event=kind;ClockEpoch=epoch;ScheduleSha256=hash;TrialId=trial;RetryOf=retry;BlockIndex=block;ItemIndex=item;MonoMs=mono;ScheduledOnsetMonoMs=onset;State=state;
          AudibleStatus=audible;ExposureConsumed=consumed;ResetOk=reset;FocusOk=focus;TechnicalFaultCode=fault;ResponseCode=response;EvidenceSha256=evidence;
          OpportunityId=opportunity;AudioRequestIds=new List<string>(audioRequests??Array.Empty<string>()).AsReadOnly(); }
    }
}
