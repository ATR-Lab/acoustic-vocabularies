using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

namespace AcousticVocab.SessionEngine
{
    // The visit session and station calendar day of a battery's first accepted
    // start. Persisted before that start; never reconstructed from later rows.
    public sealed class ResumeAnchor
    {
        public string SessionId { get; }
        public string LocalDate { get; }
        public ResumeAnchor(string sessionId,string localDate)
        {
            if(!ResumePolicy.IsSessionId(sessionId)||!ResumePolicy.TryDate(localDate,out _))throw new SessionFault("RESUME_ANCHOR_INVALID");
            SessionId=sessionId;LocalDate=localDate;
        }
    }

    // Durable resumption facts of one visit. Each write returns only after the
    // fact is durable, or throws.
    public interface IResumeRecord
    {
        ResumeAnchor Anchor { get; }
        string ClosedCode { get; }
        void RecordAnchor(ResumeAnchor anchor);
        void RecordClosed(string code);
    }

    // When an interrupted battery may continue. Study A D0 (immediate test):
    // only in the same visit session and on the same station calendar day as
    // its first start. Study B V1: only on its own first-start day. Delayed
    // visits (A D7; B V2, V3, W1, W4): only through the configured window end,
    // or on their own first-start day. Missing facts refuse.
    public sealed class ResumePolicy
    {
        public const string OutsideSession="RESUME_OUTSIDE_SESSION",OutsideWindow="RESUME_OUTSIDE_WINDOW";
        static readonly string[] delayed={"A/D7","B/V2","B/V3","B/W1","B/W4"};
        public string Study { get; }
        public string Visit { get; }
        public string SessionId { get; }
        public string WindowEnd { get; }
        bool SameSession=>Study=="A"&&Visit=="D0";
        bool OwnDay=>Study=="B"&&Visit=="V1";
        public ResumePolicy(string study,string visit,string sessionId,string windowEnd)
        {
            Study=study;Visit=visit;SessionId=sessionId;WindowEnd=windowEnd;
            bool known=SameSession||OwnDay||Array.IndexOf(delayed,study+"/"+visit)>=0;
            // A day-0 visit has no window; a window there is a configuration error.
            if(!known||!IsSessionId(sessionId)||windowEnd!=null&&(SameSession||OwnDay||!TryDate(windowEnd,out _)))
                throw new SessionFault("RESUME_POLICY_INVALID");
        }
        // Null when the battery may continue at `now` (station local time),
        // otherwise the refusal code. A refusal is final once recorded.
        public string Refusal(bool started,ResumeAnchor anchor,string closedCode,DateTimeOffset now)
        {
            if(closedCode!=null)return closedCode;
            if(!started)return null;
            DateTime today=now.Date;
            DateTime? first=anchor!=null&&TryDate(anchor.LocalDate,out var day)?day:(DateTime?)null;
            if(SameSession)return anchor!=null&&anchor.SessionId==SessionId&&first==today?null:OutsideSession;
            if(OwnDay)return first==today?null:OutsideWindow;
            DateTime? end=WindowEnd!=null&&TryDate(WindowEnd,out var window)?window:(DateTime?)null;
            if(first.HasValue&&(!end.HasValue||first.Value>end.Value))end=first;
            return end.HasValue&&today<=end.Value?null:OutsideWindow;
        }
        internal static bool IsSessionId(string value)=>value!=null&&Regex.IsMatch(value,@"\A[0-9a-f]{32}\z");
        internal static bool TryDate(string value,out DateTime date)
        {
            date=default;
            return value!=null&&Regex.IsMatch(value,@"\A[0-9]{4}-[0-9]{2}-[0-9]{2}\z")&&
                DateTime.TryParseExact(value,"yyyy-MM-dd",CultureInfo.InvariantCulture,DateTimeStyles.None,out date);
        }
    }

    // Evaluated by the host before every ConfirmResume (start or resume). The
    // engine itself is unchanged: a refusal never reaches ConfirmResume, so no
    // trial is prepared, replayed or re-run and committed rows stay as they
    // are. A refused battery is closed as interrupted: the refusal is recorded
    // durably once and the engine is stopped at its operator boundary.
    public sealed class ResumeGuard
    {
        readonly ResumePolicy policy;readonly IResumeRecord record;readonly Func<DateTimeOffset> localNow;
        public ResumeGuard(ResumePolicy policy,IResumeRecord record,Func<DateTimeOffset> localNow)
        {
            this.policy=policy??throw new ArgumentNullException(nameof(policy));
            this.record=record??throw new ArgumentNullException(nameof(record));
            this.localNow=localNow??throw new ArgumentNullException(nameof(localNow));
        }
        // Null when the command may continue; the first accepted start records
        // its anchor first. Otherwise the refusal code, after closing the battery.
        public string Admit(FixedSlotEngine engine,IReadOnlyList<SessionRecord> history)
        {
            if(engine==null)throw new ArgumentNullException(nameof(engine));
            if(history==null)throw new ArgumentNullException(nameof(history));
            if(!engine.NeedsOperatorConfirmation)throw new SessionFault("SESSION_NOT_AT_BOUNDARY");
            DateTimeOffset now=localNow();
            var anchor=record.Anchor;string closed=record.ClosedCode;
            bool started=history.Count>0||anchor!=null||closed!=null;
            string refusal=policy.Refusal(started,anchor,closed,now);
            if(refusal==null)
            {
                if(!started)record.RecordAnchor(new ResumeAnchor(policy.SessionId,now.ToString("yyyy-MM-dd",CultureInfo.InvariantCulture)));
                return null;
            }
            if(closed==null)record.RecordClosed(refusal);
            engine.RequestStop();
            return refusal;
        }
    }
}
