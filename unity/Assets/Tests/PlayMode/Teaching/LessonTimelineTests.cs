using System.Collections;
using System.Collections.Generic;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace AcousticVocab.Teaching.PlayModeTests
{
    public sealed class LessonTimelineTests
    {
        [UnityTest]public IEnumerator UninstalledTeachingHostCannotExposeOrPlay()
        {
            var hostRoot=new GameObject("Synthetic teaching host");var audioRoot=new GameObject("Synthetic silent audio",typeof(AudioSource),typeof(AudioPlayer));
            var host=hostRoot.AddComponent<TeachingSessionHost>();host.player=audioRoot.GetComponent<AudioPlayer>();
            yield return null;yield return null;
            Assert.That(host.Installed,Is.False);Assert.That(host.player.Playing,Is.False);Assert.That(hostRoot.GetComponentsInChildren<Canvas>(true),Is.Empty);
            Object.Destroy(hostRoot);Object.Destroy(audioRoot);yield return null;
        }
        [UnityTest]public IEnumerator UnityFrameDrivenMockTimelineRetainsTwentyFourSeconds()
        {
            var item=new SlotItem("DEMO-play","message_lesson","K-a1-r1",null,"structured","teaching",false,24,3,1);var context=new SlotContext(item,750,null);
            var events=new List<LessonEvent>();var timeline=new LessonTimeline(context,true,24000,24000,"DEMO-display",new string('a',64),new string('b',64),new string('c',64),events.Add);
            int requests=0;timeline.PlayRequested+=(index,id,at)=>requests++;timeline.Start(0);
            var completed=new bool[3];double[] offsets={750,8750,18750};
            for(double simulated=250;simulated<=24750;simulated+=250)
            {
                timeline.Tick(simulated);
                for(int i=0;i<3;i++){if(simulated==offsets[i])timeline.Onset(context.AudioRequestIds[i],simulated,10,simulated);if(!completed[i]&&simulated>=offsets[i]+1200){timeline.Completed(context.AudioRequestIds[i],simulated);completed[i]=true;}}
                if(simulated==9000)timeline.Response("DEMO-correct",simulated);
                if(simulated<24750)Assert.That(timeline.Ended,Is.False);
                yield return null;
            }
            Assert.That(requests,Is.EqualTo(3));Assert.That(timeline.Ended,Is.True);
            Assert.That(events.Find(x=>x.Kind=="lesson_end").MonoMs,Is.EqualTo(24750));
        }
    }
}
