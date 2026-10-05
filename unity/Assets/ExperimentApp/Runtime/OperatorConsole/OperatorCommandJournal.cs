using System;
using System.IO;
using System.Text;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.OperatorConsole
{
    public sealed class OperatorAuditRecord
    {
        public string Kind { get; }
        public OperatorRequest Request { get; }
        public double HostMonoMs { get; }
        public long PreviousConsumedSequence { get; }
        public OperatorReceipt Receipt { get; }
        internal OperatorAuditRecord(string kind, OperatorRequest request, double now, long prior, OperatorReceipt receipt = null)
        { Kind = kind; Request = request; HostMonoMs = now; PreviousConsumedSequence = prior; Receipt = receipt; }
        internal JObject Json() => new JObject {
            ["kind"] = Kind, ["request"] = Request.Json(), ["host_mono_ms"] = HostMonoMs,
            ["previous_consumed_sequence"] = PreviousConsumedSequence,
            ["stop_supersedes_through_sequence"] = Request.Command == "stop" ? new JValue(Request.Sequence - 1) : JValue.CreateNull(),
            ["prior_receipt_acknowledgement"] = Request.Command == "stop" ? new JValue("unknown") : JValue.CreateNull(),
            ["receipt"] = Receipt == null ? JValue.CreateNull() : (JToken)Receipt.Json()
        };
    }

    public interface IOperatorCommandJournal
    {
        // Must durably append before returning, or throw. Never update/delete.
        void Append(OperatorAuditRecord record);
    }

    // One new append-only file per adapter process/nonce; old sessions are never
    // replayed. Torn prior files remain untouched for independent investigation.
    public sealed class FileOperatorCommandJournal : IOperatorCommandJournal, IDisposable
    {
        readonly FileStream output;
        readonly int ownerThread = System.Threading.Thread.CurrentThread.ManagedThreadId;
        readonly string nonce;
        string previous = new string('0', 64);
        long sequence;
        double previousTime = -1;
        bool failed, closed;
        public string Path { get; }
        public FileOperatorCommandJournal(string privateDirectory, string sessionNonce)
        {
            Wire.Require(Wire.Guid(sessionNonce), "journal_identity"); nonce = sessionNonce;
            Wire.NoLinks(privateDirectory); Directory.CreateDirectory(privateDirectory); Wire.NoLinks(privateDirectory);
            Path = System.IO.Path.Combine(privateDirectory, "operator-" + nonce + ".local.jsonl");
            output = new FileStream(Path, FileMode.CreateNew, FileAccess.Write, FileShare.Read, 4096, FileOptions.WriteThrough);
        }
        public void Append(OperatorAuditRecord record)
        {
            if (closed || failed) throw new OperatorFault("journal_unavailable");
            try
            {
                Wire.Require(System.Threading.Thread.CurrentThread.ManagedThreadId == ownerThread && record != null &&
                    record.HostMonoMs >= previousTime && !double.IsNaN(record.HostMonoMs) && !double.IsInfinity(record.HostMonoMs), "journal_invalid");
                var row = new JObject { ["version"] = 1, ["session_nonce"] = nonce, ["sequence"] = sequence,
                    ["previous_sha256"] = previous, ["record"] = record.Json() };
                string hash = Wire.Sha(Wire.Bytes(row)); row["sha256"] = hash;
                byte[] bytes = Encoding.UTF8.GetBytes(row.ToString(Newtonsoft.Json.Formatting.None) + "\n");
                Wire.Require(bytes.Length <= Wire.MaximumBytes && output.Length + bytes.Length <= 32 * 1024 * 1024, "journal_limit");
                output.Write(bytes, 0, bytes.Length); output.Flush(true);
                previous = hash; sequence++; previousTime = record.HostMonoMs;
            }
            catch { failed = true; throw new OperatorFault("journal_failed"); }
        }
        public void Dispose()
        {
            if (closed) return; closed = true;
            try { output.Flush(true); } finally { output.Dispose(); }
        }
    }
}
