using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.ResponsePanel
{
    public sealed class PanelJournal : IDisposable
    {
        readonly FileStream stream;
        readonly StreamWriter writer;
        public static double NowMs => (double)Stopwatch.GetTimestamp() * 1000 / Stopwatch.Frequency;
        public PanelJournal(string directory, JObject identity, string stationId, JObject settings)
        {
            Directory.CreateDirectory(directory);
            stream = new FileStream(Path.Combine(directory, "panel-" + Guid.NewGuid().ToString("N") + ".jsonl"), FileMode.CreateNew, FileAccess.Write, FileShare.Read);
            writer = new StreamWriter(stream, new UTF8Encoding(false)) { AutoFlush = true };
            Write(new JObject { ["event"] = "header", ["schema"] = "response-panel-engineering-v1", ["build_identity"] = identity.DeepClone(), ["station_id"] = stationId,
                ["configuration"] = settings.DeepClone(), ["monotonic_frequency_hz"] = Stopwatch.Frequency, ["mono_ms"] = NowMs,
                ["qualification"] = "development_only", ["atomic_commit_policy"] = "explicit_commit_provisional",
                ["timing_source"] = (string)settings["engineering_mode"] == "disabled" ? "caller_supplied_anchor" : "engineering_start_no_audio" });
        }
        public void Process(PanelProcessEvent value)
        {
            var row = Base(value.Request, value.MonoMs); row["event"] = value.Kind; row["input"] = value.Input;
            row["selected_target"] = value.Target; row["selected_action"] = value.Action; Write(row);
        }
        public void Response(PanelResponse value)
        {
            var row = Base(value.Request, value.ResponseMonoMs); row["event"] = "response";
            row["response_code"] = value.Code == ResponseCode.Commit ? "COMMIT" : value.Code == ResponseCode.DontKnow ? "DONT_KNOW" : "TIMEOUT";
            row["response_target"] = value.Target; row["response_action"] = value.Action;
            row["selected_target"] = value.SelectedTarget; row["selected_action"] = value.SelectedAction;
            row["commit_mono_ms"] = value.Code == ResponseCode.Commit ? (JToken)value.ResponseMonoMs : JValue.CreateNull(); Write(row);
        }
        static JObject Base(PanelRequest request, double now) => new JObject { ["trial_id"] = request.TrialId, ["mode"] = request.Mode.ToString(), ["role"] = request.Role.ToString(),
            ["mono_ms"] = now, ["anchor_mono_ms"] = request.AnchorMonoMs, ["opens_mono_ms"] = request.OpensMonoMs, ["deadline_mono_ms"] = request.DeadlineMonoMs };
        public void Fault(string reason) => Write(new JObject { ["event"] = "panel_fault", ["reason"] = reason, ["mono_ms"] = NowMs });
        void Write(JObject value) { writer.WriteLine(value.ToString(Formatting.None)); stream.Flush(true); }
        public void Dispose() => writer.Dispose();
    }
}
