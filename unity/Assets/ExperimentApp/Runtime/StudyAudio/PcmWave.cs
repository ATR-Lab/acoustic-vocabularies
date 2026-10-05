using System;
using System.Security.Cryptography;
using System.Text;

namespace AcousticVocab.StudyAudio
{
    public class AudioFault : Exception
    {
        public string Code { get; }
        public AudioFault(string code) : base(Bounded(code)) { Code=Message; }
        static string Bounded(string code) => code!=null && System.Text.RegularExpressions.Regex.IsMatch(code,@"\A[A-Z][A-Z0-9_]{0,63}\z")?code:"AUDIO_FAULT";
    }
    public sealed class AudioIntegrityException : AudioFault
    { public AudioIntegrityException() : base("HASH_MISMATCH") { } }

    // Owns its bytes; callers receive copies. No answers, filesystem paths or
    // semantic labels are part of this audio-only value.
    public sealed class PcmWave
    {
        public const int SampleRate = 48000;
        readonly byte[] pcm;
        public int SampleCount => pcm.Length / 2;
        public string PcmSha256 { get; }
        public string FileSha256 { get; }
        internal PcmWave(byte[] samples, string fileHash = null)
        {
            if(samples==null || samples.Length==0 || samples.Length%2!=0) throw new AudioIntegrityException();
            pcm=(byte[])samples.Clone(); PcmSha256=Hash(pcm); FileSha256=fileHash;
        }
        public void Verify() { if(Hash(pcm)!=PcmSha256) throw new AudioIntegrityException(); }
        public byte[] CopyPcm16() { Verify(); return (byte[])pcm.Clone(); }
        public float[] CopySamples()
        {
            Verify(); var result=new float[SampleCount];
            for(int i=0;i<result.Length;i++) result[i]=(short)(pcm[2*i] | pcm[2*i+1]<<8)/32768f;
            return result;
        }
        internal void HashInto(HashAlgorithm hash) { Verify(); hash.TransformBlock(pcm,0,pcm.Length,null,0); }
        internal void CopyInto(byte[] destination,int offset) { Verify(); Buffer.BlockCopy(pcm,0,destination,offset,pcm.Length); }
        public static string Hash(byte[] bytes)
        { using var hash=SHA256.Create(); return BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-","").ToLowerInvariant(); }
        static uint U32(byte[] b,int i) => (uint)(b[i] | b[i+1]<<8 | b[i+2]<<16 | b[i+3]<<24);
        static int U16(byte[] b,int i) => b[i] | b[i+1]<<8;
        static bool Tag(byte[] bytes,int at,string text) => Encoding.ASCII.GetString(bytes,at,4)==text;
        public static PcmWave ParseCanonical(byte[] bytes)
        {
            if(bytes==null || bytes.Length<46 || bytes.Length%2!=0 || !Tag(bytes,0,"RIFF") ||
                U32(bytes,4)!=bytes.Length-8 || !Tag(bytes,8,"WAVE") || !Tag(bytes,12,"fmt ") ||
                U32(bytes,16)!=16 || U16(bytes,20)!=1 || U16(bytes,22)!=1 || U32(bytes,24)!=SampleRate ||
                U32(bytes,28)!=SampleRate*2 || U16(bytes,32)!=2 || U16(bytes,34)!=16 ||
                !Tag(bytes,36,"data") || U32(bytes,40)!=bytes.Length-44) throw new AudioIntegrityException();
            var pcm=new byte[bytes.Length-44]; Buffer.BlockCopy(bytes,44,pcm,0,pcm.Length);
            return new PcmWave(pcm,Hash(bytes));
        }
    }
}
