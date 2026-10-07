using System.Collections;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.StateSources;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace AcousticVocab.PlayModeTests
{
    public sealed class StateSourceTests
    {
        static SceneFrame Frame(long seq,double seconds) => new SceneFrame(new string('a',32),seq,
            (ulong)(seconds*1e9),seconds,seq,"synthetic",new double[43],
            new[]{new SceneObject("card",Vector3.zero,Quaternion.identity,true,true,new JObject { ["card_face"]=0 })});
        IEnumerator Gap(double seconds,int faults)
        {
            var source=new LiveIsaacSource(0,0); var events=new List<SourceEvent>(); source.Event+=events.Add;
            source.Receive(Frame(0,0),0,0); var initial=source.Render(0);
            // Injected host clock advances deterministically once per actual
            // Unity play-mode frame. This is not a network/headset measurement.
            for(double now=1d/90;now<seconds;now+=1d/90)
            { yield return null; Assert.That(source.Render(now),Is.SameAs(initial)); }
            source.Receive(Frame(1,seconds),seconds,seconds); source.Render(seconds);
            Assert.That(events.Count(e=>e.Code=="STATE_STALE"),Is.EqualTo(faults));
            if(faults==1) Assert.That(events.Single(e=>e.Code=="STATE_RECOVERED").DurationSeconds,Is.EqualTo(seconds).Within(1d/90));
        }
        [UnityTest] public IEnumerator Gap200ms() => Gap(.2,0);
        [UnityTest] public IEnumerator Gap300ms() => Gap(.3,1);
        [UnityTest] public IEnumerator Gap2s() => Gap(2,1);
    }
}
