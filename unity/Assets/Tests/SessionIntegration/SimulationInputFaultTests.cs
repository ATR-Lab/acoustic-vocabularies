using System;
using System.Collections.Generic;
using System.Reflection;
using System.Runtime.Serialization;
using AcousticVocab.Foundation;
using AcousticVocab.SessionEngine;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.SessionIntegration.Tests
{
    // attr-013 ended as the joined owner's generic JOIN_RUNTIME_FAILED. A simulated
    // input rejection or view exception must carry its own fault code, and the
    // exception (type plus code-shaped message only) must be journaled.
    public sealed class SimulationInputFaultTests
    {
        const BindingFlags Flags=BindingFlags.NonPublic|BindingFlags.Instance;
        static (object driver,List<JObject> rows) Driver()
        {
            var type=typeof(JoinedEngineeringBootstrap).Assembly.GetType("AcousticVocab.SessionIntegration.SimulationInputDriver",true);
            var rows=new List<JObject>();var authority=(SimulationTestAuthority)FormatterServices.GetUninitializedObject(typeof(SimulationTestAuthority));
            var driver=Activator.CreateInstance(type,Flags,null,new object[]{authority,null,new string('a',32),new Action<JObject>(rows.Add),false},null);return (driver,rows);
        }
        static bool Act(object driver,Func<bool> action)
        {try{return (bool)driver.GetType().GetMethod("Act",Flags).Invoke(driver,new object[]{"panel","B-C01-M1-V1-ML-03",action});}catch(TargetInvocationException e){throw e.InnerException;}}
        [Test]public void ViewExceptionBecomesSimulationInputFailedWithBoundedEvidence()
        {
            var (driver,rows)=Driver();
            var fault=Assert.Throws<SessionFault>(()=>Act(driver,()=>throw new InvalidOperationException("PANEL_GLYPH_DOES_NOT_FIT")));
            Assert.That(fault.Code,Is.EqualTo("SIMULATION_INPUT_FAILED"));
            Assert.That((string)rows[0]["kind"],Is.EqualTo("input_failure"));Assert.That((string)rows[0]["exception"],Is.EqualTo("InvalidOperationException:PANEL_GLYPH_DOES_NOT_FIT"));
            Assert.Throws<SessionFault>(()=>Act(driver,()=>throw new InvalidOperationException("C:\\private\\path detail")));
            Assert.That((string)rows[1]["exception"],Is.EqualTo("InvalidOperationException"),"Free text never enters the journal");
        }
        [Test]public void RejectionsAndSessionFaultsKeepTheirOwnMeaning()
        {
            var (driver,rows)=Driver();
            Assert.That(Act(driver,()=>false),Is.False,"The caller maps a rejection to SIMULATION_INPUT_REJECTED");
            Assert.That(Assert.Throws<SessionFault>(()=>Act(driver,()=>throw new SessionFault("SIMULATION_ARM_INVALID"))).Code,Is.EqualTo("SIMULATION_ARM_INVALID"));
            Assert.That(rows,Is.Empty);
        }
    }
}
