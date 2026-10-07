using System.Text;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Teaching.Tests
{
    public sealed class TeachingScaffoldTests
    {
        static JObject Allocation(string arm,string family)=>new JObject{["demo"]=true,["dyads"]=new JArray(new JObject{["sq_arm"]=arm,["structured_family"]=family,["members"]=new JArray(new JObject{["slot_id"]="DEMO-member"})})};
        static string Resolve(JObject value,string slot="DEMO-member",string hash=null){byte[] bytes=Encoding.UTF8.GetBytes(value.ToString());return TeachingCatalog.ResolveAlignedFamily("B",slot,true,bytes,hash??PcmWave.Hash(bytes));}
        [TestCase("SQ-1","K")][TestCase("SQ-2","Q")]public void StoredPerFamilyAssignmentResolvedExactly(string arm,string family)
        {Assert.That(Resolve(Allocation(arm,family)),Is.EqualTo(family));}
        [TestCase("SQ-1","Q")][TestCase("SQ-2","K")][TestCase("SQ-3","K")]public void MismatchedScaffoldCannotChangeVariant(string arm,string family)
        {Assert.Throws<SessionFault>(()=>Resolve(Allocation(arm,family)));}
        [Test]public void UnknownMemberRefused(){Assert.Throws<SessionFault>(()=>Resolve(Allocation("SQ-1","K"),"other"));}
        [Test]public void ChangedAllocationHashRefused(){Assert.Throws<SessionFault>(()=>Resolve(Allocation("SQ-1","K"),hash:new string('a',64)));}
        [Test]public void DuplicateMemberRefused(){var row=Allocation("SQ-1","K");((JArray)row["dyads"]).Add(row["dyads"][0].DeepClone());Assert.Throws<SessionFault>(()=>Resolve(row));}
        [Test]public void StudyAHasNoRandomScaffoldAssignment(){Assert.That(TeachingCatalog.ResolveAlignedFamily("A","DEMO",true,null,null),Is.Null);Assert.Throws<SessionFault>(()=>TeachingCatalog.ResolveAlignedFamily("A","DEMO",true,new byte[]{1},new string('a',64)));}
    }
}
