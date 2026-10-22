using System;
using System.Diagnostics;
using System.IO;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.Foundation
{
    // Cheap, default-on attribution for main-thread stalls. Stage markers and
    // slow durable flushes go into a fixed ring; nothing is written to disk
    // until an owner observes a frame gap and records the ring. Observation
    // only: it never changes timing gates, grants or durable ordering.
    public static class MainThreadStages
    {
        public const int Capacity=32;
        public const double SlowFlushMs=50;
        static readonly object sync=new object();
        static readonly string[] stages=new string[Capacity];
        static readonly long[] ticks=new long[Capacity];
        static readonly double[] durations=new double[Capacity];
        static int next,count;
        static long slowFlushes;
        public static long SlowFlushCount { get { lock(sync) return slowFlushes; } }
        public static void Mark(string stage)=>Add(stage,Stopwatch.GetTimestamp(),-1);
        static void Add(string stage,long at,double durationMs)
        {
            lock(sync){stages[next]=stage;ticks[next]=at;durations[next]=durationMs;next=(next+1)%Capacity;if(count<Capacity)count++;}
        }
        // Durable flush with the same semantics as Flush(true); a flush slower
        // than SlowFlushMs is remembered with its start time and duration.
        public static void Flush(FileStream stream,string name)
        {
            long start=Stopwatch.GetTimestamp();stream.Flush(true);
            double ms=(Stopwatch.GetTimestamp()-start)*1000.0/Stopwatch.Frequency;
            if(ms>=SlowFlushMs){lock(sync)slowFlushes++;Add("slow_flush:"+name,start,ms);}
        }
        // Oldest first; times are local monotonic milliseconds.
        public static JArray Recent()
        {
            var result=new JArray();
            lock(sync)
            {
                for(int i=0;i<count;i++)
                {
                    int index=(next-count+i+Capacity)%Capacity;
                    var row=new JObject{["stage"]=stages[index],["mono_ms"]=ticks[index]*1000.0/Stopwatch.Frequency};
                    if(durations[index]>=0)row["duration_ms"]=durations[index];
                    result.Add(row);
                }
            }
            return result;
        }
        public static void ResetForTests(){lock(sync){next=count=0;slowFlushes=0;Array.Clear(stages,0,Capacity);}}
    }
    // Detects a gap between successive owner updates and snapshots the ring
    // and managed GC counters at that moment.
    public sealed class MainThreadStallMonitor
    {
        readonly double thresholdMs;double last=-1;int gen0,gen1,gen2;long slow;
        public MainThreadStallMonitor(double thresholdMs=250){if(!(thresholdMs>0))throw new ArgumentOutOfRangeException(nameof(thresholdMs));this.thresholdMs=thresholdMs;Capture();}
        void Capture(){gen0=GC.CollectionCount(0);gen1=GC.CollectionCount(1);gen2=GC.CollectionCount(2);slow=MainThreadStages.SlowFlushCount;}
        // Returns a record when the gap since the previous call exceeds the
        // threshold; otherwise null. Always advances the baseline.
        public JObject Observe(double nowMs)
        {
            JObject record=null;
            if(last>=0&&nowMs-last>thresholdMs)
                record=new JObject{["kind"]="main_thread_stall",["gap_ms"]=nowMs-last,["previous_update_mono_ms"]=last,["observed_mono_ms"]=nowMs,["threshold_ms"]=thresholdMs,
                    ["gc_gen0_collections"]=GC.CollectionCount(0)-gen0,["gc_gen1_collections"]=GC.CollectionCount(1)-gen1,["gc_gen2_collections"]=GC.CollectionCount(2)-gen2,
                    ["managed_heap_bytes"]=GC.GetTotalMemory(false),["slow_flushes"]=MainThreadStages.SlowFlushCount-slow,["stages"]=MainThreadStages.Recent()};
            last=nowMs;Capture();return record;
        }
    }
}
