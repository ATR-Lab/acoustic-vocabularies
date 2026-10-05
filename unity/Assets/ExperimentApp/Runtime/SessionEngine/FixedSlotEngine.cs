using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using AcousticVocab.StudyAudio;

namespace AcousticVocab.SessionEngine
{
    public sealed class FixedSlotEngine
    {
        const double LeadMs=750,MinimumLeadMs=150;
        readonly VisitSchedule schedule;
        readonly ISessionClock clock;
        readonly ISessionJournal journal;
        readonly ISlotContentFactory factory;
        readonly string epoch=Guid.NewGuid().ToString("N");
        readonly HashSet<string> completed=new HashSet<string>(StringComparer.Ordinal);
        readonly Dictionary<string,SessionRecord> latest=new Dictionary<string,SessionRecord>(StringComparer.Ordinal);
        readonly List<SlotItem> retries=new List<SlotItem>();
        readonly Dictionary<string,string> retryOf=new Dictionary<string,string>(StringComparer.Ordinal);
        readonly HashSet<string> retryGranted=new HashSet<string>(StringComparer.Ordinal);
        double lastNow=-1,nextOnset,tailEnd;
        SessionState? stateAfterTail;
        int blockIndex,itemIndex,retryIndex;
        bool pausedRequested,stopRequested,inRetry,consumed,opened,closed,transitioning,pumping,faulting;
        string fault,response;
        AudibleStatus audible;
        ISlotContent content;
        SlotContext context;
        NovelPermit permit;
        public SessionState Status { get; private set; }=SessionState.AwaitingOperator;
        public ItemState? CurrentState { get; private set; }
        public string CurrentTrialId => content==null?null:context.Item.TrialId;
        public string CurrentBlock => blockIndex<schedule.Blocks.Count?schedule.Blocks[blockIndex].Name:null;
        public bool ExposureConsumed => content!=null && consumed;
        public int CompletedOpportunities => completed.Count;
        public string ScheduleSha256 => schedule.Sha256;
        public string PackageSha256 => schedule.PackageSha256;
        public IReadOnlyList<int> CompletedCounts => Array.AsReadOnly(schedule.Blocks.Select(block => block.Items.Count(item => completed.Contains(item.TrialId))).ToArray());
        public bool NeedsOperatorConfirmation => Status==SessionState.AwaitingOperator || Status==SessionState.Paused;
        public FixedSlotEngine(VisitSchedule schedule,ISessionClock clock,ISessionJournal journal,ISlotContentFactory factory)
        {
            this.schedule=schedule??throw new ArgumentNullException(nameof(schedule));this.clock=clock??throw new ArgumentNullException(nameof(clock));
            this.journal=journal??throw new ArgumentNullException(nameof(journal));this.factory=factory??throw new ArgumentNullException(nameof(factory));
            Recover();
        }
        double Now()
        {
            double now=clock.NowMs;
            if(double.IsNaN(now)||double.IsInfinity(now)||now<0||now<lastNow) { Emergency("SESSION_CLOCK_INVALID");throw new SessionFault("SESSION_CLOCK_INVALID"); }
            lastNow=now;return now;
        }
        void Recover()
        {
            // No journal data reaches a participant view. Intent records are
            // conservative: a crash between a cue intent and its result still
            // consumes the exposure. Never infer no-onset from a missing event.
            foreach(var record in journal.Records)
            {
                if(record.ScheduleSha256!=schedule.Sha256) throw new SessionFault("SESSION_SCHEDULE_MISMATCH");
                if(record.TrialId!=null) latest[record.TrialId]=record;
                if(record.Event=="retry_queued") { retryGranted.Add(record.RetryOf);retryOf[record.TrialId]=record.RetryOf; }
                if(record.State==ItemState.Done && record.TrialId!=null) completed.Add(record.TrialId);
            }
            foreach(var pair in latest)
                if(pair.Value.State>=ItemState.CueRequested) completed.Add(pair.Key);
            // Reconstruct only retries which were durably queued but never
            // requested. The originals retain their unsuccessful opportunity.
            foreach(var pair in retryOf)
            {
                var original=schedule.Blocks.SelectMany(x=>x.Items).SingleOrDefault(x=>x.TrialId==pair.Value);
                if(original==null) throw new SessionFault("SESSION_RETRY_JOURNAL_INVALID");
                if(!completed.Contains(pair.Key) && RetryEvidenceAllows(pair.Value)) retries.Add(original.Retry(pair.Key));
            }
            AdvancePastCompleted();
            if(blockIndex>=schedule.Blocks.Count) Status=SessionState.Complete;
        }
        void AdvancePastCompleted()
        {
            while(blockIndex<schedule.Blocks.Count)
            {
                var block=schedule.Blocks[blockIndex];
                while(itemIndex<block.Items.Count && completed.Contains(block.Items[itemIndex].TrialId)) itemIndex++;
                if(itemIndex<block.Items.Count) return;
                var pending=retries.Where(x=>!completed.Contains(x.TrialId) && RetryEvidenceAllows(retryOf[x.TrialId]) && block.Items.Any(i=>i.TrialId==retryOf[x.TrialId])).ToArray();
                if(pending.Length>0) { inRetry=true;retryIndex=retries.IndexOf(pending[0]);return; }
                blockIndex++;itemIndex=0;inRetry=false;
            }
        }
        public void ConfirmResume()
        {
            if(!NeedsOperatorConfirmation || transitioning) throw new SessionFault("SESSION_NOT_AT_BOUNDARY");
            double now=Now();
            Write("operator_resume",now,null);
            pausedRequested=stopRequested=false;Status=SessionState.Running;nextOnset=now+LeadMs;
            PrepareNext();
        }
        SlotItem NextItem()
        {
            if(blockIndex>=schedule.Blocks.Count) return null;
            if(inRetry) return retries[retryIndex];
            return schedule.Blocks[blockIndex].Items[itemIndex];
        }
        void PrepareNext()
        {
            if(Status!=SessionState.Running) return;
            var item=NextItem();if(item==null) { Status=SessionState.Complete;Write("visit_complete",Now(),null);return; }
            consumed=false;audible=item.Plays==0?AudibleStatus.NoCue:AudibleStatus.NotRequested;fault=response=null;opened=closed=false;
            context=new SlotContext(item,nextOnset,retryOf.TryGetValue(item.TrialId,out string original)?original:null);
            try
            {
                content=factory.Create(item)??throw new SessionFault("SESSION_CONTENT_UNAVAILABLE");
                CurrentState=null;Transition(ItemState.Loaded);content.Prepare(context);
            }
            catch(SessionFault error) { Fault(error.Code); }
            catch(Exception) { Fault("SESSION_CONTENT_PREPARE_FAILED"); }
        }
        public void Tick()
        {
            if(Status!=SessionState.Running || transitioning || pumping || faulting) return;
            if(factory is ISessionContentPump pump)
            {
                pumping=true;
                try { pump.Pump(); }
                catch(SessionFault error) { Fault(error.Code); }
                catch(Exception) { Fault("SESSION_CONTENT_PUMP_FAILED"); }
                finally { pumping=false; }
                if(Status!=SessionState.Running) return;
            }
            double now=Now();
            if(stateAfterTail.HasValue)
            {
                if(now>=tailEnd) { Status=stateAfterTail.Value;stateAfterTail=null;Write(Status==SessionState.Complete?"visit_complete":Status==SessionState.Stopped?"session_stopped":"session_paused",now,null); }
                return;
            }
            if(content==null) return;
            try
            {
                if(CurrentState==ItemState.Loaded)
                {
                    if(!content.Readiness.Ready)
                    {
                        if(now>context.OnsetMonoMs-MinimumLeadMs) Fault("SESSION_READY_DEADLINE_MISSED");
                        return;
                    }
                    Transition(ItemState.Ready);
                }
                if(CurrentState==ItemState.Ready)
                {
                    if(pausedRequested||stopRequested) { AtBoundary();return; }
                    if(now<context.OnsetMonoMs-LeadMs) return;
                    if(!content.Readiness.Ready || now>context.OnsetMonoMs-MinimumLeadMs) { Fault("SESSION_CUE_GATE_REFUSED");return; }
                    if(context.Item.Plays>0) { consumed=true;audible=AudibleStatus.Uncertain; }
                    Transition(ItemState.CueRequested); // durable before any PlayScheduled call
                    permit=context.Item.Heldout?new NovelPermit(this,context.Item.ContentId):null;
                    content.RequestCue(context,permit);
                }
                if(CurrentState>=ItemState.CueRequested && fault==null && (!content.Readiness.FocusOk || !content.Readiness.InputOk))
                { Fault("SESSION_FOCUS_OR_INPUT_LOST");return; }
                if(CurrentState==ItemState.CueRequested && now>=context.OnsetMonoMs+context.Item.ResponseOpensSeconds*1000)
                {
                    Transition(ItemState.ResponseOpen);opened=true;content.OpenResponse(context);
                }
                if((CurrentState==ItemState.ResponseOpen || CurrentState==ItemState.CueRequested) && now>=context.OnsetMonoMs+context.Item.ResponseClosesSeconds*1000)
                {
                    Transition(ItemState.Closed);closed=true;content.CloseResponse(context);
                    Transition(ItemState.Reset);content.RequestReset(context);
                }
                if(CurrentState==ItemState.Reset)
                {
                    if(content.ResetComplete) FinishItem();
                    else if(now>=context.EndMonoMs) { fault=fault??"SESSION_RESET_DEADLINE_MISSED";pausedRequested=true;FinishItem(); }
                }
            }
            catch(SessionFault error) { Fault(error.Code); }
            catch(AudioFault error) { Fault(error.Code); }
            catch(Exception) { Fault("SESSION_CONTENT_FAILED"); }
        }
        void Transition(ItemState state)
        {
            transitioning=true;
            try { Write("state_before",Now(),state);CurrentState=state;Write("state_after",Now(),state); }
            finally { transitioning=false; }
        }
        void Write(string kind,double now,ItemState? state,string evidence=null)
        {
            SlotReadiness ready=content==null?default:content.Readiness;
            var record=new SessionRecord(kind,epoch,schedule.Sha256,content==null?null:context.Item.TrialId,content==null?null:context.RetryOf,
                blockIndex,itemIndex,now,content==null?(double?)null:context.OnsetMonoMs,state,audible,consumed,ready.ResetAcknowledged,ready.FocusOk,fault,response,evidence,
                content==null?null:context.OpportunityId,content==null?null:context.AudioRequestIds);
            try { journal.Append(record);if(record.TrialId!=null) latest[record.TrialId]=record; }
            catch { Emergency("SESSION_JOURNAL_FAILED");throw new SessionFault("SESSION_JOURNAL_FAILED"); }
        }
        public void ObserveAudible(AudibleStatus status,string evidenceSha256)
        {
            if(content==null || CurrentState<ItemState.CueRequested || CurrentState==ItemState.Done || context.Item.Plays==0 ||
                status is not (AudibleStatus.ConfirmedAudible or AudibleStatus.ConfirmedNoOnset or AudibleStatus.Uncertain) ||
                evidenceSha256==null || !Regex.IsMatch(evidenceSha256,@"\A[0-9a-f]{64}\z")) throw new SessionFault("SESSION_ONSET_EVIDENCE_INVALID");
            if(audible==AudibleStatus.ConfirmedAudible && status!=AudibleStatus.ConfirmedAudible) throw new SessionFault("SESSION_EXPOSURE_CANNOT_BE_UNDONE");
            if(status==AudibleStatus.ConfirmedNoOnset && context.Item.Plays!=1) throw new SessionFault("SESSION_MULTIPLAY_NO_ONSET_UNPROVEN");
            audible=status;consumed=status!=AudibleStatus.ConfirmedNoOnset;
            Write("onset_evidence",Now(),CurrentState,evidenceSha256);
            if(status==AudibleStatus.ConfirmedNoOnset) QueueRetry();
        }
        public void RecordResponse(string code)
        {
            if(content==null || !opened || closed || response!=null || CurrentState!=ItemState.ResponseOpen ||
                code is not ("commit" or "dont_know" or "timeout")) throw new SessionFault("SESSION_RESPONSE_REFUSED");
            double now=Now();
            if(now>=context.OnsetMonoMs+context.Item.ResponseClosesSeconds*1000 && code!="timeout") throw new SessionFault("SESSION_RESPONSE_LATE");
            response=code;Write("response",now,CurrentState);
        }
        public void RequestPause()
        {
            if(Status!=SessionState.Running) return;pausedRequested=true;
            if(CurrentState>=ItemState.CueRequested)
            {
                fault=fault??"SESSION_PAUSE_DURING_ITEM";Write("pause_requested",Now(),CurrentState);
                // A scheduled cue may already have been handed to the device.
                // Stop it before a future onset when possible, but retain the
                // uncertain consumed opportunity without a no-onset receipt.
                if(Now()<context.OnsetMonoMs)Fault("SESSION_PAUSE_AFTER_CUE_REQUEST");
            }
            else AtBoundary();
        }
        public void RequestStop()
        {
            if(Status is SessionState.Complete or SessionState.Stopped) return;stopRequested=true;
            if(content!=null && CurrentState>=ItemState.CueRequested) { fault=fault??"SESSION_OPERATOR_STOP";content.Interrupt(fault);Write("stop_requested",Now(),CurrentState); }
            AtBoundary();
        }
        public void Fault(string code)
        {
            if(Status==SessionState.Faulted || faulting) return;
            faulting=true;
            try
            {
            fault=new SessionFault(code).Code;pausedRequested=true;
            if(content!=null) { try { content.Interrupt(fault); } catch { } }
            Write("item_fault",Now(),CurrentState);
            // A started opportunity is kept through its fixed end; no new cue
            // can occur. Before a request, this is an unplayed safe boundary.
            if(CurrentState<ItemState.CueRequested || CurrentState==null) AtBoundary();
            else
            {
                closed=true;permit=null;
                Transition(ItemState.Reset);
                try { content.RequestReset(context); } catch { }
            }
            }
            finally { faulting=false; }
        }
        void FinishItem()
        {
            string id=context.Item.TrialId;double end=context.EndMonoMs;
            Transition(ItemState.Done);completed.Add(id);permit=null;content=null;CurrentState=null;
            if(inRetry) retryIndex++;else itemIndex++;
            int previousBlock=blockIndex;AdvancePastCompleted();
            if(blockIndex>=schedule.Blocks.Count || pausedRequested || stopRequested || blockIndex!=previousBlock)
            {
                tailEnd=end;stateAfterTail=stopRequested?SessionState.Stopped:blockIndex>=schedule.Blocks.Count?SessionState.Complete:SessionState.Paused;
                return;
            }
            nextOnset=end;
            if(Now()>nextOnset-MinimumLeadMs) { Status=SessionState.Paused;Write("boundary_late",Now(),null);return; }
            PrepareNext();
        }
        void QueueRetry()
        {
            string id=context.Item.TrialId;
            if(context.RetryOf!=null || context.Item.Plays!=1 || !retryGranted.Add(id)) return;
            string newId="retry-"+Guid.NewGuid().ToString("N");var retry=context.Item.Retry(newId);
            var saved=context;context=new SlotContext(retry,context.OnsetMonoMs,id);
            try { Write("retry_queued",Now(),null); } finally { context=saved; }
            retries.Add(retry);retryOf.Add(newId,id);
        }
        bool RetryEvidenceAllows(string original) => latest.TryGetValue(original,out var value) &&
            value.AudibleStatus==AudibleStatus.ConfirmedNoOnset && !value.ExposureConsumed;
        void AtBoundary()
        {
            if(content!=null && CurrentState<ItemState.CueRequested) { try { content.Interrupt("SESSION_PAUSED_BEFORE_CUE"); } catch { } content=null;CurrentState=null; }
            Status=stopRequested?SessionState.Stopped:SessionState.Paused;permit=null;Write(stopRequested?"session_stopped":"session_paused",Now(),CurrentState);
        }
        void Emergency(string code) { Status=SessionState.Faulted;permit=null;try { content?.Interrupt(code); } catch { } }
        sealed class NovelPermit : INovelSlotAuthorization
        {
            readonly FixedSlotEngine owner;readonly string message;bool used;
            internal NovelPermit(FixedSlotEngine owner,string message) { this.owner=owner;this.message=message; }
            public bool TryConsume(string packageSha256,string messageId)
            {
                if(used || owner.permit!=this || owner.Status!=SessionState.Running || owner.CurrentState!=ItemState.CueRequested ||
                    packageSha256!=owner.schedule.PackageSha256 || messageId!=message || !owner.content.Readiness.Ready) return false;
                used=true;owner.Write("novel_buffer_authorized",owner.Now(),owner.CurrentState);return true;
            }
        }
    }
}
