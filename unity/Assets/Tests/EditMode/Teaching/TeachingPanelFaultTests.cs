using System;
using System.Collections.Generic;
using System.Reflection;
using System.Runtime.Serialization;
using AcousticVocab.ResponsePanel;
using AcousticVocab.SessionEngine;
using AcousticVocab.StateIntegration;
using AcousticVocab.StudyAudio;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.Teaching.Tests
{
    // Attempt simulation-test-A-D0-023-isaac2-017: the response panel faulted
    // with reason `foundation_fault` (the foundation had lost focus) and the
    // lesson reported LESSON_INPUT_LOST, discarding the panel's reason.
    public sealed class TeachingPanelFaultTests
    {
        sealed class Backend:ITeachingBackend
        {public void Pump(){}public void RequestTeachingMode(){}public bool TeachingModeAcknowledged=>true;public bool NeutralHoldHealthy=>true;public string RequestReset()=>"r";public bool ResetAcknowledged(string id)=>true;public void Interrupt(){}}
        sealed class View:ITeachingView
        {public void Prepare(TeachingDisplay display){}public void Show(string owner,TeachingDisplay display,string feedbackText){}public void Hide(string owner){}public void Highlight(string owner,LessonHighlight highlight){}}
        [TestCase("foundation_fault","LESSON_PANEL_FOUNDATION_FAULT")]
        [TestCase("input_lost","LESSON_INPUT_LOST")]
        [TestCase("application_focus_lost","LESSON_FOCUS_LOST")]
        [TestCase("application_paused","LESSON_PANEL_APPLICATION_PAUSED")]
        [TestCase("panel_runtime_or_log_fault","LESSON_PANEL_PANEL_RUNTIME_OR_LOG_FAULT")]
        [TestCase("C:\\private path/free text","LESSON_PANEL_FAULT")]
        public void ActualPanelFaultReachesTheLessonWithItsBoundedReason(string reason,string expected)
        {
            var go=new GameObject("Synthetic lesson panel fault");go.SetActive(false);
            try
            {
                var panel=go.AddComponent<ResponsePanelController>();var audio=go.AddComponent<AudioPlayer>();var source=go.AddComponent<StateSourceHost>();
                var catalog=(TeachingCatalog)FormatterServices.GetUninitializedObject(typeof(TeachingCatalog));var faults=new List<string>();
                using var factory=new TeachingContentFactory(catalog,null,new Backend(),audio,panel,source,new View(),()=>true,_=>{},_=>{},_=>{},faults.Add);
                typeof(ResponsePanelController).GetMethod("Fail",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(panel,new object[]{reason});
                Assert.That(faults,Is.EqualTo(new[]{expected}));Assert.That(new SessionFault(expected).Code,Is.EqualTo(expected),"A valid bounded session fault code");
            }
            finally{UnityEngine.Object.DestroyImmediate(go);}
        }
        [Test]public void UnboundedOrMissingReasonsNeverEnterTheCode()
        {
            Assert.That(TeachingContentFactory.PanelFaultCode(null),Is.EqualTo("LESSON_PANEL_FAULT"));
            Assert.That(TeachingContentFactory.PanelFaultCode(new string('a',49)),Is.EqualTo("LESSON_PANEL_"+new string('A',49)));
            Assert.That(TeachingContentFactory.PanelFaultCode(new string('a',50)),Is.EqualTo("LESSON_PANEL_FAULT"),"Over the bound");
            Assert.That(TeachingContentFactory.PanelFaultCode("Foundation_Fault"),Is.EqualTo("LESSON_PANEL_FAULT"),"Only the panel's lowercase code grammar");
        }
    }
}
