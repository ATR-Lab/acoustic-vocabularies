using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.DataLogging
{
    public sealed class StoredObjectVersion
    {
        public string ObjectId {get;} public string Version {get;}
        public StoredObjectVersion(string objectId,string version){DataJson.Require(DataJson.Id(objectId)&&DataJson.Id(version),"DATA_STORE_REFERENCE");ObjectId=objectId;Version=version;}
    }
    // No production transport is supplied. Implementations require an approved
    // encrypted store, credentials outside source, bounded I/O and create-new
    // semantics. OpenRead must bind the exact returned immutable object version.
    public interface IApprovedStoreTransport
    {
        string ApprovalEvidenceSha256 {get;}
        Task<StoredObjectVersion> PutNewAsync(string logicalName,Stream bytes,CancellationToken cancellation);
        Task<Stream> OpenReadAsync(StoredObjectVersion reference,CancellationToken cancellation);
    }
    public sealed class UploadVerification
    {
        public bool Verified {get;} public int VerifiedFiles {get;} public string ManifestSha256 {get;}
        internal UploadVerification(int count,string hash){Verified=true;VerifiedFiles=count;ManifestSha256=hash;}
    }
    public static class VerifiedUpload
    {
        public static async Task<UploadVerification> UploadAsync(ExportBundle bundle,IApprovedStoreTransport transport,string freshReceiptFile,CancellationToken cancellation)
        {
            if(transport==null)throw new DataFault("DATA_STORE_UNCONFIGURED");
            DataJson.Require(bundle!=null&&bundle.HeadersQualified,"DATA_EXPORT_UNQUALIFIED");DataJson.Require(DataJson.Hash(transport.ApprovalEvidenceSha256),"DATA_STORE_APPROVAL_REQUIRED");
            DataJson.NoLinks(freshReceiptFile);DataJson.Require(!File.Exists(freshReceiptFile)&&Directory.Exists(Path.GetDirectoryName(Path.GetFullPath(freshReceiptFile))),"DATA_RECEIPT_PATH");
            bundle.VerifyAll();var verified=new JArray();
            foreach(var file in bundle.Files)
            {
                cancellation.ThrowIfCancellationRequested();byte[] local=bundle.VerifiedBytes(file);
                StoredObjectVersion reference;
                using(var input=new MemoryStream(local,false))reference=await transport.PutNewAsync(file.RelativePath,input,cancellation).ConfigureAwait(false);
                DataJson.Require(reference!=null,"DATA_STORE_REFERENCE");
                using(Stream stored=await transport.OpenReadAsync(reference,cancellation).ConfigureAwait(false))
                using(var hash=SHA256.Create())
                {
                    DataJson.Require(stored!=null&&stored.CanRead,"DATA_STORED_STREAM");var buffer=new byte[8192];long count=0;int read;
                    while((read=await stored.ReadAsync(buffer,0,buffer.Length,cancellation).ConfigureAwait(false))>0)
                    {count+=read;DataJson.Require(count<=file.Bytes,"DATA_UPLOAD_HASH_MISMATCH");hash.TransformBlock(buffer,0,read,null,0);}
                    hash.TransformFinalBlock(Array.Empty<byte>(),0,0);string actual=BitConverter.ToString(hash.Hash).Replace("-","").ToLowerInvariant();
                    DataJson.Require(count==file.Bytes&&actual==file.Sha256,"DATA_UPLOAD_HASH_MISMATCH");
                    verified.Add(new JObject{["path"]=file.RelativePath,["object_id"]=reference.ObjectId,["version"]=reference.Version,["bytes"]=count,["sha256"]=actual});
                }
            }
            bundle.VerifyAll();cancellation.ThrowIfCancellationRequested();
            var receipt=new JObject{["schema_version"]="data-sync-receipt-provisional-1",["manifest_sha256"]=bundle.ManifestSha256,["store_approval_evidence_sha256"]=transport.ApprovalEvidenceSha256,["verification"]="separate_exact_version_readback_sha256",["verified_files"]=verified};
            ExportBundle.CreateFile(freshReceiptFile,DataJson.Bytes(receipt));return new UploadVerification(verified.Count,bundle.ManifestSha256);
        }
    }
    public sealed class DataJournalHealth
    {
        public bool Writable {get;} public bool Failed {get;} public bool Closed {get;}
        public long DurableRecordCount {get;} public long? LastDurableSequence {get;} public string FaultCode {get;}
        internal DataJournalHealth(DataJournal writer){Writable=!writer.Closed&&!writer.Failed;Failed=writer.Failed;Closed=writer.Closed;DurableRecordCount=writer.Records.Count;LastDurableSequence=DurableRecordCount==0?(long?)null:writer.Records[writer.Records.Count-1].Sequence;FaultCode=Failed?"DATA_JOURNAL_UNAVAILABLE":null;}
    }
}
