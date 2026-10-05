using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.DataLogging
{
    public sealed class ExportHeaders
    {
        public IReadOnlyList<string> Trials {get;} public IReadOnlyList<string> Exposures {get;}
        public bool Qualified {get;} public string ReviewEvidenceSha256 {get;}
        public string TrialTemplateSha256 {get;} public string ExposureTemplateSha256 {get;}
        ExportHeaders(string[] trials,string[] exposures,bool qualified,string review,string trialHash,string exposureHash)
        {Trials=Array.AsReadOnly(trials);Exposures=Array.AsReadOnly(exposures);Qualified=qualified;ReviewEvidenceSha256=review;TrialTemplateSha256=trialHash;ExposureTemplateSha256=exposureHash;}
        public static ExportHeaders Provisional()=>new ExportHeaders(DataDeriver.TrialHeaders.ToArray(),DataDeriver.ExposureHeaders.ToArray(),false,null,null,null);
        public static ExportHeaders FromReviewedTemplates(byte[] trials,byte[] exposures,string expectedTrialHash,string expectedExposureHash,string reviewEvidenceHash)
        {
            DataJson.Require(DataJson.Hash(expectedTrialHash)&&DataJson.Hash(expectedExposureHash)&&DataJson.Hash(reviewEvidenceHash),"DATA_TEMPLATE_REVIEW_REQUIRED");
            DataJson.Require(trials!=null&&exposures!=null&&trials.Length<1048576&&exposures.Length<1048576&&DataJson.HashBytes(trials)==expectedTrialHash&&DataJson.HashBytes(exposures)==expectedExposureHash,"DATA_TEMPLATE_HASH");
            string[] Parse(byte[] bytes,IReadOnlyList<string> supported)
            {
                string text=new UTF8Encoding(false,true).GetString(bytes).TrimStart('\ufeff');string line=text.Split('\n')[0].TrimEnd('\r');
                // Header vocabulary is deliberately closed; metadata preambles,
                // new fields and quoted/multiline headers require a versioned adapter.
                string[] fields=line.Split(',');DataJson.Require(fields.Length==supported.Count&&fields.Distinct(StringComparer.Ordinal).Count()==fields.Length&&fields.All(supported.Contains),"DATA_TEMPLATE_HEADERS");return fields;
            }
            return new ExportHeaders(Parse(trials,DataDeriver.TrialHeaders),Parse(exposures,DataDeriver.ExposureHeaders),true,reviewEvidenceHash,expectedTrialHash,expectedExposureHash);
        }
        public JObject ToJson()=>new JObject{["schema_version"]="data-header-contract-provisional-1",["qualified"]=Qualified,["review_evidence_sha256"]=ReviewEvidenceSha256,["trial_template_sha256"]=TrialTemplateSha256,["exposure_template_sha256"]=ExposureTemplateSha256,["trial_headers"]=new JArray(Trials),["exposure_headers"]=new JArray(Exposures)};
    }
    public sealed class ExportFile
    {
        public string RelativePath {get;} public long Bytes {get;} public string Sha256 {get;}
        internal ExportFile(string path,long size,string hash){RelativePath=path;Bytes=size;Sha256=hash;}
        internal JObject ToJson()=>new JObject{["path"]=RelativePath,["bytes"]=Bytes,["sha256"]=Sha256};
    }
    public sealed class ExportBundle
    {
        readonly string directory;readonly IReadOnlyList<ExportFile> files;
        public string ManifestSha256 {get;} public bool HeadersQualified {get;} public IReadOnlyList<ExportFile> Files=>files;
        internal ExportBundle(string directory,IEnumerable<ExportFile> files,string manifestHash,bool qualified){this.directory=directory;this.files=files.ToList().AsReadOnly();ManifestSha256=manifestHash;HeadersQualified=qualified;}
        public byte[] VerifiedBytes(ExportFile entry)
        {
            DataJson.Require(files.Contains(entry),"DATA_EXPORT_FILE");string path=Path.Combine(directory,entry.RelativePath.Replace('/',Path.DirectorySeparatorChar));DataJson.NoLinks(path);
            DataJson.Require(new FileInfo(path).Length==entry.Bytes&&entry.Bytes<=DataJournal.MaximumSegmentBytes,"DATA_EXPORT_CHANGED");
            byte[] bytes=File.ReadAllBytes(path);DataJson.Require(bytes.LongLength==entry.Bytes&&DataJson.HashBytes(bytes)==entry.Sha256,"DATA_EXPORT_CHANGED");return bytes;
        }
        public void VerifyAll(){foreach(var entry in files)VerifiedBytes(entry);}
        internal static void CreateFile(string path,byte[] bytes)
        {DataJson.NoLinks(path);using var f=new FileStream(path,FileMode.CreateNew,FileAccess.Write,FileShare.Read,4096,FileOptions.WriteThrough);f.Write(bytes,0,bytes.Length);f.Flush(true);}
        public static ExportBundle Create(string rawDirectory,string freshOutputDirectory,DataIdentity identity,ExportHeaders headers)
        {
            DataJson.Require(headers!=null,"DATA_HEADERS_REQUIRED");DataJson.NoLinks(freshOutputDirectory);DataJson.Require(!Directory.Exists(freshOutputDirectory)&&!File.Exists(freshOutputDirectory),"DATA_EXPORT_EXISTS");
            var snapshot=DataJournal.Verify(rawDirectory,identity);DataJson.Require(snapshot.SegmentFiles.Count>0,"DATA_RAW_REQUIRED");var tables=DataDeriver.Derive(snapshot,identity);
            // Retain incomplete output if any write fails; never reuse its path.
            Directory.CreateDirectory(freshOutputDirectory);Directory.CreateDirectory(Path.Combine(freshOutputDirectory,"raw"));var entries=new List<ExportFile>();
            void Save(string relative,byte[] bytes){CreateFile(Path.Combine(freshOutputDirectory,relative.Replace('/',Path.DirectorySeparatorChar)),bytes);entries.Add(new ExportFile(relative,bytes.LongLength,DataJson.HashBytes(bytes)));}
            foreach(string path in snapshot.SegmentFiles){DataJson.NoLinks(path);byte[] bytes=File.ReadAllBytes(path);DataJson.Require(bytes.LongLength<=DataJournal.MaximumSegmentBytes,"DATA_SEGMENT_LIMIT");Save("raw/"+Path.GetFileName(path),bytes);}
            // Verify copied raw bytes, not merely the earlier in-memory read.
            var copy=DataJournal.Verify(Path.Combine(freshOutputDirectory,"raw"),identity);
            DataJson.Require(copy.Records.Count==snapshot.Records.Count&&copy.Previous==snapshot.Previous&&JToken.DeepEquals(copy.PendingTails,snapshot.PendingTails),"DATA_RAW_CHANGED_DURING_EXPORT");
            Save("trial-log.csv",Csv(headers.Trials,tables.Trials));Save("exposure-ledger.csv",Csv(headers.Exposures,tables.Exposures));Save("header-contract.json",DataJson.Bytes(headers.ToJson()));
            bool qualified=headers.Qualified&&!snapshot.HasUnacknowledgedTail;
            var manifest=new JObject{["schema_version"]="data-export-provisional-1",["export_id"]=Guid.NewGuid().ToString("N"),["identity"]=identity.ToJson(),["headers_qualified"]=qualified,["unacknowledged_torn_tail"]=snapshot.HasUnacknowledgedTail,["record_count"]=snapshot.Records.Count,["last_record_sha256"]=snapshot.Previous,["trial_rows"]=tables.Trials.Count,["exposure_rows"]=tables.Exposures.Count,["files"]=new JArray(entries.Select(x=>x.ToJson()))};
            byte[] manifestBytes=DataJson.Bytes(manifest);string hash=DataJson.HashBytes(manifestBytes);Save("manifest.json",manifestBytes);
            var bundle=new ExportBundle(freshOutputDirectory,entries,hash,qualified);bundle.VerifyAll();return bundle;
        }
        public static ExportBundle Load(string directory,string expectedManifestSha256)
        {
            DataJson.Require(DataJson.Hash(expectedManifestSha256),"DATA_MANIFEST_HASH_REQUIRED");string path=Path.Combine(directory,"manifest.json");DataJson.NoLinks(path);DataJson.Require(new FileInfo(path).Length<=DataJson.MaxLine,"DATA_MANIFEST_LIMIT");byte[] bytes=File.ReadAllBytes(path);DataJson.Require(DataJson.HashBytes(bytes)==expectedManifestSha256,"DATA_MANIFEST_HASH");
            var m=DataJson.ParseCanonical(bytes);DataJson.Keys(m,"schema_version","export_id","identity","headers_qualified","unacknowledged_torn_tail","record_count","last_record_sha256","trial_rows","exposure_rows","files");
            DataJson.Require((string)m["schema_version"]=="data-export-provisional-1"&&DataJson.Guid((string)m["export_id"])&&DataJson.Hash((string)m["last_record_sha256"]));DataIdentity.Parse(m["identity"] as JObject);
            bool qualified=DataJson.Boolean(m["headers_qualified"]),tail=DataJson.Boolean(m["unacknowledged_torn_tail"]);DataJson.Require(!qualified||!tail);foreach(string k in new[]{"record_count","trial_rows","exposure_rows"})DataJson.Integer(m[k]);
            DataJson.Require(m["files"] is JArray list&&list.Count>=4&&list.Count<=1003,"DATA_MANIFEST_FILES");var files=new List<ExportFile>();var seen=new HashSet<string>(StringComparer.Ordinal);
            foreach(JToken v in (JArray)m["files"]){DataJson.Keys(v,"path","bytes","sha256");string name=DataJson.Text(v["path"]);long size=DataJson.Integer(v["bytes"]);string hash=DataJson.Text(v["sha256"]);DataJson.Require((new[]{"trial-log.csv","exposure-ledger.csv","header-contract.json"}.Contains(name)||Regex.IsMatch(name,@"\Araw/events-[0-9]{4}\.local\.jsonl\z"))&&seen.Add(name)&&size<=DataJournal.MaximumSegmentBytes&&DataJson.Hash(hash),"DATA_MANIFEST_FILES");files.Add(new ExportFile(name,size,hash));}
            DataJson.Require(new[]{"trial-log.csv","exposure-ledger.csv","header-contract.json"}.All(seen.Contains));files.Add(new ExportFile("manifest.json",bytes.LongLength,expectedManifestSha256));
            var bundle=new ExportBundle(directory,files,expectedManifestSha256,qualified);bundle.VerifyAll();return bundle;
        }
        static byte[] Csv(IReadOnlyList<string> headers,IReadOnlyList<IReadOnlyDictionary<string,string>> rows)
        {
            string Escape(string value){value=value??"";return value.IndexOfAny(new[]{',','"','\r','\n'})>=0?"\""+value.Replace("\"","\"\"")+"\"":value;}
            var s=new StringBuilder(string.Join(",",headers)+"\n");foreach(var row in rows){DataJson.Require(headers.All(row.ContainsKey),"DATA_EXPORT_COLUMN");s.Append(string.Join(",",headers.Select(h=>Escape(row[h])))).Append('\n');}return new UTF8Encoding(false,true).GetBytes(s.ToString());
        }
    }
}
