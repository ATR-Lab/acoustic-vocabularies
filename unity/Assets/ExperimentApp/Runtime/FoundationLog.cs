using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.Foundation
{
    public sealed class FoundationLog : IDisposable
    {
        readonly StreamWriter writer;
        readonly FileStream stream;
        public FoundationLog(string directory, JObject identity, string stationId, string robotStateSource = "unresolved")
        {
            Directory.CreateDirectory(directory);
            stream = new FileStream(Path.Combine(directory, "foundation-" + Guid.NewGuid().ToString("N") + ".jsonl"), FileMode.CreateNew, FileAccess.Write, FileShare.Read);
            writer = new StreamWriter(stream, new UTF8Encoding(false)) { AutoFlush = true };
            Write("header", new JObject { ["event_schema"] = "foundation-engineering-v1", ["station_id"] = stationId,
                ["robot_state_source"] = robotStateSource,
                ["build_identity"] = identity.DeepClone(), ["monotonic_frequency_hz"] = Stopwatch.Frequency });
        }
        public void Write(string kind, JObject data = null)
        {
            var record = data ?? new JObject();
            record["event"] = kind; record["monotonic_ticks"] = Stopwatch.GetTimestamp();
            writer.WriteLine(record.ToString(Formatting.None));
            stream.Flush(true);
        }
        public void Dispose() { writer.Dispose(); }
    }
}
