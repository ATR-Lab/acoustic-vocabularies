using System;
using System.IO;
using System.Text;
using AcousticVocab.Foundation;
using AcousticVocab.Orientation;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using UnityEngine;
namespace AcousticVocab.SessionIntegration
{
    [DefaultExecutionOrder(-100)]
    [DisallowMultipleComponent]
    public sealed class PreallocationEngineeringHost:MonoBehaviour
    {
        public OrientationHost orientation;public JoinedEngineeringBootstrap joined;
        string root,mailbox,receipts,screening,listHash;OrientationReceipt outcome;PreallocationHandoff gate;bool closed,handedOff;
        public string StatusCode{get;private set;}="PREALLOCATION_NOT_CONFIGURED";
        public bool ParticipantAdmission=>false;
        static string Argument(string key){var args=Environment.GetCommandLineArgs();for(int i=0;i<args.Length-1;i++)if(args[i]==key)return args[i+1];return null;}
        void Start()
        {
            try
            {
                if(orientation==null||joined==null||joined.enabled||!joined.requirePreallocation)throw new SessionFault("PREALLOCATION_SCENE_BINDING");
                string path=Argument("-preallocationConfig"),pin=Argument("-preallocationConfigSha256");
                if(path==null||pin==null)throw new SessionFault("PREALLOCATION_CONFIG_REQUIRED");
                path=Path.GetFullPath(path);root=Path.GetDirectoryName(path);var value=JoinedVisitArtifacts.Json(JoinedEngineeringConfig.ReadPinned(path,pin,16384,null));
                PreallocationHandoff.Keys(value,"version","scope","screening_id","station_id","protocol_version","allocation_list_sha256","mailbox_directory","receipt_directory");
                var station=orientation.foundation?.Configuration;
                PreallocationHandoff.Need(value["version"].Type==JTokenType.Integer&&(int)value["version"]==1&&(string)value["scope"]=="DEMO_ENGINEERING"&&station!=null&&JToken.DeepEquals(value["station_id"],station["station_id"])&&JToken.DeepEquals(value["protocol_version"],station["protocol_version"])&&PreallocationHandoff.Code((string)value["screening_id"])&&PreallocationHandoff.Hash((string)value["allocation_list_sha256"]),"PREALLOCATION_CONFIG_BINDING");
                screening=(string)value["screening_id"];listHash=(string)value["allocation_list_sha256"];mailbox=Confined((string)value["mailbox_directory"]);receipts=Confined((string)value["receipt_directory"]);
                orientation.BindScreening(screening);orientation.OutcomeRecorded+=Recorded;Report("PREALLOCATION_SILENT_ORIENTATION");
            }
            catch(SessionFault e){Fail(e.Code);}catch{Fail("PREALLOCATION_CONFIG_INVALID");}
        }
        string Confined(string relative)
        {
            if(string.IsNullOrEmpty(relative)||Path.IsPathRooted(relative)||relative.Contains("\\")||relative.Contains(":")||Array.Exists(relative.Split('/'),x=>x is "" or "." or ".."))throw new SessionFault("PREALLOCATION_PATH");
            string full=Path.GetFullPath(Path.Combine(root,relative));if(!full.StartsWith(root.TrimEnd(Path.DirectorySeparatorChar)+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase))throw new SessionFault("PREALLOCATION_PATH");JoinedEngineeringConfig.NoLinks(full);return full;
        }
        static void CreateNew(string path,byte[] bytes)
        {using(var f=new FileStream(path,FileMode.CreateNew,FileAccess.Write,FileShare.Read)){f.Write(bytes,0,bytes.Length);f.Flush(true);}}
        void Recorded(OrientationOutcome value)
        {
            if(closed)return;
            try
            {
                outcome=orientation.FinalizeOutcome(screening);gate=new PreallocationHandoff(outcome,listHash);
                JoinedEngineeringConfig.NoLinks(receipts);Directory.CreateDirectory(receipts);
                byte[] journal=File.ReadAllBytes(orientation.JournalPath);var proof=outcome.Json;
                if(PcmWave.Hash(journal)!=(string)proof["journal_sha256"]||journal.LongLength!=(long)proof["journal_bytes"])throw new SessionFault("PREALLOCATION_JOURNAL_PIN");
                CreateNew(Path.Combine(receipts,outcome.OrientationId+".journal.jsonl"),journal);
                CreateNew(Path.Combine(receipts,outcome.OrientationId+".receipt.json"),ReceiptCanonical.Bytes(proof));
                Report(outcome.Eligible?"PREALLOCATION_WAITING_OPERATOR_RECEIPT":"PREALLOCATION_NOT_ELIGIBLE");
            }
            catch{Fail("PREALLOCATION_OUTCOME_WRITE_FAILED");throw;}
        }
        byte[] FileBytes(JObject packet,string key,int maximum,out string path,out string pin)
        {var file=packet[key] as JObject;PreallocationHandoff.Keys(file,"path","sha256");path=Confined((string)file["path"]);pin=(string)file["sha256"];return JoinedEngineeringConfig.ReadPinned(path,pin,maximum,null);}
        void Update()
        {
            if(closed||handedOff||outcome?.Eligible!=true)return;
            try
            {
                string path=Path.Combine(mailbox,"handoff.local.json");JoinedEngineeringConfig.NoLinks(path);if(!File.Exists(path))return;
                var info=new FileInfo(path);if(info.Length<=0||info.Length>16384)throw new SessionFault("PREALLOCATION_HANDOFF_SIZE");
                var p=StationConfig.ParseStrict(File.ReadAllText(path,new UTF8Encoding(false,true)));
                PreallocationHandoff.Keys(p,"version","request_id","orientation_id","orientation_receipt_sha256","eligibility","reveal","package_hashes","joined_config");
                PreallocationHandoff.Need(p["version"].Type==JTokenType.Integer&&(int)p["version"]==1&&System.Text.RegularExpressions.Regex.IsMatch((string)p["request_id"]??"",@"\A[0-9a-f]{32}\z")&&(string)p["orientation_id"]==outcome.OrientationId&&(string)p["orientation_receipt_sha256"]==outcome.Sha256,"PREALLOCATION_HANDOFF_BINDING");
                byte[] eligible=FileBytes(p,"eligibility",65536,out _,out string ep),reveal=FileBytes(p,"reveal",65536,out _,out string rp),mapping=FileBytes(p,"package_hashes",1024*1024,out _,out string mp);
                var binding=gate.Consume(eligible,ep,reveal,rp,mapping,mp);
                // Only now may the operator's joined configuration be opened.
                FileBytes(p,"joined_config",65536,out string configPath,out string configPin);
                CreateNew(Path.Combine(receipts,outcome.OrientationId+".handoff.json"),ReceiptCanonical.Bytes(new JObject{["version"]=1,["request_id"]=p["request_id"].DeepClone(),["orientation_receipt_sha256"]=outcome.Sha256,["allocation_receipt_sha256"]=binding.RevealReceiptSha256,["joined_config_sha256"]=configPin,["participant_admission"]=false}));
                orientation.ReleaseForHandoff();orientation.OutcomeRecorded-=Recorded;handedOff=true;joined.StartAllocated(binding,configPath,configPin);Report("PREALLOCATION_HANDED_TO_ENGINEERING_JOIN");
            }
            catch(SessionFault e){Fail(e.Code);}catch{Fail("PREALLOCATION_HANDOFF_FAILED");}
        }
        void Report(string code){if(StatusCode==code)return;StatusCode=code;Debug.Log("PREALLOCATION_STATUS "+code+" participant_admission=false");}
        void Fail(string code){if(closed)return;closed=true;Report(code);if(orientation!=null){orientation.OutcomeRecorded-=Recorded;orientation.enabled=false;}}
        void OnDisable(){if(!handedOff)Fail("PREALLOCATION_HOST_DISABLED");}
        void OnDestroy(){if(orientation!=null)orientation.OutcomeRecorded-=Recorded;}
    }
}
