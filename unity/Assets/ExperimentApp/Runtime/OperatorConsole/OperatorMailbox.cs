using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using AcousticVocab.SessionEngine;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.OperatorConsole
{
    // Trusted host constructs a validated engine and supplies measured health.
    // This class has no participant UI, package loader, allocation or network API.
    public sealed class OperatorMailbox : IDisposable
    {
        readonly FixedSlotEngine engine;
        readonly Action<FixedSlotEngine> prepareResume;
        readonly Action<FixedSlotEngine,OperatorRequest> prepareRequestResume;
        readonly IOperatorCommandJournal journal;
        readonly Func<OperatorAdmission> admission;
        readonly Func<OperatorHealth> health;
        readonly Func<double> mono;
        readonly Func<DateTimeOffset> utc;
        readonly string directory, manifestHash;
        readonly FileStream writerLock;
        readonly int ownerThread = System.Threading.Thread.CurrentThread.ManagedThreadId;
        readonly Dictionary<string, OperatorReceipt> handled = new Dictionary<string, OperatorReceipt>(StringComparer.Ordinal);
        readonly Dictionary<string, string> requestHashes = new Dictionary<string, string>(StringComparer.Ordinal);
        long consumed, heartbeat;
        double lastNow = -1, lastPublish = -1000;
        string lastFileHash;
        bool loaded, failed, disposed;
        OperatorReceipt receipt;
        public string SessionNonce { get; }
        public bool Loaded => loaded && !failed && !disposed;
        public bool Failed => failed;
        public long ConsumedSequence => consumed;
        public OperatorMailbox(string privateDirectory, string sessionNonce, string runSheetManifestSha256,
            FixedSlotEngine engine, IOperatorCommandJournal journal, Func<OperatorAdmission> admission,
            Func<OperatorHealth> health, Func<double> monotonicMilliseconds, Func<DateTimeOffset> utcClock = null, Action<FixedSlotEngine> prepareResume = null,Action<FixedSlotEngine,OperatorRequest> prepareRequestResume=null)
        {
            Wire.Require(Wire.Guid(sessionNonce) && Wire.Hash(runSheetManifestSha256), "binding_invalid");
            Wire.Require(prepareResume==null||prepareRequestResume==null,"binding_invalid");
            this.engine = engine ?? throw new ArgumentNullException(nameof(engine));this.prepareResume=prepareResume;this.prepareRequestResume=prepareRequestResume;
            this.journal = journal ?? throw new ArgumentNullException(nameof(journal));
            this.admission = admission ?? throw new ArgumentNullException(nameof(admission));
            this.health = health ?? throw new ArgumentNullException(nameof(health));
            mono = monotonicMilliseconds ?? throw new ArgumentNullException(nameof(monotonicMilliseconds));
            utc = utcClock ?? (() => DateTimeOffset.UtcNow);
            directory = System.IO.Path.GetFullPath(privateDirectory); SessionNonce = sessionNonce; manifestHash = runSheetManifestSha256;
            Wire.NoLinks(directory); Wire.Require(Directory.Exists(directory), "mailbox_missing");
            string lockPath = System.IO.Path.Combine(directory, "engine-writer.lock"); Wire.NoLinks(lockPath);
            writerLock = new FileStream(lockPath, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
        }
        void Owner() => Wire.Require(!disposed && System.Threading.Thread.CurrentThread.ManagedThreadId == ownerThread, "owner_unavailable");
        double Now()
        {
            double value = mono();
            if (double.IsNaN(value) || double.IsInfinity(value) || value < 0 || value < lastNow) { FailClosed(); throw new OperatorFault("clock_invalid"); }
            lastNow = value; return value;
        }
        void FailClosed()
        {
            if (failed) return;
            failed = true; loaded = false;
            // A persistence/host failure cannot leave a content module exposing.
            // RequestStop is emergency fail-closed action, not an accepted command.
            try { engine.Fault("OPERATOR_ADAPTER_FAILED"); } catch { }
            try { engine.RequestStop(); } catch { }
        }
        void Append(OperatorAuditRecord entry)
        {
            try { journal.Append(entry); }
            catch { FailClosed(); throw new OperatorFault("journal_failed"); }
        }
        public OperatorReceipt Handle(OperatorRequest request)
        {
            Owner(); Wire.Require(request != null, "command_invalid");
            if (failed) return new OperatorReceipt(request, false, "engine_fault");
            double now = Now(); string digest = Wire.Sha(Wire.Bytes(request.Json()));
            if (handled.TryGetValue(request.RequestId, out var prior) && requestHashes[request.RequestId] == digest) return prior;
            bool nonceOk = request.SessionNonce == SessionNonce;
            bool sequenceOk = request.Sequence == consumed + 1 || request.Command == "stop" && request.Sequence > consumed;
            bool freshId = !handled.ContainsKey(request.RequestId);
            if (freshId && handled.Count >= 10000) { FailClosed(); throw new OperatorFault("command_limit"); }
            string refusal = !nonceOk ? "session_mismatch" : !sequenceOk ? "sequence_rejected" : !freshId ? "request_reused" :
                request.ManifestSha256 != manifestHash || request.ScheduleSha256 != engine.ScheduleSha256 ? "hash_mismatch" : null;
            long previousConsumed = consumed;
            Append(new OperatorAuditRecord("request", request, now, previousConsumed));
            // Invalid epochs/sequences do not replace the current high-water receipt.
            // They cannot make a restarted console forget the last consumed sequence.
            bool consumes = nonceOk && sequenceOk;
            if (consumes) consumed = request.Sequence;
            if (refusal == null)
            {
                try
                {
                    if (request.Command == "load") { Wire.Require(admission()?.Allowed == true, "admission_failed"); loaded = true; }
                    else if (request.Command == "pause") engine.RequestPause();
                    else if (request.Command == "stop") engine.RequestStop();
                    else
                    {
                        Wire.Require(Loaded, "visit_not_loaded");
                        Wire.Require(admission()?.Allowed == true, "admission_failed");
                        Wire.Require(health()?.Ready == true, "health_failed");
                        Wire.Require(request.Command == "start" ? engine.Status == SessionState.AwaitingOperator : engine.Status == SessionState.Paused, "not_at_boundary");
                        prepareResume?.Invoke(engine);
                        prepareRequestResume?.Invoke(engine,request);
                        engine.ConfirmResume();
                    }
                }
                catch (OperatorFault fault) { refusal = fault.Code; }
                catch (SessionFault) { refusal = "engine_fault"; FailClosed(); }
                catch { refusal = "engine_fault"; FailClosed(); }
            }
            var result = new OperatorReceipt(request, refusal == null, refusal ?? "none");
            Append(new OperatorAuditRecord("result", request, Now(), previousConsumed, result));
            if (consumes)
            {
                receipt = result;
                if (freshId) { handled.Add(request.RequestId, result); requestHashes.Add(request.RequestId, digest); }
            }
            return result;
        }
        public JObject Snapshot()
        {
            Owner(); Now(); OperatorAdmission gate; OperatorHealth status;
            try { gate = admission() ?? throw new OperatorFault("admission_failed"); status = health() ?? throw new OperatorFault("health_failed"); }
            catch { FailClosed(); gate = new OperatorAdmission(false, false, false); status = new OperatorHealth(false, false, false, false, 0, 0, 0); }
            string state = failed ? "faulted" : engine.Status == SessionState.AwaitingOperator ? "awaiting_operator" : engine.Status.ToString().ToLowerInvariant();
            Wire.Require(heartbeat < long.MaxValue, "heartbeat_limit"); heartbeat++;
            return new JObject { ["version"] = 1, ["session_nonce"] = SessionNonce, ["sequence"] = heartbeat,
                ["utc"] = utc().UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss.fffffff'Z'", CultureInfo.InvariantCulture),
                ["receipt"] = receipt == null ? JValue.CreateNull() : (JToken)receipt.Json(),
                ["run_sheet_manifest_sha256"] = manifestHash, ["schedule_sha256"] = engine.ScheduleSha256, ["package_sha256"] = engine.PackageSha256,
                ["engine_state"] = state, ["completed_counts"] = new JArray(engine.CompletedCounts), ["admission"] = gate.Json(), ["health"] = status.Json() };
        }
        public void Tick()
        {
            Owner();
            try
            {
                double now = Now(); string command = System.IO.Path.Combine(directory, "command.json"); bool changed = false;
                if (File.Exists(command))
                {
                    Wire.NoLinks(command); Wire.Require(new FileInfo(command).Length <= Wire.MaximumBytes, "command_invalid");
                    byte[] bytes;
                    using (var stream = new FileStream(command, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                    using (var copy = new MemoryStream())
                    {
                        Wire.Require(stream.Length <= Wire.MaximumBytes, "command_invalid");
                        stream.CopyTo(copy); bytes = copy.ToArray();
                        Wire.Require(bytes.Length <= Wire.MaximumBytes, "command_invalid");
                    }
                    string hash = Wire.Sha(bytes);
                    if (hash != lastFileHash)
                    {
                        lastFileHash = hash;
                        // Malformed packets never reach the engine. No untrusted
                        // text is echoed and no invalid ID is fabricated for receipt.
                        try { Handle(OperatorRequest.Parse(bytes)); changed = true; }
                        catch (OperatorFault fault) when (fault.Code == "command_invalid") { }
                    }
                }
                if (!failed) engine.Tick();
                if (changed || now - lastPublish >= 250)
                { Wire.Atomic(System.IO.Path.Combine(directory, "state.json"), Wire.Bytes(Snapshot())); lastPublish = now; }
            }
            catch { FailClosed(); throw; }
        }
        public void Dispose()
        {
            if (disposed) return;
            // Losing the adapter must not leave an unattended engine running.
            if (engine.Status == SessionState.Running) FailClosed();
            disposed = true; writerLock.Dispose();
        }
    }
}
