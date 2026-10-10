using System;
using System.IO;
using System.Linq;
using AcousticVocab.Foundation;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.SessionIntegration.Tests
{
    public sealed class MainThreadStallTests
    {
        [SetUp]public void Setup()=>MainThreadStages.ResetForTests();
        // Native attempts 007-010 lost 0.4-4.9 s of Unity main thread with no
        // record of what ran. A gap above the threshold must name the recent
        // stages, slow durable flushes and GC activity; a normal frame must not.
        [Test]public void GapAboveThresholdRecordsRecentStagesAndGcCounters()
        {
            var monitor=new MainThreadStallMonitor(250);
            Assert.That(monitor.Observe(1000),Is.Null,"First observation only sets the baseline");
            MainThreadStages.Mark("staged_pump");Assert.That(monitor.Observe(1240),Is.Null,"240 ms is not a stall");
            MainThreadStages.Mark("preflight_seal_before_post_menu");GC.Collect(0);
            var stall=monitor.Observe(1240+4808);
            Assert.That(stall,Is.Not.Null);Assert.That((string)stall["kind"],Is.EqualTo("main_thread_stall"));
            Assert.That((double)stall["gap_ms"],Is.EqualTo(4808));Assert.That((double)stall["previous_update_mono_ms"],Is.EqualTo(1240));
            Assert.That((int)stall["gc_gen0_collections"],Is.GreaterThanOrEqualTo(1));Assert.That((long)stall["managed_heap_bytes"],Is.GreaterThan(0));
            var stages=((JArray)stall["stages"]).Select(x=>(string)x["stage"]).ToArray();
            Assert.That(stages.Last(),Is.EqualTo("preflight_seal_before_post_menu"),"The last stage before the gap is attributed");
            Assert.That(monitor.Observe(6060),Is.Null,"The baseline advances after a stall");
        }
        [Test]public void RingKeepsOnlyTheMostRecentMarkersInOrder()
        {
            for(int i=0;i<MainThreadStages.Capacity+5;i++)MainThreadStages.Mark("stage-"+i);
            var stages=MainThreadStages.Recent().Select(x=>(string)x["stage"]).ToArray();
            Assert.That(stages.Length,Is.EqualTo(MainThreadStages.Capacity));Assert.That(stages.First(),Is.EqualTo("stage-5"));Assert.That(stages.Last(),Is.EqualTo("stage-"+(MainThreadStages.Capacity+4)));
            var times=MainThreadStages.Recent().Select(x=>(double)x["mono_ms"]).ToArray();Assert.That(times,Is.Ordered);
        }
        [Test]public void InstrumentedFlushIsStillDurableAndFastFlushesAreNotRecorded()
        {
            string path=Path.Combine(Path.GetTempPath(),"stall-flush-"+Guid.NewGuid().ToString("N"));
            try
            {
                using(var stream=new FileStream(path,FileMode.CreateNew,FileAccess.Write,FileShare.Read)){stream.WriteByte(42);MainThreadStages.Flush(stream,"test");Assert.That(new FileInfo(path).Length,Is.EqualTo(1));}
                Assert.That(MainThreadStages.Recent().Any(x=>((string)x["stage"]).StartsWith("slow_flush:")),Is.EqualTo(MainThreadStages.SlowFlushCount>0));
            }
            finally{File.Delete(path);}
        }
    }
}
