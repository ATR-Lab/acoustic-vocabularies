using System;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.SessionEngine;
using NUnit.Framework;

namespace AcousticVocab.Teaching.Tests
{
    // The storyboard playhead is a read-only view of the aligned highlight.
    public sealed class LessonPlayheadTests
    {
        [TestCase(false, false)][TestCase(true, true)][TestCase(true, false)]
        [Category("REQ_LES_08"), Category("REQ_LES_05")]
        public void StaticAndAtomicLessonsNeverYieldAPlayhead(bool atomic, bool aligned)
        {
            var run = new TimelineRun(atomic, aligned); run.Complete();
            Assert.That(run.Fractions.All(f => f == null), Is.True);
        }

        [Test, Category("REQ_LES_07"), Category("REQ_LES_06")]
        public void AlignedPlayheadIsReadOnlyAndExistsExactlyWhileALabelIsLit()
        {
            var aligned = new TimelineRun(false, true); var plain = new TimelineRun(false, false);
            aligned.Complete(); plain.Complete();
            Assert.That(aligned.Fractions.Count, Is.EqualTo(aligned.Highlights.Count));
            for (int i = 0; i < aligned.Fractions.Count; i++)
            {
                Assert.That(aligned.Fractions[i].HasValue, Is.EqualTo(aligned.Highlights[i] != LessonHighlight.None), "tick " + i);
                if (aligned.Fractions[i].HasValue) Assert.That(aligned.Fractions[i].Value, Is.InRange(0f, 1f));
                if (i > 0 && aligned.Fractions[i].HasValue && aligned.Fractions[i - 1].HasValue && aligned.Highlights[i] == aligned.Highlights[i - 1])
                    Assert.That(aligned.Fractions[i].Value, Is.GreaterThanOrEqualTo(aligned.Fractions[i - 1].Value));
            }
            Assert.That(aligned.Fractions.Any(f => f.HasValue), Is.True);
            // Querying the playhead adds no record: the logs differ only in highlight rows.
            string Key(LessonEvent e) => string.Join("|", e.Kind, e.MonoMs, e.ExpectedMonoMs, e.PresentationIndex, e.FeedbackContentId);
            Assert.That(aligned.Events.Where(x => !x.Kind.StartsWith("highlight", StringComparison.Ordinal)).Select(Key), Is.EqualTo(plain.Events.Select(Key)));
        }

        // A lesson timeline driven like the existing EditMode timeline tests, sampling
        // the read-only playhead and highlight on every tick.
        sealed class TimelineRun
        {
            internal readonly List<LessonEvent> Events = new List<LessonEvent>();
            internal readonly List<float?> Fractions = new List<float?>();
            internal readonly List<LessonHighlight> Highlights = new List<LessonHighlight>();
            readonly LessonTimeline timeline; readonly SlotContext context;
            internal TimelineRun(bool atomic, bool aligned)
            {
                context = new SlotContext(new SlotItem("DEMO-lesson", atomic ? "atomic_lesson" : "message_lesson", atomic ? "K-a1" : "K-a1-r1", atomic ? "action" : null, "structured", "teaching", false, atomic ? 20 : 24, 3, 1), 750, null);
                string hash = new string('a', 64);
                timeline = new LessonTimeline(context, aligned, 24000, atomic ? 0 : 33600, "DEMO-meaning", hash, atomic ? null : hash, atomic ? null : hash, Events.Add);
                timeline.Start(0);
            }
            internal void Complete()
            {
                bool atomic = context.Item.TrialType == "atomic_lesson";
                double[] offsets = atomic ? new[] { 0d, 6000, 14000 } : new[] { 0d, 8000, 18000 };
                double duration = atomic ? 500 : 1400;
                for (double now = 5; now <= context.EndMonoMs; now += 5)
                {
                    timeline.Tick(now);
                    for (int i = 0; i < 3; i++)
                    {
                        if (now == 750 + offsets[i]) timeline.Onset(context.AudioRequestIds[i], now, 10, now);
                        if (now == 750 + offsets[i] + duration) timeline.Completed(context.AudioRequestIds[i], now);
                    }
                    if (now == 750 + offsets[1] + 100) timeline.Response("DEMO-correct", now);
                    if (now == 750 + context.Item.ResponseClosesSeconds * 1000) timeline.RequestReset();
                    Fractions.Add(timeline.PlayheadAt(now)); Highlights.Add(timeline.Highlight);
                }
            }
        }
    }
}
