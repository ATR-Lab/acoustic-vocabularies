using System;
using System.Security.Cryptography;

namespace AcousticVocab.StudyAudio
{
    // Supplied by the trusted future session engine. This application boundary
    // consumes one approved slot; it is not an authentication credential.
    public interface INovelSlotAuthorization
    {
        bool TryConsume(string packageSha256,string messageId);
    }

    public static class MessageComposer
    {
        public const int GapSamples=9600;
        static readonly byte[] gap=new byte[GapSamples*2];
        public static string CompositeHash(PcmWave action,PcmWave referent)
        {
            CheckAtom(action); CheckAtom(referent);
            using var hash=SHA256.Create();
            action.HashInto(hash); hash.TransformBlock(gap,0,gap.Length,null,0);
            referent.HashInto(hash); hash.TransformFinalBlock(Array.Empty<byte>(),0,0);
            return BitConverter.ToString(hash.Hash).Replace("-","").ToLowerInvariant();
        }
        internal static void CheckAtom(PcmWave wave)
        {
            if(wave==null || (wave.SampleCount!=21600 && wave.SampleCount!=28800 &&
                wave.SampleCount!=36000 && wave.SampleCount!=43200)) throw new AudioIntegrityException();
        }
        // Internal: public callers compose only through the package's fixed
        // matrix and single-use novel-slot boundary, never a heldout=true flag.
        internal static PcmWave Compose(PcmWave action,PcmWave referent,string expected)
        {
            if(CompositeHash(action,referent)!=expected) throw new AudioIntegrityException();
            var pcm=new byte[(action.SampleCount+GapSamples+referent.SampleCount)*2];
            action.CopyInto(pcm,0); referent.CopyInto(pcm,(action.SampleCount+GapSamples)*2);
            var result=new PcmWave(pcm);
            if(result.PcmSha256!=expected) throw new AudioIntegrityException();
            return result;
        }
    }
}
