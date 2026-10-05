using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Text;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
namespace AcousticVocab.SessionIntegration.Tests
{
    public sealed class JoinedArtifactTests
    {
        static T New<T>(params object[] args)=>(T)Activator.CreateInstance(typeof(T),BindingFlags.Instance|BindingFlags.NonPublic,null,args,null);
        static SlotItem Item(string type)=>New<SlotItem>("DEMO-item",type,null,null,null,"engineering",false,20,1,1);
        static VisitSchedule Schedule(params ScheduleBlock[] blocks)=>New<VisitSchedule>(new string('a',64),new string('b',64),"DEMO-person","V2",true,blocks,"B","pilot",null);
        [TestCase("profile_menu",JoinedModuleKind.Menus)][TestCase("atom_menu",JoinedModuleKind.Menus)]
        [TestCase("atomic_lesson",JoinedModuleKind.Teaching)][TestCase("message_lesson",JoinedModuleKind.Teaching)]
        [TestCase("pre_old",JoinedModuleKind.Assessment)][TestCase("trained",JoinedModuleKind.Assessment)][TestCase("atomic",JoinedModuleKind.Assessment)]
        [TestCase("novel",JoinedModuleKind.Assessment)][TestCase("speech",JoinedModuleKind.Assessment)][TestCase("no_cue",JoinedModuleKind.Assessment)]
        public void EveryProducerSlotHasAnExplicitModule(string type,JoinedModuleKind expected)
        {
            var map=JoinedBlockMap.Build(Schedule(New<ScheduleBlock>("block",new[]{Item(type)})));Assert.That(map["block"],Is.EqualTo(expected));
            Assert.Throws<NotSupportedException>(()=>((IDictionary<string,JoinedModuleKind>)map).Add("injected",JoinedModuleKind.Menus));
        }
        [Test]public void MixedEmptyAndUnknownBlocksCannotCreateAmbiguousOwners()
        {
            foreach(var items in new[]{new[]{Item("profile_menu"),Item("atomic_lesson")},Array.Empty<SlotItem>(),new[]{Item("invented")}})
                Assert.Throws<SessionFault>(()=>JoinedBlockMap.Build(Schedule(New<ScheduleBlock>("block",items))));
        }
        [Test]public void GainRestorationConsumesExactlyPinnedBytesAndCodedIdentity()
        {
            var row=new JObject{["event"]="comfort_gain_changed",["coded_id"]="DEMO-person",["visit_id"]=new string('a',32),["old_gain"]=.1,["new_gain"]=.2,["mono_s"]=1.0};
            byte[] bytes=Encoding.UTF8.GetBytes(row.ToString(Newtonsoft.Json.Formatting.None)+"\n");string pin=PcmWave.Hash(bytes);
            Assert.That(ComfortableGainStore.RestoreVerified(bytes,pin,"DEMO-person"),Is.EqualTo(.2f));
            Assert.Throws<AudioFault>(()=>ComfortableGainStore.RestoreVerified(bytes,pin,"DEMO-other"));
            byte[] replacement=(byte[])bytes.Clone();replacement[4]^=1;
            Assert.Throws<AudioFault>(()=>ComfortableGainStore.RestoreVerified(replacement,pin,"DEMO-person"));
            byte[] empty=Encoding.UTF8.GetBytes("\n");Assert.Throws<AudioFault>(()=>ComfortableGainStore.RestoreVerified(empty,PcmWave.Hash(empty),"DEMO-person"));
            Assert.Throws<AudioFault>(()=>ComfortableGainStore.RestoreVerified(Array.Empty<byte>(),PcmWave.Hash(Array.Empty<byte>()),"DEMO-person"));
        }
    }
}
