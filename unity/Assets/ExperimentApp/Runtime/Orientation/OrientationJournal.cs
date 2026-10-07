using System;
using System.IO;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.Orientation
{
    public sealed class OrientationJournal : IDisposable
    {
        readonly FileStream stream;readonly StreamWriter writer;readonly JObject header;bool closed,sealedOutcome;
        public string JournalPath{get;}public string OrientationId{get;}=Guid.NewGuid().ToString("N");
        public OrientationJournal(string directory,JObject build,string station,string planHash,string demoIndexHash,string screeningId=null)
        {
            Directory.CreateDirectory(directory);
            JournalPath=Path.Combine(directory,"orientation-"+OrientationId+".jsonl");stream=new FileStream(JournalPath,FileMode.CreateNew,FileAccess.Write,FileShare.Read);
            writer=new StreamWriter(stream,new UTF8Encoding(false)) { AutoFlush=true };
            header=new JObject { ["event"]="orientation_header",["schema"]="silent-orientation-v1",["build_identity"]=build.DeepClone(),["station_id"]=station,["plan_sha256"]=planHash,["demo_index_sha256"]=demoIndexHash,["preallocation"]=true,["study_audio_loaded"]=false };
            if(screeningId!=null){header["screening_id"]=screeningId;header["orientation_id"]=OrientationId;header["protocol_version"]=build["protocol_version"]?.DeepClone();}Record(header);
        }
        public void Record(JObject value) { if(closed||sealedOutcome)throw new OrientationFault("ORIENTATION_JOURNAL_CLOSED");writer.WriteLine(value.ToString(Formatting.None));stream.Flush(true); }
        public OrientationReceipt SealOutcome(string screeningId,OrientationOutcome outcome)
        {
            if(closed||sealedOutcome||outcome==null||!System.Text.RegularExpressions.Regex.IsMatch(screeningId??"",@"\A[A-Za-z0-9._-]{1,32}\z")||(string)header["screening_id"]!=screeningId||header["protocol_version"]?.Type!=JTokenType.String)throw new OrientationFault("ORIENTATION_RECEIPT_BINDING");
            sealedOutcome=true;writer.Flush();stream.Flush(true);Dispose();byte[] bytes=File.ReadAllBytes(JournalPath);
            string[] lines=Encoding.UTF8.GetString(bytes).TrimEnd('\n','\r').Split('\n');var last=JObject.Parse(lines[lines.Length-1]);
            string code=outcome.Code==EligibilityCode.PassFirst?"pass_first":outcome.Code==EligibilityCode.PassSecond?"pass_second":"fail";
            if((string)last["event"]!="eligibility_outcome"||(string)last["outcome"]!=code||(bool?)last["engineering_draft"]!=outcome.EngineeringDraft)throw new OrientationFault("ORIENTATION_RECEIPT_HISTORY");
            var receipt=new OrientationReceipt(new JObject{["schema_version"]=1,["receipt_type"]="orientation-outcome",["screening_id"]=screeningId,["station_id"]=header["station_id"].DeepClone(),["protocol_version"]=header["build_identity"]["protocol_version"]?.DeepClone(),["orientation_id"]=OrientationId,["plan_sha256"]=header["plan_sha256"].DeepClone(),["demo_index_sha256"]=header["demo_index_sha256"].DeepClone(),["journal_sha256"]=AcousticVocab.StateSources.SceneRegistry.Hash(bytes),["journal_bytes"]=bytes.Length,["outcome"]=code,["engineering_draft"]=outcome.EngineeringDraft,["eligible"]=outcome.Passed&&!outcome.EngineeringDraft});
            Dispose();return receipt;
        }
        public void Dispose(){if(closed)return;closed=true;writer.Dispose();}
    }
}
