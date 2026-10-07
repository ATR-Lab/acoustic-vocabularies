using AcousticVocab.StateIntegration;
using NUnit.Framework;

namespace AcousticVocab.Tests
{
    public sealed class ControlPollCadenceTests
    {
        [TestCase(0,75)][TestCase(20,55)][TestCase(50,25)][TestCase(50.2,25)][TestCase(74,1)][TestCase(80.9215,1)][TestCase(200,1)]
        public void TotalCycleIncludesActualRequestDuration(double elapsed,int delay)
        {
            int actual=ControlPollCadence.DelayMilliseconds(1000,1000+elapsed);Assert.That(actual,Is.EqualTo(delay));Assert.That(actual,Is.InRange(1,75));
            Assert.That(elapsed+actual,Is.GreaterThanOrEqualTo(75));
            if(elapsed<74)Assert.That(elapsed+actual,Is.LessThan(76));
        }
        [Test]public void Native006RoundTripDoesNotAddAnother75Milliseconds()
        {
            const double actualRtt=80.9215,sourceAge=34.818907;
            double formerReceiptPeriod=actualRtt+75,newReceiptPeriod=actualRtt+ControlPollCadence.DelayMilliseconds(0,actualRtt);
            Assert.That(formerReceiptPeriod+actualRtt+sourceAge,Is.GreaterThan(250));
            Assert.That(newReceiptPeriod+actualRtt+sourceAge,Is.LessThan(250));
            // This is a cadence calculation, not evidence that future network
            // replies will meet that duration or grant actual native readiness.
        }
        [TestCase(-1,0)][TestCase(2,1)][TestCase(double.NaN,1)][TestCase(0,double.PositiveInfinity)]
        public void InvalidMonotonicTimesRefusePolling(double start,double end)
        {Assert.That(Assert.Throws<ControlFault>(()=>ControlPollCadence.DelayMilliseconds(start,end)).Code,Is.EqualTo("CONTROL_POLL_CLOCK"));}
    }
}
