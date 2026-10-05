using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using NUnit.Framework;

namespace AcousticVocab.DataLogging.Tests
{
    public sealed class DataExportTests
    {
        static ExportHeaders ReviewedSyntheticHeaders(bool reverse=false)
        {
            var t=(reverse?DataDeriver.TrialHeaders.Reverse():DataDeriver.TrialHeaders).ToArray();
            byte[] a=Encoding.UTF8.GetBytes(string.Join(",",t)+"\n"),b=Encoding.UTF8.GetBytes(string.Join(",",DataDeriver.ExposureHeaders)+"\n");
            return ExportHeaders.FromReviewedTemplates(a,b,DataJson.HashBytes(a),DataJson.HashBytes(b),new string('f',64));
        }
        static string Raw()
        {string raw=SyntheticData.Folder("export-raw");using(var writer=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>1))SyntheticData.Trial(writer,"synthetic-export");return raw;}
        [Test] public void ProvisionalHeadersExportButCannotQualifyOrUpload()
        {
            string output=SyntheticData.Folder("provisional-export");var bundle=ExportBundle.Create(Raw(),output,SyntheticData.Identity,ExportHeaders.Provisional());Assert.That(bundle.HeadersQualified,Is.False);bundle.VerifyAll();Assert.That(File.Exists(Path.Combine(output,"manifest.json")),Is.True);
            Assert.ThrowsAsync<DataFault>(async()=>await VerifiedUpload.UploadAsync(bundle,null,Path.Combine(output,"unused-receipt.json"),CancellationToken.None));
        }
        [Test] public void ExactReviewedHeaderOrderIsUsedAndMissingOrNewFieldsFail()
        {
            var headers=ReviewedSyntheticHeaders(true);string output=SyntheticData.Folder("reordered-export");var bundle=ExportBundle.Create(Raw(),output,SyntheticData.Identity,headers);Assert.That(bundle.HeadersQualified,Is.True);
            Assert.That(File.ReadLines(Path.Combine(output,"trial-log.csv")).First(),Is.EqualTo(string.Join(",",DataDeriver.TrialHeaders.Reverse())));
            byte[] bad=Encoding.UTF8.GetBytes("participant_name\n");Assert.Throws<DataFault>(()=>ExportHeaders.FromReviewedTemplates(bad,bad,DataJson.HashBytes(bad),DataJson.HashBytes(bad),new string('f',64)));
            Assert.Throws<DataFault>(()=>ExportHeaders.FromReviewedTemplates(bad,bad,new string('0',64),new string('0',64),new string('f',64)));
        }
        [Test] public void ExportIsCreateNewAndIncludesRawDerivedAndManifestHashes()
        {
            string raw=Raw(),output=SyntheticData.Folder("immutable-export");var bundle=ExportBundle.Create(raw,output,SyntheticData.Identity,ReviewedSyntheticHeaders());
            Assert.That(bundle.Files.Any(x=>x.RelativePath.StartsWith("raw/",StringComparison.Ordinal)),Is.True);foreach(string file in new[]{"trial-log.csv","exposure-ledger.csv","header-contract.json","manifest.json"})Assert.That(bundle.Files.Any(x=>x.RelativePath==file),Is.True);
            Assert.Throws<DataFault>(()=>ExportBundle.Create(raw,output,SyntheticData.Identity,ReviewedSyntheticHeaders()));
            Assert.That(ExportBundle.Load(output,bundle.ManifestSha256).Files.Count,Is.EqualTo(bundle.Files.Count));
        }
        [Test] public void AnyDerivedByteChangeOrManifestChangeIsDetected()
        {
            string output=SyntheticData.Folder("tampered-export");var bundle=ExportBundle.Create(Raw(),output,SyntheticData.Identity,ReviewedSyntheticHeaders());File.AppendAllText(Path.Combine(output,"trial-log.csv"),"x");Assert.Throws<DataFault>(()=>bundle.VerifyAll());
            File.AppendAllText(Path.Combine(output,"manifest.json"),"x");Assert.Throws<DataFault>(()=>ExportBundle.Load(output,bundle.ManifestSha256));
        }
        [Test] public void RawByteTamperCannotBeReexportedAsValid()
        {string raw=Raw();string file=Directory.GetFiles(raw).Single();byte[] data=File.ReadAllBytes(file);data[10]^=1;File.WriteAllBytes(file,data);Assert.Throws<DataFault>(()=>ExportBundle.Create(raw,SyntheticData.Folder("rejected-export"),SyntheticData.Identity,ReviewedSyntheticHeaders()));}
        [Test] public void PreservedUnacknowledgedTailPreventsQualification()
        {string raw=Raw();File.AppendAllText(Directory.GetFiles(raw).Single(),"torn");var bundle=ExportBundle.Create(raw,SyntheticData.Folder("torn-export"),SyntheticData.Identity,ReviewedSyntheticHeaders());Assert.That(bundle.HeadersQualified,Is.False);Assert.That(bundle.Files.Any(x=>x.RelativePath.StartsWith("raw/",StringComparison.Ordinal)),Is.True);}
        sealed class MemoryStore : IApprovedStoreTransport
        {
            internal readonly Dictionary<string,byte[]> objects=new Dictionary<string,byte[]>();internal bool Corrupt;internal int Reads;
            public string ApprovalEvidenceSha256=>new string('f',64); // Explicitly synthetic fixture, never a real approval.
            public async Task<StoredObjectVersion> PutNewAsync(string logicalName,Stream bytes,CancellationToken token)
            {using var copy=new MemoryStream();await bytes.CopyToAsync(copy,8192,token);string id=Guid.NewGuid().ToString("N");objects.Add(id,copy.ToArray());return new StoredObjectVersion(id,"synthetic-version-1");}
            public Task<Stream> OpenReadAsync(StoredObjectVersion reference,CancellationToken token)
            {token.ThrowIfCancellationRequested();Reads++;byte[] b=(byte[])objects[reference.ObjectId].Clone();if(Corrupt&&b.Length>0)b[0]^=1;return Task.FromResult<Stream>(new MemoryStream(b,false));}
        }
        [Test] public async Task SeparateStoredBytesAreReadBeforeAnySyncReceiptExists()
        {
            var bundle=ExportBundle.Create(Raw(),SyntheticData.Folder("upload-export"),SyntheticData.Identity,ReviewedSyntheticHeaders());var store=new MemoryStore();string directory=SyntheticData.Folder("receipts");Directory.CreateDirectory(directory);string receipt=Path.Combine(directory,"verified.json");
            var result=await VerifiedUpload.UploadAsync(bundle,store,receipt,CancellationToken.None);Assert.That(result.Verified,Is.True);Assert.That(store.Reads,Is.EqualTo(bundle.Files.Count));Assert.That(result.VerifiedFiles,Is.EqualTo(bundle.Files.Count));Assert.That(File.ReadAllText(receipt),Does.Contain("separate_exact_version_readback_sha256"));
        }
        [Test] public void SuccessfulPutWithWrongStoredBytesNeverMarksSynced()
        {
            var bundle=ExportBundle.Create(Raw(),SyntheticData.Folder("bad-upload-export"),SyntheticData.Identity,ReviewedSyntheticHeaders());var store=new MemoryStore{Corrupt=true};string directory=SyntheticData.Folder("bad-receipt");Directory.CreateDirectory(directory);string receipt=Path.Combine(directory,"never.json");
            var fault=Assert.ThrowsAsync<DataFault>(async()=>await VerifiedUpload.UploadAsync(bundle,store,receipt,CancellationToken.None));Assert.That(fault.Code,Is.EqualTo("DATA_UPLOAD_HASH_MISMATCH"));Assert.That(File.Exists(receipt),Is.False);Assert.That(store.objects.Count,Is.GreaterThan(0),"Failed remote artifacts are retained, not deleted");
        }
        [Test] public void SyncReceiptCannotMutateAnExportDirectory()
        {
            string folder=SyntheticData.Folder("receipt-export");var bundle=ExportBundle.Create(Raw(),folder,SyntheticData.Identity,ReviewedSyntheticHeaders());var store=new MemoryStore();
            var fault=Assert.ThrowsAsync<DataFault>(async()=>await VerifiedUpload.UploadAsync(bundle,store,Path.Combine(folder,"receipt.json"),CancellationToken.None));Assert.That(fault.Code,Is.EqualTo("DATA_RECEIPT_INSIDE_EXPORT"));Assert.That(store.objects,Is.Empty);
        }
        [Test] public void CancellationLeavesNoFalseReceipt()
        {
            var bundle=ExportBundle.Create(Raw(),SyntheticData.Folder("cancel-export"),SyntheticData.Identity,ReviewedSyntheticHeaders());var store=new MemoryStore();string directory=SyntheticData.Folder("cancel-receipt");Directory.CreateDirectory(directory);string receipt=Path.Combine(directory,"never.json");var token=new CancellationToken(true);
            Assert.CatchAsync<OperationCanceledException>(async()=>await VerifiedUpload.UploadAsync(bundle,store,receipt,token));Assert.That(File.Exists(receipt),Is.False);Assert.That(store.objects,Is.Empty);
        }
    }
}
