using AcousticVocab.StateIntegration;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Tests
{
    public sealed class ControlHealthGateTests
    {
        const string Pin="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        double now;
        JObject Sample(double host=10,string mode="teaching")=>new JObject{["control_session_id"]=Pin,["mode"]=mode,["paused"]=false,["stopped"]=false,["fault"]=null,["demo_active"]=false,["publisher_ready"]=true,["neutral_verification_age_ms"]=10,["publisher_age_ms"]=15,["health_sample_host_mono_ms"]=host,["exposure_ready"]=mode=="test",["public_stream_recovered"]=false};
        ControlHealthGate Gate(string mode="teaching")=>new ControlHealthGate(Pin,mode,()=>now);
        [Test]public void RequiresProgressionAndIncludesRoundTripAndElapsedTime()
        {var g=Gate();now=20;g.Observe(Sample(),0,20);Assert.That(g.Fresh,Is.False);now=40;g.Observe(Sample(20),20,40);Assert.That(g.Fresh,Is.True);now=256;Assert.That(g.Fresh,Is.False);}
        [Test]public void DuplicateHealthCannotRefreshFreshness()
        {var g=Gate();now=20;g.Observe(Sample(),0,20);now=40;g.Observe(Sample(20),20,40);now=240;g.Observe(Sample(20),220,240);now=256;Assert.That(g.Fresh,Is.False);}
        [TestCase("publisher_ready")][TestCase("exposure_ready")]public void ProtectedModeRequiresPublisherAndExposureReadiness(string flag)
        {var g=Gate("test");now=20;g.Observe(Sample(mode:"test"),0,20);var row=Sample(20,"test");row[flag]=false;g.Observe(row,0,20);Assert.That(g.Fresh,Is.False);}
        [Test]public void TeachingDoesNotMisreadProtectedOnlyExposureFlag()
        {var g=Gate();now=20;g.Observe(Sample(),0,20);g.Observe(Sample(20),0,20);Assert.That(g.Fresh,Is.True);}
        [TestCase("control_session_id")][TestCase("publisher_age_ms")][TestCase("neutral_verification_age_ms")][TestCase("paused")]
        public void WrongTypesOrSessionInvalidate(string field)
        {var g=Gate();now=20;g.Observe(Sample(),0,20);var row=Sample(20);row[field]="wrong";Assert.Throws<ControlFault>(()=>g.Observe(row,0,20));Assert.That(g.Fresh,Is.False);}
        [Test]public void UnknownKeyRejected()
        {var g=Gate();var row=Sample();row["target"]="A";Assert.Throws<ControlFault>(()=>g.Observe(row,0,20));}
        [Test]public void NullAgesCannotBecomeReady()
        {var g=Gate();now=20;g.Observe(Sample(),0,20);var row=Sample(20);row["neutral_verification_age_ms"]=null;g.Observe(row,0,20);Assert.That(g.Fresh,Is.False);}
        [Test]public void HostClockRegressionRejected()
        {var g=Gate();g.Observe(Sample(20),0,20);Assert.Throws<ControlFault>(()=>g.Observe(Sample(19),0,20));}
        [Test]public void LongRoundTripRefusedEvenIfReportedAgesAreZero()
        {var g=Gate();Assert.Throws<ControlFault>(()=>g.Observe(Sample(),0,251));}
        [TestCase("ws://192.0.2.1:9000/commands")][TestCase("ws://127.0.0.1:9000/state")][TestCase("ws://127.0.0.1:9000/commands?mode=test")]
        public void EndpointCannotReachPublicStateOrExternalHosts(string endpoint)
        {Assert.Throws<ControlFault>(()=>new PrivateModeResetClient(endpoint,Pin,"test",_=>{}));}
    }
}
