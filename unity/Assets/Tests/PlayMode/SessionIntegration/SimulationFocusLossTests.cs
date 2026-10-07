using System.Collections;
using System.Collections.Generic;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace AcousticVocab.SessionIntegration.PlayModeTests
{
    // #81 headset_disconnect software path: the hook delivers the same focus
    // message Unity sends for an OS focus change to every live component, so each
    // component's own handler (audio abort, panel latch, host fail) responds.
    public sealed class SimulationFocusLossTests
    {
        sealed class FocusProbe:MonoBehaviour{public readonly List<bool> Seen=new List<bool>();void OnApplicationFocus(bool focused)=>Seen.Add(focused);}
        readonly List<GameObject> objects=new List<GameObject>();
        [UnityTearDown]public IEnumerator Cleanup(){foreach(var o in objects)if(o!=null)Object.Destroy(o);objects.Clear();yield return null;}
        [UnityTest]public IEnumerator FocusLossIsDeliveredOnceToEveryLiveComponentHandler()
        {
            var a=new GameObject("Focus probe A");objects.Add(a);var first=a.AddComponent<FocusProbe>();
            var b=new GameObject("Focus probe B");objects.Add(b);var second=b.AddComponent<FocusProbe>();var extra=b.AddComponent<FocusProbe>();
            var hidden=new GameObject("Inactive probe");objects.Add(hidden);var inactive=hidden.AddComponent<FocusProbe>();hidden.SetActive(false);
            yield return null;first.Seen.Clear();second.Seen.Clear();extra.Seen.Clear();
            Assert.That(NativeFaultTargets.DispatchFocusLoss(),Is.GreaterThanOrEqualTo(2));
            Assert.That(first.Seen,Is.EqualTo(new[]{false}));Assert.That(second.Seen,Is.EqualTo(new[]{false}));Assert.That(extra.Seen,Is.EqualTo(new[]{false}),"One message per GameObject reaches each component once");
            Assert.That(inactive.Seen,Is.Empty,"Inactive objects are not live handlers");
        }
    }
}
