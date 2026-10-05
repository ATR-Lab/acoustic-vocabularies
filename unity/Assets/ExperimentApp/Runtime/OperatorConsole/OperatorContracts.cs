using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.OperatorConsole
{
    public sealed class OperatorFault : Exception
    {
        public string Code => Message;
        public OperatorFault(string code, Exception cause = null) : base(Regex.IsMatch(code ?? "", @"\A[a-z][a-z0-9_]{0,63}\z") ? code : "engine_fault", cause) { }
    }

    public sealed class OperatorAdmission
    {
        public bool Verified { get; }
        public bool OldHashesOk { get; }
        public bool LocksOk { get; }
        public bool Allowed => Verified && OldHashesOk && LocksOk;
        public OperatorAdmission(bool verified, bool oldHashesOk, bool locksOk)
        { Verified = verified; OldHashesOk = oldHashesOk; LocksOk = locksOk; }
        internal JObject Json() => new JObject { ["verified"] = Verified, ["old_hashes_ok"] = OldHashesOk, ["locks_ok"] = LocksOk };
    }

    public sealed class OperatorHealth
    {
        public bool Headset { get; }
        public bool Audio { get; }
        public bool Reset { get; }
        public bool Input { get; }
        public double BridgeAgeMs { get; }
        public double FrameMs { get; }
        public double MaxGapMs { get; }
        public bool Ready => Headset && Audio && Reset && Input && BridgeAgeMs <= 250 && FrameMs <= 250 && MaxGapMs <= 250;
        public OperatorHealth(bool headset, bool audio, bool reset, bool input, double bridgeAgeMs, double frameMs, double maxGapMs)
        {
            foreach (double value in new[] { bridgeAgeMs, frameMs, maxGapMs }) Wire.Require(!double.IsNaN(value) && !double.IsInfinity(value) && value >= 0, "health_invalid");
            Headset = headset; Audio = audio; Reset = reset; Input = input;
            BridgeAgeMs = bridgeAgeMs; FrameMs = frameMs; MaxGapMs = maxGapMs;
        }
        internal JObject Json() => new JObject { ["headset"] = Headset, ["audio"] = Audio, ["reset"] = Reset, ["input"] = Input,
            ["bridge_age_ms"] = BridgeAgeMs, ["frame_ms"] = FrameMs, ["max_gap_ms"] = MaxGapMs };
    }

    public sealed class OperatorRequest
    {
        public string SessionNonce { get; }
        public string RequestId { get; }
        public long Sequence { get; }
        public string Command { get; }
        public string ManifestSha256 { get; }
        public string ScheduleSha256 { get; }
        internal OperatorRequest(JObject value)
        {
            Wire.Keys(value, "version", "session_nonce", "request_id", "sequence", "command", "run_sheet_manifest_sha256", "schedule_sha256");
            Wire.Require(Wire.Integer(value["version"]) == 1, "command_invalid");
            SessionNonce = Wire.Text(value["session_nonce"]); RequestId = Wire.Text(value["request_id"]);
            Sequence = Wire.Integer(value["sequence"]); Command = Wire.Text(value["command"]);
            ManifestSha256 = Wire.Text(value["run_sheet_manifest_sha256"]); ScheduleSha256 = Wire.Text(value["schedule_sha256"]);
            Wire.Require(Wire.Guid(SessionNonce) && Wire.Guid(RequestId) && Sequence > 0 && Wire.Hash(ManifestSha256) && Wire.Hash(ScheduleSha256) &&
                new[] { "load", "start", "pause", "resume", "stop" }.Contains(Command), "command_invalid");
        }
        public static OperatorRequest Parse(byte[] bytes) => new OperatorRequest(Wire.Parse(bytes));
        internal JObject Json() => new JObject { ["version"] = 1, ["session_nonce"] = SessionNonce, ["request_id"] = RequestId, ["sequence"] = Sequence,
            ["command"] = Command, ["run_sheet_manifest_sha256"] = ManifestSha256, ["schedule_sha256"] = ScheduleSha256 };
    }

    public sealed class OperatorReceipt
    {
        public string RequestId { get; }
        public long Sequence { get; }
        public bool Accepted { get; }
        public string Code { get; }
        internal OperatorReceipt(OperatorRequest request, bool accepted, string code)
        { RequestId = request.RequestId; Sequence = request.Sequence; Accepted = accepted; Code = new OperatorFault(code).Code; }
        internal JObject Json() => new JObject { ["request_id"] = RequestId, ["sequence"] = Sequence, ["status"] = Accepted ? "accepted" : "rejected", ["code"] = Code };
    }

    internal static class Wire
    {
        internal const int MaximumBytes = 65536;
        internal static void Require(bool valid, string code) { if (!valid) throw new OperatorFault(code); }
        internal static bool Guid(string value) => Regex.IsMatch(value ?? "", @"\A[0-9a-f]{32}\z");
        internal static bool Hash(string value) => Regex.IsMatch(value ?? "", @"\A[0-9a-f]{64}\z");
        internal static string Text(JToken value) { Require(value?.Type == JTokenType.String, "command_invalid"); return (string)value; }
        internal static long Integer(JToken value) { Require(value?.Type == JTokenType.Integer, "command_invalid"); try { return (long)value; } catch { throw new OperatorFault("command_invalid"); } }
        internal static void Keys(JObject value, params string[] keys) => Require(value != null && value.Properties().Select(p => p.Name).OrderBy(x => x, StringComparer.Ordinal).SequenceEqual(keys.OrderBy(x => x, StringComparer.Ordinal)), "command_invalid");
        internal static byte[] Bytes(JObject value) => new UTF8Encoding(false, true).GetBytes(value.ToString(Formatting.None));
        internal static string Sha(byte[] bytes) { using var sha = SHA256.Create(); return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant(); }
        internal static JObject Parse(byte[] bytes)
        {
            try
            {
                Require(bytes != null && bytes.Length > 0 && bytes.Length <= MaximumBytes, "command_invalid");
                string text = new UTF8Encoding(false, true).GetString(bytes);
                using var reader = new JsonTextReader(new StringReader(text)) { DateParseHandling = DateParseHandling.None, MaxDepth = 8 };
                var value = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
                Require(!reader.Read() && text == value.ToString(Formatting.None), "command_invalid");
                return value;
            }
            catch (OperatorFault) { throw; }
            catch { throw new OperatorFault("command_invalid"); }
        }
        internal static void NoLinks(string path)
        {
            FileSystemInfo item = File.Exists(path) ? new FileInfo(path) : new DirectoryInfo(path);
            for (; item != null; item = item is FileInfo f ? f.Directory : ((DirectoryInfo)item).Parent)
                if (item.Exists) Require((item.Attributes & FileAttributes.ReparsePoint) == 0, "mailbox_link");
        }
        #if UNITY_STANDALONE_WIN || UNITY_EDITOR_WIN
        [System.Runtime.InteropServices.DllImport("kernel32.dll", EntryPoint = "MoveFileExW", CharSet = System.Runtime.InteropServices.CharSet.Unicode, SetLastError = true, ExactSpelling = true)]
        [return: System.Runtime.InteropServices.MarshalAs(System.Runtime.InteropServices.UnmanagedType.Bool)]
        static extern bool MoveFileEx(string existing, string replacement, uint flags);
#endif
        internal static void Atomic(string path, byte[] bytes)
        {
            Require(bytes.Length <= MaximumBytes, "state_limit"); NoLinks(path);
            string temporary = path + "." + System.Guid.NewGuid().ToString("N") + ".tmp";
            using (var file = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.Read, 4096, FileOptions.WriteThrough))
            { file.Write(bytes, 0, bytes.Length); file.Flush(true); }
            // Preserve an incomplete temp file on failure for investigation.
            #if UNITY_STANDALONE_WIN || UNITY_EDITOR_WIN
            // Same-directory replacement. Windows may briefly deny replacement
            // while another process reads; retry only that bounded I/O operation,
            // never a command or side effect. Persistent failures remain latched.
            for (int attempt = 0; ; attempt++)
            {
                if (MoveFileEx(temporary, path, 0x1 | 0x8)) break;
                int error = System.Runtime.InteropServices.Marshal.GetLastWin32Error();
                if (attempt == 10 || error != 5 && error != 32 && error != 33)
                    throw new OperatorFault("state_publish_failed", new System.ComponentModel.Win32Exception(error));
                System.Threading.Thread.Sleep(2);
            }
#else
            throw new OperatorFault("mailbox_platform_unqualified");
#endif
        }
    }
}
