using System;
using System.Collections.Generic;
using AcousticVocab.SessionEngine;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.Teaching.Tests
{
    public sealed class TeachingCleanupTests
    {
        [Test]public void ThrowingJournalAndViewCannotSkipAudioBackendOrDetach()
        {
            var stages=new List<string>();var first=new SessionFault("LESSON_JOURNAL_FAILED");
            var result=TeachingCleanup.Attempt(()=>{stages.Add("journal");throw first;},
                ()=>{stages.Add("hide");throw new InvalidOperationException();},()=>stages.Add("panel"),
                ()=>stages.Add("audio_abort"),()=>stages.Add("backend_interrupt"),()=>stages.Add("detach"));
            Assert.That(stages,Is.EqualTo(new[]{"journal","hide","panel","audio_abort","backend_interrupt","detach"}));
            Assert.That(result,Is.SameAs(first));Assert.That(Assert.Throws<SessionFault>(()=>TeachingCleanup.ThrowFirst(result)).Code,Is.EqualTo("LESSON_JOURNAL_FAILED"));
        }
        [Test]public void LaterOwnersAreDisposedAfterEarlierOwnerFails()
        {
            int disposed=0;var result=TeachingCleanup.Attempt(()=>{disposed++;throw new Exception();},()=>disposed++,()=>disposed++);
            Assert.That(disposed,Is.EqualTo(3));Assert.That(TeachingCleanup.Code(result),Is.EqualTo("LESSON_CLEANUP_FAILED"));
        }
        [Test]public void FaultingObserverCannotPreventLaterFaultNotificationOrUnlatchHost()
        {
            var root=new GameObject("Synthetic teaching failure");var host=root.AddComponent<TeachingSessionHost>();int calls=0;
            host.Faulted+=_=>throw new SessionFault("LESSON_OBSERVER_FAILED");host.Faulted+=_=>calls++;
            Assert.DoesNotThrow(()=>host.Fail("LESSON_JOURNAL_FAILED"));host.Fail("SECOND_FAILURE");
            Assert.That(calls,Is.EqualTo(1));Assert.That(host.FaultCode,Is.EqualTo("LESSON_JOURNAL_FAILED"));
            Assert.That(host.CleanupFailureCode,Is.EqualTo("LESSON_OBSERVER_FAILED"));Assert.That(host.Installed,Is.False);
            UnityEngine.Object.DestroyImmediate(root);
        }
        [Test]public void DeferredDestroyAfterUninstallCannotAbortSubsequentSharedPlayerOwner()
        {
            var playerObject=new GameObject("Shared audio owner");var player=playerObject.AddComponent<AcousticVocab.StudyAudio.AudioPlayer>();
            var root=new GameObject("Released teaching owner");var host=root.AddComponent<TeachingSessionHost>();host.player=player;
            host.Uninstall();Assert.That(host.Installed,Is.False);
            // Abort has a real irreversible failure side effect even without a
            // playing clip; reset it to represent the subsequent owner's Configure.
            var failed=typeof(AcousticVocab.StudyAudio.AudioPlayer).GetField("failed",System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.NonPublic);
            failed.SetValue(player,false);host.SendMessage("OnApplicationFocus",false);host.enabled=false;UnityEngine.Object.DestroyImmediate(root);
            Assert.That((bool)failed.GetValue(player),Is.False,"Released host must never call Abort on its former player");UnityEngine.Object.DestroyImmediate(playerObject);
        }
    }
}
