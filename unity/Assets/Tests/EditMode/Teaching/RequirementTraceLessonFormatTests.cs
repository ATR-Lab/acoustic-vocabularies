using System.Linq;
using System.Text;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Teaching.Tests
{
    // Requirement trace: the Study B lesson format per family comes from the
    // stored allocation of the dyad, never from a run-time choice.
    public sealed class RequirementTraceLessonFormatTests
    {
        static JObject Dyad(string arm,string family,params string[] members)
        {
            var dyad=new JObject{["members"]=new JArray(members.Select(x=>new JObject{["slot_id"]=x}))};
            if(arm!=null)dyad["sq_arm"]=arm;if(family!=null)dyad["structured_family"]=family;return dyad;
        }
        static byte[] Allocation(params JObject[] dyads)=>Encoding.UTF8.GetBytes(new JObject{["demo"]=true,["dyads"]=new JArray(dyads.Cast<object>().ToArray())}.ToString());
        static string Resolve(byte[] allocation,string slot)=>TeachingCatalog.ResolveAlignedFamily("B",slot,true,allocation,PcmWave.Hash(allocation));

        [Test, Category("REQ_SB_07")]
        public void BothMembersOfADyadGetTheAllocatedStructuredFamily()
        {
            var allocation=Allocation(Dyad("SQ-1","K","DEMO-B1-active","DEMO-B1-assigned"),Dyad("SQ-2","Q","DEMO-B2-active","DEMO-B2-assigned"));
            Assert.That(new[]{"DEMO-B1-active","DEMO-B1-assigned"}.Select(x=>Resolve(allocation,x)),Is.EqualTo(new[]{"K","K"}));
            Assert.That(new[]{"DEMO-B2-active","DEMO-B2-assigned"}.Select(x=>Resolve(allocation,x)),Is.EqualTo(new[]{"Q","Q"}));
            Assert.That(Enumerable.Range(0,5).Select(_=>Resolve(allocation,"DEMO-B2-assigned")).Distinct(),Is.EqualTo(new[]{"Q"}),"Resolution is a pure function of the stored allocation");
        }

        [Test, Category("REQ_SB_07")]
        public void MissingOrInconsistentAllocatedFormatIsRefusedRatherThanChosen()
        {
            foreach(var dyad in new[]{Dyad(null,null,"DEMO-member"),Dyad("SQ-1",null,"DEMO-member"),Dyad(null,"K","DEMO-member"),Dyad("SQ-2","K","DEMO-member")})
                Assert.Throws<SessionFault>(()=>Resolve(Allocation(dyad),"DEMO-member"),dyad.ToString());
            var participantAllocation=Encoding.UTF8.GetBytes(new JObject{["demo"]=false,["dyads"]=new JArray(Dyad("SQ-1","K","DEMO-member"))}.ToString());
            Assert.Throws<SessionFault>(()=>TeachingCatalog.ResolveAlignedFamily("B","DEMO-member",true,participantAllocation,PcmWave.Hash(participantAllocation)));
        }
    }
}
