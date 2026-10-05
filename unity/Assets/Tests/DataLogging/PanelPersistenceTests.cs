using System;
using System.IO;
using System.Reflection;
using AcousticVocab.ResponsePanel;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.DataLogging.Tests
{
    public sealed class PanelPersistenceTests
    {
        [Test] public void SubscriberFailureAfterPanelFlushPreventsCommitConfirmation()
        {
            string directory=SyntheticData.Folder("panel-subscriber-failure");var go=new GameObject("Synthetic panel persistence test");go.SetActive(false);var controller=go.AddComponent<ResponsePanelController>();
            var journal=new PanelJournal(directory,new JObject{["qualification"]="synthetic_test"},"synthetic",new JObject{["engineering_mode"]="disabled"});
            typeof(ResponsePanelController).GetField("journal",BindingFlags.NonPublic|BindingFlags.Instance).SetValue(controller,journal);
            var method=typeof(ResponsePanelController).GetMethod("PersistProcess",BindingFlags.NonPublic|BindingFlags.Instance);
            var state=new ResponseState(()=>1,p=> {try{method.Invoke(controller,new object[]{p});}catch(TargetInvocationException e){throw e.InnerException;}});
            bool confirmed=false;state.Responded+=_=>confirmed=true;controller.ProcessRecorded+=p=>{if(p.Kind=="commit")throw new IOException("synthetic downstream persistence failure");};
            try
            {
                state.Open(new PanelRequest("synthetic-attempt",PanelMode.FullMessage,PanelRole.Command,0));state.SelectTarget("A");state.SelectAction("ADD_ONE");Assert.Throws<IOException>(()=>state.Commit());
                Assert.That(state.Aborted,Is.True);Assert.That(state.Locked,Is.True);Assert.That(state.Result,Is.Null);Assert.That(confirmed,Is.False);Assert.That(state.Commit(),Is.False);
            }
            finally{journal.Dispose();UnityEngine.Object.DestroyImmediate(go);}
            Assert.That(File.ReadAllText(Directory.GetFiles(directory,"*.jsonl")[0]),Does.Contain("\"event\":\"commit\""),"The original flushed decision evidence is preserved despite failed confirmation");
        }
    }
}
