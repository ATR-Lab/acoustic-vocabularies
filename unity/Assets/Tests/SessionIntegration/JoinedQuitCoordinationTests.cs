using System.Linq;
using AcousticVocab.Foundation;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.SessionIntegration.Tests
{
    public sealed class JoinedQuitCoordinationTests
    {
        [Test]public void QuitHookClosesOnceWithoutAVisitAndNeverInventsATerminalStatus()
        {
            var go=new GameObject("Joined quit coordination test");
            try
            {
                var host=go.AddComponent<JoinedEngineeringBootstrap>();
                var first=host.CoordinateQuit("test_quit");
                Assert.That(first,Is.Not.Null);Assert.That(first.All(x=>x.Outcome=="completed"||x.Outcome=="cancelled"||x.Outcome=="faulted"||x.Outcome=="timed_out"),Is.True);
                Assert.That(host.StatusCode,Is.EqualTo("JOIN_NOT_STARTED"),"Quit without a visit records no fault or completion");
                Assert.That(host.CoordinateQuit("again"),Is.Empty,"A second quit signal cannot rerun cleanup or wait again");
            }
            finally{Object.DestroyImmediate(go);}
        }
    }
}
