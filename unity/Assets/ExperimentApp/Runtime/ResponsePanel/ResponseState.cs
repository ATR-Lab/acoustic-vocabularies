using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;

namespace AcousticVocab.ResponsePanel
{
    public enum PanelMode { FullMessage, AtomicProbe, LessonAtomic, LessonMessage, Practice }
    public enum PanelRole { Command, Action, Target }
    public enum ResponseCode { Commit, DontKnow, Timeout }

    public static class PublicCommands
    {
        public static readonly IReadOnlyList<string> Targets = Array.AsReadOnly(new[] { "A", "B", "C", "D", "E", "F", "G", "H" });
        public static readonly IReadOnlyList<string> Actions = Array.AsReadOnly(new[] { "ADD_ONE", "REMOVE_ONE", "FLIP_CARD", "ALIGN_ARROW", "SCAN", "TAG", "CLOSE", "QUARANTINE" });
        public static int Family(string target) { int index = Index(Targets, target); return index < 0 ? -1 : index / 4; }
        public static int ActionFamily(string action) { int index = Index(Actions, action); return index < 0 ? -1 : index / 4; }
        public static bool Legal(string target, string action) => Family(target) >= 0 && Family(target) == ActionFamily(action);
        public static int Index(IReadOnlyList<string> values, string value) { for (int i = 0; i < values.Count; i++) if (values[i] == value) return i; return -1; }
    }

    public sealed class PanelRequest
    {
        public string TrialId { get; }
        public PanelMode Mode { get; }
        public PanelRole Role { get; }
        public double AnchorMonoMs { get; }
        public double OpensMonoMs { get; }
        public double DeadlineMonoMs { get; }
        public double? SlotEndMonoMs { get; }
        public PanelRequest(string trialId, PanelMode mode, PanelRole role, double anchorMonoMs, double? practiceWindowMs = null)
        {
            if (trialId == null || !Regex.IsMatch(trialId, @"\A[A-Za-z0-9][A-Za-z0-9._-]{0,79}\z")) throw new ArgumentException("Opaque trial identifier required");
            if (!Enum.IsDefined(typeof(PanelMode), mode) || !Enum.IsDefined(typeof(PanelRole), role)) throw new ArgumentException("Unknown panel mode or role");
            bool atomic = mode == PanelMode.AtomicProbe || mode == PanelMode.LessonAtomic;
            if (atomic == (role == PanelRole.Command)) throw new ArgumentException("Atomic mode requires one role; command mode requires command role");
            Finite(anchorMonoMs);
            double start = 0, end, slot;
            switch (mode)
            {
                case PanelMode.FullMessage: end = 12000; slot = 14000; break;
                case PanelMode.AtomicProbe: end = 7000; slot = 9000; break;
                case PanelMode.LessonAtomic: start = 6000; end = 13000; slot = 20000; break;
                case PanelMode.LessonMessage: start = 8000; end = 17000; slot = 24000; break;
                default:
                    if (!practiceWindowMs.HasValue || practiceWindowMs <= 0 || practiceWindowMs > 600000) throw new ArgumentException("Explicit bounded engineering practice window required");
                    Finite(practiceWindowMs.Value); end = practiceWindowMs.Value; slot = end; break;
            }
            if (mode != PanelMode.Practice && practiceWindowMs.HasValue) throw new ArgumentException("Study-mode windows cannot be overridden");
            TrialId = trialId; Mode = mode; Role = role; AnchorMonoMs = anchorMonoMs;
            OpensMonoMs = anchorMonoMs + start; DeadlineMonoMs = anchorMonoMs + end;
            SlotEndMonoMs = mode == PanelMode.Practice ? (double?)null : anchorMonoMs + slot;
            Finite(DeadlineMonoMs); if (SlotEndMonoMs.HasValue) Finite(SlotEndMonoMs.Value);
            if (DeadlineMonoMs <= OpensMonoMs || (SlotEndMonoMs.HasValue && SlotEndMonoMs.Value <= DeadlineMonoMs)) throw new ArgumentException("Monotonic anchor precision cannot represent the response window");
        }
        internal static void Finite(double value) { if (double.IsNaN(value) || double.IsInfinity(value) || value < 0) throw new ArgumentException("Nonnegative finite monotonic time required"); }
    }

    public sealed class PanelResponse
    {
        public ResponseCode Code { get; }
        public string Target { get; }
        public string Action { get; }
        public string SelectedTarget { get; }
        public string SelectedAction { get; }
        public double ResponseMonoMs { get; }
        public PanelRequest Request { get; }
        internal PanelResponse(PanelRequest request, ResponseCode code, string target, string action, double now)
        { Request = request; Code = code; SelectedTarget = target; SelectedAction = action; Target = code == ResponseCode.Commit ? target : null; Action = code == ResponseCode.Commit ? action : null; ResponseMonoMs = now; }
    }

    public sealed class PanelProcessEvent
    {
        public string Kind { get; }
        public double MonoMs { get; }
        public string Input { get; }
        public string Target { get; }
        public string Action { get; }
        public PanelRequest Request { get; }
        internal PanelProcessEvent(string kind, double now, string input, string target, string action, PanelRequest request)
        { Kind = kind; MonoMs = now; Input = input; Target = target; Action = action; Request = request; }
    }

    // No scoring, correct answer, vocabulary, robot command, slot completion or trial scheduler.
    public sealed class ResponseState
    {
        readonly Func<double> clock;
        readonly Action<PanelProcessEvent> persist;
        double lastNow = -1;
        public PanelRequest Request { get; private set; }
        public string SelectedTarget { get; private set; }
        public string SelectedAction { get; private set; }
        public bool Locked { get; private set; } = true;
        public bool Aborted { get; private set; }
        public PanelResponse Result { get; private set; }
        public event Action<PanelResponse> Responded;
        public bool LegalSelection => Request != null && (Request.Role == PanelRole.Command ? PublicCommands.Legal(SelectedTarget, SelectedAction) : Request.Role == PanelRole.Target ? PublicCommands.Family(SelectedTarget) >= 0 : PublicCommands.ActionFamily(SelectedAction) >= 0);
        public bool WindowOpen => Request != null && !Locked && lastNow >= Request.OpensMonoMs && lastNow < Request.DeadlineMonoMs;
        public bool CanCommit => WindowOpen && LegalSelection;
        public ResponseState(Func<double> clock, Action<PanelProcessEvent> persist)
        { this.clock = clock ?? throw new ArgumentNullException(nameof(clock)); this.persist = persist ?? throw new ArgumentNullException(nameof(persist)); }
        double Now()
        {
            double now = clock();
            try { PanelRequest.Finite(now); } catch { Locked = true; Aborted = true; throw; }
            if (now < lastNow) { Locked = true; Aborted = true; throw new InvalidOperationException("Monotonic clock regressed"); }
            lastNow = now; return now;
        }
        void Emit(string kind, double now, string input = null)
        {
            try { persist(new PanelProcessEvent(kind, now, input, SelectedTarget, SelectedAction, Request)); }
            catch { Locked = true; Aborted = true; throw; }
        }
        public void Open(PanelRequest request)
        {
            if (request == null) throw new ArgumentNullException(nameof(request));
            if (Request != null && !Locked) throw new InvalidOperationException("Close or abort active panel before opening another trial");
            double now = Now(); Request = request; SelectedTarget = null; SelectedAction = null; Result = null; Aborted = false; Locked = false;
            Emit("panel_open", now); Advance(now);
        }
        public void Tick() { if (Request != null) Advance(Now()); }
        void Advance(double now)
        {
            if (!Locked && now >= Request.DeadlineMonoMs) { Emit("deadline_lock", now); Complete(ResponseCode.Timeout, now); }
        }
        bool Enter(string input, out double now)
        {
            now = Now(); if (Request == null) return false;
            Advance(now);
            if (Locked) { Emit("post_lock_input", now, input); return false; }
            if (now < Request.OpensMonoMs) { Emit("before_window_input", now, input); return false; }
            return true;
        }
        public bool SelectTarget(string target)
        {
            if (!Enter(PublicCommands.Family(target) >= 0 ? target : "invalid_target", out double now)) return false;
            if (Request.Role == PanelRole.Action || PublicCommands.Family(target) < 0) { Emit("invalid_input", now, "select_target"); return false; }
            if (SelectedTarget == target) { Emit("selection_repeated", now, target); return false; }
            string oldAction = SelectedAction; SelectedTarget = target;
            if (Request.Role == PanelRole.Command && oldAction != null && !PublicCommands.Legal(target, oldAction)) SelectedAction = null;
            Emit("select_target", now, target);
            if (oldAction != null && SelectedAction == null) Emit("action_cleared_family_change", now);
            return true;
        }
        public bool SelectAction(string action)
        {
            if (!Enter(PublicCommands.ActionFamily(action) >= 0 ? action : "invalid_action", out double now)) return false;
            if (Request.Role == PanelRole.Target || PublicCommands.ActionFamily(action) < 0 || (Request.Role == PanelRole.Command && !PublicCommands.Legal(SelectedTarget, action)))
            { Emit("invalid_input", now, "select_action"); return false; }
            if (SelectedAction == action) { Emit("selection_repeated", now, action); return false; }
            SelectedAction = action; Emit("select_action", now, action); return true;
        }
        public bool Commit()
        {
            if (!Enter("commit", out double now)) return false;
            if (!LegalSelection) { Emit("commit_disabled", now); return false; }
            Complete(ResponseCode.Commit, now); return true;
        }
        public bool DontKnow() { if (!Enter("dont_know", out double now)) return false; Complete(ResponseCode.DontKnow, now); return true; }
        void Complete(ResponseCode code, double now)
        {
            Locked = true;
            Emit(code == ResponseCode.Commit ? "commit" : code == ResponseCode.DontKnow ? "dont_know" : "timeout", now);
            Result = new PanelResponse(Request, code, SelectedTarget, SelectedAction, now);
            try { Responded?.Invoke(Result); } catch { Aborted = true; throw; }
        }
        public void Abort()
        {
            if (Request == null || Locked) return;
            double now = Now(); Locked = true; Aborted = true; Emit("panel_aborted", now);
        }
    }
}
