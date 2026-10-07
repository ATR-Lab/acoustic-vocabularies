using System;
using System.Linq;
using System.Reflection;
using System.Text;
using AcousticVocab.Orientation;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
namespace AcousticVocab.SessionIntegration.Tests
{
    // Explicit synthetic authority fixtures test chain mechanics, not a human's
    // consent/eligibility or a qualified motion/audio/material provision.
    public sealed class PreallocationHandoffTests
    {
        const string Person="SYNTHETIC-01";static string H(char x)=>new string(x,64);
        static byte[] Bytes(JObject x)=>ReceiptCanonical.Bytes(x);
        static OrientationReceipt Orientation(bool eligible=true)=>
            (OrientationReceipt)Activator.CreateInstance(typeof(OrientationReceipt),BindingFlags.Instance|BindingFlags.NonPublic,null,new object[]{new JObject{["screening_id"]=Person,["orientation_id"]=new string('a',32),["eligible"]=eligible}},null);
        static JObject EntryA()=>new JObject{["slot_id"]="A-C01-L01",["unit_id"]="A-C01",["slot"]="L01",["order"]=1,["wave"]=1,["wave_position"]=1,["profile"]="P1",["book_id"]="BK-C-BCFGHJ",["participant_id"]=Person};
        static JObject EntryB()=>new JObject{["unit_id"]="B-C01",["kind"]="dyad",["order"]=1,["block_id"]="B-C-blk01",["block_position"]=1,["sq_arm"]="SQ-1",["structured_family"]="K",["swap_w1_w4"]=false,["members"]=new JArray(new JObject{["slot_id"]="B-C01-M1",["member"]=1,["role"]="active",["participant_id"]="SYNTHETIC-02"},new JObject{["slot_id"]="B-C01-M2",["member"]=2,["role"]="yoked",["participant_id"]=Person}),["bank_id"]="bank-C001",["profile_menu_order"]=new JArray("P2","P1","P3"),["replaces"]=null};
        static (JObject e,JObject r) Receipts(OrientationReceipt orientation,bool b=false)
        {
            var people=b?new JArray("SYNTHETIC-02",Person):new JArray(Person);var proofs=b?new JArray(H('f'),orientation.Sha256):new JArray(orientation.Sha256);
            var e=ReceiptCanonical.Seal(new JObject{["schema_version"]=1,["receipt_type"]="allocation-eligibility",["study"]=b?"B":"A",["set"]="confirmatory",["list_sha256"]=H('b'),["eligibility_id"]="E0001",["screening_ids"]=people,["orientation_receipt_sha256"]=proofs,["journal_head_sha256"]=H('c'),["journal_line"]=1});
            var entry=b?EntryB():EntryA();var r=ReceiptCanonical.Seal(new JObject{["schema_version"]=1,["receipt_type"]="allocation-reveal",["study"]=b?"B":"A",["set"]="confirmatory",["list_sha256"]=H('b'),["eligibility_id"]="E0001",["eligibility_receipt_sha256"]=e["receipt_sha256"].DeepClone(),["screening_ids"]=people.DeepClone(),["entry"]=entry,["entry_sha256"]=ReceiptCanonical.Hash(entry),["journal_head_sha256"]=H('d'),["journal_line"]=2});return(e,r);
        }
        static JObject Reseal(JObject x){x.Remove("receipt_sha256");return ReceiptCanonical.Seal(x);}
        static AllocationJoinBinding Consume(PreallocationHandoff gate,JObject e,JObject r,string pin=null)
        {var eb=Bytes(e);var rb=Bytes(r);var mapping=Encoding.UTF8.GetBytes("synthetic mapping bytes - not an admitted package");return gate.Consume(eb,pin??PcmWave.Hash(eb),rb,PcmWave.Hash(rb),mapping,PcmWave.Hash(mapping));}
        [TestCase(false)][TestCase(true)]public void ExactOrderedReceiptChainBindsTheCurrentScreeningMember(bool b)
        {var o=Orientation();var(e,r)=Receipts(o,b);var gate=new PreallocationHandoff(o,H('b'));var binding=Consume(gate,e,r);Assert.That(binding.UnitId,Is.EqualTo(b?"B-C01":"A-C01"));Assert.That(binding.SlotId,Is.EqualTo(b?"B-C01-M2":"A-C01-L01"));Assert.That(binding.PackageKey,Is.EqualTo(b?"B-C01":"BK-C-BCFGHJ"));Assert.That(binding.Role,Is.EqualTo(b?"yoked":null));Assert.Throws<SessionFault>(()=>Consume(gate,e,r));}
        [Test]public void DraftOrFailedOrientationCannotOpenAllocationReceipts()
        {var o=Orientation(false);var(e,r)=Receipts(o);Assert.Throws<SessionFault>(()=>Consume(new PreallocationHandoff(o,H('b')),e,r));}
        [TestCase("raw_pin")][TestCase("self_hash")][TestCase("wrong_list")][TestCase("orientation")][TestCase("member")][TestCase("earlier_head")][TestCase("unknown")]
        public void InvalidChainFailsOnceBeforeAnyJoinedLoader(string failure)
        {
            var o=Orientation();var(e,r)=Receipts(o);string pin=null;
            if(failure=="raw_pin")pin=H('0');
            if(failure=="self_hash")r["entry"]["unit_id"]="A-C02";
            if(failure=="wrong_list"){e["list_sha256"]=H('0');e=Reseal(e);}
            if(failure=="orientation"){e["orientation_receipt_sha256"][0]=H('0');e=Reseal(e);r["eligibility_receipt_sha256"]=e["receipt_sha256"].DeepClone();r=Reseal(r);}
            if(failure=="member"){r["entry"]["participant_id"]="SYNTHETIC-02";r["entry_sha256"]=ReceiptCanonical.Hash(r["entry"]);r=Reseal(r);}
            if(failure=="earlier_head"){r["journal_line"]=1;r=Reseal(r);}
            if(failure=="unknown"){r["approved"]=true;r=Reseal(r);}
            var gate=new PreallocationHandoff(o,H('b'));Assert.Throws<SessionFault>(()=>Consume(gate,e,r,pin));var valid=Receipts(o);Assert.Throws<SessionFault>(()=>Consume(gate,valid.e,valid.r));
        }
        [Test]public void CanonicalBytesHaveIndependentPythonCompatibleOrderAndAsciiEscapes()
        {var x=new JObject{["z"]="é",["a"]=new JObject{["v"]=2,["b"]=true}};Assert.That(Encoding.ASCII.GetString(ReceiptCanonical.Bytes(x)),Is.EqualTo("{\"a\":{\"b\":true,\"v\":2},\"z\":\"\\u00e9\"}"));}
    }
}
