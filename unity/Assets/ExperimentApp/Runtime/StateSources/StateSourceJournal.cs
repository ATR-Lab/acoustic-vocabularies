using System;
using System.Collections.Concurrent;
using System.Globalization;
using System.IO;
using System.Text;
using System.Threading;
using AcousticVocab.Foundation;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.StateSources
{
    public sealed class StateSourceJournal : IDisposable
    {
        readonly FoundationLog events;
        readonly BlockingCollection<string> rows=new BlockingCollection<string>(2048);
        readonly Thread thread;
        volatile Exception failure;
        int disposed;
        static string N(double value) => value.ToString("R",CultureInfo.InvariantCulture);
        public StateSourceJournal(string directory,JObject identity,SceneRegistry registry,string kind)
        {
            Directory.CreateDirectory(directory);
            events=new FoundationLog(directory,identity,registry.StationId,kind);
            events.Write("state_source_identity",new JObject {
                ["scene_sha256"]=registry.SceneHash,["reset_snapshot_sha256"]=registry.SnapshotHash,
                ["robot_state_source"]=kind,["age_basis"]="local_valid_sample_arrival",
                ["qualification"]="engineering_only"
            });
            string path=Path.Combine(directory,"sample-age-"+Guid.NewGuid().ToString("N")+".csv");
            var stream=new FileStream(path,FileMode.CreateNew,FileAccess.Write,FileShare.Read);
            thread=new Thread(() => {
                try
                {
                    using var writer=new StreamWriter(stream,new UTF8Encoding(false));
                    writer.WriteLine("host_mono_s,sample_age_s,sim_time,stale,reset_confirmed");
                    foreach(string row in rows.GetConsumingEnumerable()) writer.WriteLine(row);
                    writer.Flush(); stream.Flush(true);
                }
                catch(Exception error) { failure=error; }
                finally { stream.Dispose(); }
            }) { IsBackground=true,Name="state-source-evidence" };
            thread.Start();
        }
        public void Record(SourceEvent value)
        {
            Check();
            events.Write("state_source_event",new JObject {
                ["code"]=value.Code,["start_mono_s"]=value.StartMonoSeconds,
                ["observed_mono_s"]=value.ObservedMonoSeconds,["duration_s"]=value.DurationSeconds
            });
        }
        public void Sample(IRobotStateSource source,double now)
        {
            Check();
            if(!rows.TryAdd(string.Join(",",N(now),N(source.SampleAgeSeconds),N(source.LastSimTime),
                source.Stale?"1":"0",source.ResetConfirmed?"1":"0")))
                throw new StateFault("STATE_LOG_OVERFLOW");
        }
        void Check()
        {
            if(failure!=null || !thread.IsAlive || disposed!=0) throw new StateFault("STATE_LOG_UNAVAILABLE");
        }
        public void Dispose()
        {
            if(Interlocked.Exchange(ref disposed,1)!=0) return;
            rows.CompleteAdding();
            bool stopped=thread.Join(2000);
            events.Dispose();
            if(!stopped || failure!=null) throw new StateFault("STATE_LOG_NOT_FINALIZED");
            rows.Dispose();
        }
    }
}
