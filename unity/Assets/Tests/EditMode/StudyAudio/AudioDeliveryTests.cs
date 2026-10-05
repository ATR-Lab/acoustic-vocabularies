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
        [TestCase(double.NaN,512,48000)] [TestCase(1,0,48000)] [TestCase(1,512,44100)]
        public void InvalidCallbackLatchesFailure(double dsp,int frames,int rate)
        { var delivery=new AudioDelivery(1,96000);delivery.Observe(dsp,frames,rate);Assert.That(delivery.Status,Is.EqualTo(AudioDelivery.InvalidCallback)); }
        [Test] public void CallbackAfterEndCannotConcealMissingAudio()
        { var delivery=new AudioDelivery(1,96000);delivery.Observe(3,512,48000);Assert.That(delivery.Status,Is.EqualTo(AudioDelivery.Underrun)); }
    }
}
