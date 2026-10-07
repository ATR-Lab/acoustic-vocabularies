using System;
using System.IO;
using System.Threading;
using AcousticVocab.DataLogging;
using Newtonsoft.Json.Linq;

// Synthetic process-kill probe of the actual Unity-built durable writer DLL.
// The supervisor kills only this owned process after READY; not a player/device test.
static class Program
{
    static int Main(string[] args)
    {
        string raw=args[1];var id=new DataIdentity(new string('1',32),"SYNTHETIC","DEMO","synthetic-station","engineering-test",new string('a',64));
        if(args[0]=="write")
        {
            using var journal=new DataJournal(raw,id,new string('3',32),()=>1);
            journal.Append(new EventDraft("panel_response",new EventContext("synthetic-crash","synthetic-crash"),new JObject{["kind"]="response",["observed_mono_ms"]=1,["mode"]="FullMessage",["role"]="Command",["input"]=JValue.CreateNull(),["selected_target"]="A",["selected_action"]="ADD_ONE",["response_code"]="commit",["response_target"]="A",["response_action"]="ADD_ONE"}));
            using(var f=new FileStream(raw+".ready",FileMode.CreateNew,FileAccess.Write,FileShare.Read)){f.WriteByte(1);f.Flush(true);}
            Thread.Sleep(Timeout.Infinite);return 2;
        }
        if(args[0]=="verify")
        {
            using(var journal=new DataJournal(raw,id,new string('4',32),()=>0)){}
            var snapshot=DataJournal.Verify(raw,id);var rows=DataDeriver.Derive(snapshot,id);
            if(rows.Trials.Count!=1||rows.Trials[0]["response_code"]!="commit"||rows.Trials[0]["interrupted"]!="true")return 1;
            Console.WriteLine("PASS: owned process killed after durable Commit; record preserved; interrupted attempt retained");return 0;
        }
        return 3;
    }
}
