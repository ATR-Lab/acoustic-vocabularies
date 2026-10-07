using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using AcousticVocab.DataLogging;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
namespace AcousticVocab.SessionIntegration
{
    // The journal's close intent precedes resource disposal. Only this separate
    // receipt can attest that disposal and immutable export actually finished.
    public sealed class SimulationRunClosure
    {
        public const string CompleteStatus="JOIN_COMPLETE_FORMS_RECORDED";
        readonly string path,nonce,configPin,capabilityPin,sourceCommit;readonly int processId;readonly Func<ExportBundle> export;readonly Func<double> clock;
        bool finished;string status;
        public SimulationRunClosure(string path,string nonce,string configPin,string capabilityPin,string sourceCommit,int processId,Func<ExportBundle> export,Func<double> clock)
        {this.path=path;this.nonce=nonce;this.configPin=configPin;this.capabilityPin=capabilityPin;this.sourceCommit=sourceCommit;this.processId=processId;this.export=export;this.clock=clock;}
        public string Finish(string requestedStatus,Action closeIntent,IEnumerable<Action> cleanup)
        {
            if(finished)return status;finished=true;status=requestedStatus;
            bool cleanupOkay=true,exportOkay=false;string exportPin=null;
            void Failure(string code){if(status==CompleteStatus)status=code;}
            try{closeIntent();}catch{cleanupOkay=false;Failure("JOIN_DISPOSE_FAILED");}
            foreach(var action in cleanup)try{action();}catch{cleanupOkay=false;Failure("JOIN_DISPOSE_FAILED");}
            try{var bundle=export();bundle.VerifyAll();exportPin=bundle.ManifestSha256;exportOkay=true;}catch{Failure("JOIN_EXPORT_FAILED");}
            try
            {
                double now=clock();if(!double.IsFinite(now)||now<0)throw new IOException("JOIN_RESULT_CLOCK");
                var result=new JObject{["version"]=1,["scope"]="SIMULATION_TEST",["session_nonce"]=nonce,
                    ["config_sha256"]=configPin,["simulation_capability_sha256"]=capabilityPin,["source_commit"]=sourceCommit,
                    ["process_id"]=processId,["status"]=status,["complete"]=status==CompleteStatus&&cleanupOkay&&exportOkay,
                    ["cleanup_succeeded"]=cleanupOkay,["export_succeeded"]=exportOkay,["export_manifest_sha256"]=exportPin,
                    ["host_mono_ms"]=now,["participant_admission"]=false};
                byte[] bytes=Encoding.UTF8.GetBytes(result.ToString(Formatting.None)+"\n");
                // Preserve incomplete writes for diagnosis. A final receipt is
                // published only after flush and close, and never overwritten.
                string pending=path+".pending";
                using(var stream=new FileStream(pending,FileMode.CreateNew,FileAccess.Write,FileShare.Read,4096,FileOptions.WriteThrough))
                {stream.Write(bytes,0,bytes.Length);stream.Flush(true);}
                File.Move(pending,path);
            }
            catch{Failure("JOIN_RESULT_WRITE_FAILED");}
            return status;
        }
    }
}
