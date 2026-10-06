using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Text;
using AcousticVocab.StudyAudio;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace AcousticVocab.SelectionMenus.PlayModeTests
{
    public sealed class MenuHostLifecycleTests
    {
        static PcmWave Silence()
        {
            const int samples=9600;using var stream=new MemoryStream();using var writer=new BinaryWriter(stream);
            writer.Write(Encoding.ASCII.GetBytes("RIFF"));writer.Write(36+samples*2);writer.Write(Encoding.ASCII.GetBytes("WAVEfmt "));writer.Write(16);writer.Write((short)1);writer.Write((short)1);writer.Write(48000);writer.Write(96000);writer.Write((short)2);writer.Write((short)16);writer.Write(Encoding.ASCII.GetBytes("data"));writer.Write(samples*2);writer.Write(new byte[samples*2]);return PcmWave.ParseCanonical(stream.ToArray());
        }
        [UnityTest]public IEnumerator UninstalledMenuCannotExposeCardsOrStartAudio()
        {
            var root=new GameObject("Synthetic menu host");var audio=new GameObject("Silent diagnostic source",typeof(AudioPlayer));var host=root.AddComponent<MenuSessionHost>();host.player=audio.GetComponent<AudioPlayer>();
            yield return null;yield return null;Assert.That(host.Installed,Is.False);Assert.That(host.InputAvailable,Is.False);Assert.That(host.player.Playing,Is.False);Assert.That(root.GetComponentsInChildren<Canvas>(true),Is.Empty);
            Object.Destroy(root);Object.Destroy(audio);yield return null;
        }
        [UnityTest]public IEnumerator DetachedHostLifecycleCannotAbortLaterAudioOwner()
        {
            var root=new GameObject("Retired menu host");var audio=new GameObject("Later owner's silent source",typeof(AudioPlayer));var listener=new GameObject("Diagnostic listener",typeof(AudioListener));var host=root.AddComponent<MenuSessionHost>();var player=audio.GetComponent<AudioPlayer>();host.player=player;
            yield return null;host.Uninstall();player.Event+=_=>{};player.Configure(AudioRouteCalibration.Unmeasured("ENGINEERING_UNMEASURED"),()=>true);player.Preload(new Dictionary<string,PcmWave>{{"silence",Silence()}},1024*1024);Assert.That(player.Ready,Is.True);
            root.SendMessage("OnApplicationFocus",false);root.SendMessage("OnApplicationPause",true);host.enabled=false;host.Uninstall();Assert.That(player.Ready,Is.True);Object.Destroy(root);yield return null;Assert.That(player.Ready,Is.True);Assert.That(player.Playing,Is.False);
            Object.Destroy(audio);Object.Destroy(listener);yield return null;
        }
    }
}
