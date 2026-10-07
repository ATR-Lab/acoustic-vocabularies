using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using AcousticVocab.StateSources;
using AcousticVocab.StateIntegration;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace AcousticVocab.PlayModeTests
{
    public sealed class StateHostLifecycleTests
    {
        sealed class ReadySource : IRobotStateSource
        {
            public string Kind=>"snapshot";public bool Stale=>false;
            public double SampleAgeSeconds=>0;public double LastSimTime=>0;public bool ResetConfirmed=>true;
            public event Action<SourceEvent> Event { add { } remove { } }
            public SceneFrame Render(double now)=>null;
            public bool ConfirmReset(SceneFrame neutral,double now)=>true;
        }
        [UnityTest] public IEnumerator DisablingInitializedHostRevokesGrantAndReenableDoesNotRecover()
        {
            var obj=new GameObject("State host lifecycle test");var host=obj.AddComponent<StateSourceHost>();
            // Inject only the pre-existing initialized state; exercise actual
            // Unity disable/enable callbacks before Start can load private files.
            typeof(StateSourceHost).GetField("source",BindingFlags.Instance|BindingFlags.NonPublic).SetValue(host,new ReadySource());
            typeof(StateSourceHost).GetField("confirmedAtBoundary",BindingFlags.Instance|BindingFlags.NonPublic).SetValue(host,true);
            var events=new List<SourceEvent>();host.Event+=events.Add;
            Assert.That(host.Initialized,Is.True);
            LogAssert.Expect(LogType.Error,"STATE_SOURCE_FAULT STATE_HOST_DISABLED");host.enabled=false;
            Assert.That(host.Initialized,Is.False);Assert.That(host.CheckExposureReady(),Is.False);Assert.That(host.ConfirmReset(),Is.False);
            Assert.That(events.Count,Is.EqualTo(1));Assert.That(events[0].Code,Is.EqualTo("STATE_HOST_DISABLED"));
            host.enabled=true;yield return null;
            Assert.That(host.Initialized,Is.False);Assert.That(host.ResetConfirmed,Is.False);
            UnityEngine.Object.Destroy(obj);yield return null;
        }
    }
}
