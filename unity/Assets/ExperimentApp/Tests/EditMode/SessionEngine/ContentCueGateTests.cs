using System;
using System.Collections.Generic;
using System.IO;
using NUnit.Framework;

namespace AcousticVocab.SessionEngine.Tests
{
    public sealed class ContentCueGateTests
    {
        static SlotReadiness Ready(bool reset=true)=>new SlotReadiness(true,true,reset,true,true,true,true,true);
        static SlotContext Context()=>new SlotContext(new SlotItem("DEMO-item","atomic_lesson","K-a1","action",null,"teaching",false,20,3,1),1750,null);
        [TestCase("LESSON_CUE_REFUSED")][TestCase("MENU_CUE_REFUSED")]
        public void FailureCapturesOneOriginalSnapshotBeforeObserverCanChangeState(string code)
        {
            int reads=0,clocks=0;bool reset=false;ContentCueGateRefusal receipt=null;
            var gate=new ContentCueGate(code,value=>{receipt=value;reset=true;});
            var error=Assert.Throws<SessionFault>(()=>gate.Check(Context(),()=>true,()=>{reads++;return Ready(reset);},()=>true,()=>{clocks++;return 1000;}));
            Assert.That(error.Code,Is.EqualTo(code));Assert.That(reads,Is.EqualTo(1));Assert.That(clocks,Is.EqualTo(1));
            Assert.That(receipt.Readiness.ResetAcknowledged,Is.False);Assert.That(receipt.CheckedMonoMs,Is.EqualTo(1000));Assert.That(receipt.Context.OnsetMonoMs,Is.EqualTo(1750));Assert.That(gate.Failed,Is.False);
        }
        [TestCase("LESSON_CUE_REFUSED")][TestCase("MENU_CUE_REFUSED")]
        public void ThrowingObserverRetainsPrimaryRefusalAndLatchesFurtherAdmission(string code)
        {
            int reads=0;var gate=new ContentCueGate(code,_=>throw new IOException("synthetic private failure"));
            var first=Assert.Throws<SessionFault>(()=>gate.Check(Context(),()=>true,()=>{reads++;return Ready(false);},()=>true,()=>1000));
            Assert.That(first.Code,Is.EqualTo(code));Assert.That(gate.Failed,Is.True);
            var next=Assert.Throws<SessionFault>(()=>gate.Check(Context(),()=>true,()=>{reads++;return Ready();},()=>true,()=>1100));
            Assert.That(next.Code,Is.EqualTo(code));Assert.That(reads,Is.EqualTo(1));
        }
        [Test]public void TeachingInterruptedPrefixDoesNotReadReadinessOrClock()
        {
            int observed=0,read=0,clock=0;bool interrupted=true;var gate=new ContentCueGate("LESSON_CUE_REFUSED",_=>observed++);
            Assert.Throws<SessionFault>(()=>gate.Check(Context(),()=>!interrupted,()=>{read++;return Ready();},()=>true,()=>{clock++;return 1000;}));
            Assert.That(read+clock+observed,Is.Zero);
        }
        [Test]public void MenuInterruptedSuffixFollowsOriginalReadinessWithoutInventingGateRefusal()
        {
            int observed=0;var calls=new List<string>();var gate=new ContentCueGate("MENU_CUE_REFUSED",_=>observed++);
            Assert.Throws<SessionFault>(()=>gate.Check(Context(),()=>{calls.Add("authorization");return true;},()=>{calls.Add("readiness");return Ready();},()=>{calls.Add("interrupted");return false;},()=>{calls.Add("clock");return 1000;}));
            Assert.That(calls,Is.EqualTo(new[]{"authorization","clock","readiness","interrupted"}));Assert.That(observed,Is.Zero);
        }
        [Test]public void GoodReadinessHasNoObserverSideEffectOrCachedGrant()
        {
            int observed=0,reads=0;var gate=new ContentCueGate("LESSON_CUE_REFUSED",_=>observed++);
            gate.Check(Context(),()=>true,()=>{reads++;return Ready();},()=>true,()=>1000);
            Assert.That(observed,Is.Zero);Assert.That(reads,Is.EqualTo(1));
            Assert.Throws<SessionFault>(()=>gate.Check(Context(),()=>true,()=>{reads++;return Ready(false);},()=>true,()=>1200));Assert.That(reads,Is.EqualTo(2));Assert.That(observed,Is.EqualTo(1));
        }
        [Test]public void ReentrantObserverCannotGrantAnotherCue()
        {
            ContentCueGate gate=null;gate=new ContentCueGate("MENU_CUE_REFUSED",_=>gate.Check(Context(),()=>true,()=>Ready(),()=>true,()=>1000));
            var error=Assert.Throws<SessionFault>(()=>gate.Check(Context(),()=>true,()=>Ready(false),()=>true,()=>1000));Assert.That(error.Code,Is.EqualTo("MENU_CUE_REFUSED"));Assert.That(gate.Failed,Is.True);
        }
        [Test]public void ReadinessExceptionRetainsItsExistingCauseWithoutDiagnosticReevaluation()
        {
            int calls=0;var gate=new ContentCueGate("LESSON_CUE_REFUSED",_=>calls++);
            var error=Assert.Throws<SessionFault>(()=>gate.Check(Context(),()=>true,()=>throw new SessionFault("EXISTING_READ_FAILURE"),()=>true,()=>1000));Assert.That(error.Code,Is.EqualTo("EXISTING_READ_FAILURE"));Assert.That(calls,Is.Zero);
        }
    }
}
