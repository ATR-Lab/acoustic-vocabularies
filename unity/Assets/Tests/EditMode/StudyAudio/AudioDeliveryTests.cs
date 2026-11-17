using AcousticVocab.StudyAudio;
using NUnit.Framework;

namespace AcousticVocab.Tests.StudyAudio
{
    public sealed class AudioDeliveryTests
    {
        [Test] public void CoverageClipsBeforeOnsetAndAfterEndWithoutCountingExtraSamples()
        {
            var delivery=new AudioDelivery(1,96000);
            delivery.Observe(0,512,48000);Assert.That(delivery.CallbackCount,Is.Zero);
            for(int frame=-128;frame<96000;frame+=512) delivery.Observe(1+(double)frame/48000,512,48000);
            Assert.That(delivery.Status,Is.EqualTo(AudioDelivery.Complete));
            Assert.That(delivery.CoveredSamples,Is.EqualTo(96000));Assert.That(delivery.DeadlineFault(),Is.Null);
            Assert.That(delivery.FirstOutputCallbackDspSeconds,Is.EqualTo(1-128d/48000).Within(1e-12));
        }
        [Test] public void MissingCallbackIsNotSuccessfulPlayback()
        { var delivery=new AudioDelivery(1,96000);Assert.That(delivery.DeadlineFault(),Is.EqualTo("AUDIO_MISSING_PLAYBACK")); }
        [Test] public void DroppedMiddleBufferLatchesUnderrun()
        {
            var delivery=new AudioDelivery(1,96000);delivery.Observe(1,512,48000);
            delivery.Observe(1+1024d/48000,512,48000);delivery.Observe(1+512d/48000,512,48000);
            Assert.That(delivery.Status,Is.EqualTo(AudioDelivery.Underrun));Assert.That(delivery.CoveredSamples,Is.EqualTo(512));
        }
        [Test] public void DuplicateBufferCannotBeCountedTwice()
        {
            var delivery=new AudioDelivery(1,96000);delivery.Observe(1,512,48000);delivery.Observe(1,512,48000);
            Assert.That(delivery.DeadlineFault(),Is.EqualTo("AUDIO_CALLBACK_INVALID"));Assert.That(delivery.CoveredSamples,Is.EqualTo(512));
        }
        [Test] public void AdvancingButOverlappingBufferIsNotValidCoverage()
        {
            var delivery=new AudioDelivery(1,96000);delivery.Observe(1,512,48000);delivery.Observe(1+128d/48000,512,48000);
            Assert.That(delivery.Status,Is.EqualTo(AudioDelivery.InvalidCallback));Assert.That(delivery.CoveredSamples,Is.EqualTo(512));
        }
        [TestCase(double.NaN,512,48000)] [TestCase(1,0,48000)] [TestCase(1,512,44100)]
        public void InvalidCallbackLatchesFailure(double dsp,int frames,int rate)
        { var delivery=new AudioDelivery(1,96000);delivery.Observe(dsp,frames,rate);Assert.That(delivery.Status,Is.EqualTo(AudioDelivery.InvalidCallback)); }
        [Test] public void CallbackAfterEndCannotConcealMissingAudio()
        { var delivery=new AudioDelivery(1,96000);delivery.Observe(3,512,48000);Assert.That(delivery.Status,Is.EqualTo(AudioDelivery.Underrun)); }
        // Only a window that no callback reached or passed reports missing
        // playback, the sole software evidence that can confirm no onset.
        [Test, Category("REQ_INT_06"), NUnit.Framework.Property("Requirement","REQ-INT-06")]
        public void OnlyAnUntouchedWindowReportsMissingPlayback()
        {
            var silent=new AudioDelivery(1,96000);silent.Observe(.5,512,48000);
            Assert.That(silent.Status,Is.EqualTo(AudioDelivery.Pending));Assert.That(silent.DeadlineFault(),Is.EqualTo("AUDIO_MISSING_PLAYBACK"));
            var late=new AudioDelivery(1,96000);late.Observe(3,512,48000);
            Assert.That(late.CallbackCount,Is.Zero);Assert.That(late.CoveredSamples,Is.Zero);
            Assert.That(late.DeadlineFault(),Is.EqualTo("AUDIO_UNDERRUN"),"a callback after the window may carry a late start");
            var partial=new AudioDelivery(1,96000);partial.Observe(1,512,48000);
            Assert.That(partial.DeadlineFault(),Is.EqualTo("AUDIO_UNDERRUN"));
        }
    }
}
