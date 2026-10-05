using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.WebSockets;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using AcousticVocab.Foundation;
using System.Diagnostics;

using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.StateIntegration
{
    public sealed class ControlFault : Exception
    {
        public string Code {get;}
        public ControlFault(string code):base(code!=null&&Regex.IsMatch(code,@"\A[A-Z][A-Z0-9_]{0,63}\z")?code:"CONTROL_FAULT"){Code=Message;}
    }
    // No target-bearing command exists on this interface. Only the separately
    // provisioned loopback /commands endpoint is accepted. The control-session
    // pin must be obtained independently of this connection, per station.
    public sealed class PrivateModeResetClient : IDisposable
    {
        sealed class Arrival { internal string Id,Raw;internal double Sent,Received;internal bool Health; }
        readonly string session,mode;
        readonly ConcurrentQueue<string> outgoing=new ConcurrentQueue<string>();
        readonly ConcurrentQueue<Arrival> incoming=new ConcurrentQueue<Arrival>();
        readonly Dictionary<string,string> pending=new Dictionary<string,string>(StringComparer.Ordinal);
        readonly HashSet<string> resets=new HashSet<string>(StringComparer.Ordinal);
        readonly CancellationTokenSource lifetime=new CancellationTokenSource();
        readonly Action<JObject> persist;
        readonly Func<double> now;
        readonly Task worker;
        ClientWebSocket socket;
        volatile bool failed;
        bool disposed,modeAcknowledged,pumping;
        int queued;
        readonly ControlHealthGate healthGate;
        public PrivateModeResetClient(string endpoint,string independentlyPinnedControlSessionId,string requiredMode,Action<JObject> durableControlSink)
            :this(endpoint,independentlyPinnedControlSessionId,requiredMode,durableControlSink,()=>NowMs,true){}
        // The deterministic transport boundary exercises the real receive queue
        // and persistence path without opening a network connection in tests.
        internal PrivateModeResetClient(string endpoint,string independentlyPinnedControlSessionId,string requiredMode,Action<JObject> durableControlSink,Func<double> monotonicClock,bool connect)
        {
            Require(Uri.TryCreate(endpoint,UriKind.Absolute,out var uri)&&uri.Scheme=="ws"&&IPAddress.TryParse(uri.Host,out var ip)&&IPAddress.IsLoopback(ip)&&uri.AbsolutePath=="/commands"&&uri.Query.Length==0&&uri.Fragment.Length==0&&uri.UserInfo.Length==0,"CONTROL_ENDPOINT");
            Require(independentlyPinnedControlSessionId!=null&&Regex.IsMatch(independentlyPinnedControlSessionId,@"\A[0-9a-f]{32}\z"),"CONTROL_SESSION");
            Require(requiredMode=="teaching"||requiredMode=="test","CONTROL_MODE_INVALID");mode=requiredMode;session=independentlyPinnedControlSessionId;persist=durableControlSink??throw new ArgumentNullException(nameof(durableControlSink));now=monotonicClock??throw new ArgumentNullException(nameof(monotonicClock));healthGate=new ControlHealthGate(session,mode,now);worker=connect?Task.Run(()=>Run(uri)):Task.CompletedTask;
        }
        public bool ModeAcknowledged => !failed&&!disposed&&modeAcknowledged;
        public string RequiredMode => mode;
        // Exposure reads run on the owning main thread. A durable write or a
        // different Unity Update order can leave newer real replies queued.
        // Drain that bounded batch before testing freshness; never wait for the
        // network, invent a receive time, or extend the 250 ms bound.
        public bool NeutralHoldHealthy {get{if(failed||disposed)return false;Pump();return ModeAcknowledged&&healthGate.Fresh;}}
        public bool ResetAcknowledged(string exactRequestId) => exactRequestId!=null&&NeutralHoldHealthy&&resets.Contains(exactRequestId);
        public JObject ReadinessDiagnostic(string exactRequestId)
        {
            var value=healthGate.Diagnostic();value["failed"]=failed;value["disposed"]=disposed;value["mode_acknowledged"]=modeAcknowledged;
            value["exact_reset_recorded"]=exactRequestId!=null&&resets.Contains(exactRequestId);value["queued_arrivals"]=Volatile.Read(ref queued);return value;
        }
        string Request(string command)
        {
            Require(!failed&&!disposed&&pending.Count<4,"CONTROL_UNAVAILABLE");
            string id=Guid.NewGuid().ToString("N");var request=new JObject{["version"]=1,["kind"]="private_command",["control_session_id"]=session,["request_id"]=id,["command"]=command,["args"]=command=="set_mode"?new JObject{["mode"]=mode}:new JObject()};
            persist(new JObject{["kind"]="control_request",["local_mono_ms"]=now(),["request"]=request.DeepClone()});
            pending.Add(id,command);outgoing.Enqueue(request.ToString(Formatting.None));return id;
        }
        public void RequestMode(){Require(!modeAcknowledged&&!pending.Values.Contains("set_mode"),"CONTROL_MODE_PENDING");Request("set_mode");}
        public string RequestReset()=>Request("reset");
        public void Interrupt(){healthGate.Invalidate();modeAcknowledged=false;failed=true;lifetime.Cancel();socket?.Abort();}
        static double NowMs => (double)Stopwatch.GetTimestamp()/Stopwatch.Frequency*1000;
        static void Require(bool condition,string code){if(!condition)throw new ControlFault(code);}
        static void Keys(JObject value,params string[] keys)=>Require(value!=null&&value.Properties().Select(x=>x.Name).OrderBy(x=>x).SequenceEqual(keys.OrderBy(x=>x)),"CONTROL_SCHEMA");
        static double Number(JToken value)
        {Require(value?.Type is JTokenType.Integer or JTokenType.Float,"CONTROL_SCHEMA");double n=(double)value;Require(!double.IsNaN(n)&&!double.IsInfinity(n)&&n>=0,"CONTROL_SCHEMA");return n;}
        static bool Bool(JToken value){Require(value?.Type==JTokenType.Boolean,"CONTROL_SCHEMA");return(bool)value;}
        public void Pump()
        {
            Require(!failed&&!disposed,"CONTROL_UNAVAILABLE");
            Require(!pumping,"CONTROL_REENTRANCY");pumping=true;
            try
            {
            int batch=Math.Min(8,Volatile.Read(ref queued));
            for(int i=0;i<batch&&incoming.TryDequeue(out var item);i++)
            {
                Interlocked.Decrement(ref queued);
                try
                {
                    double readAt=now();Require(readAt>=item.Received&&readAt-item.Received<=250,"CONTROL_QUEUED");
                    var value=StationConfig.ParseStrict(item.Raw);
                    if(item.Health){healthGate.Observe(value,item.Sent,item.Received);continue;}
                    Keys(value,"version","kind","request_id","accepted","reason","mode","host_mono_ms","sim_time","reset_ok","duplicate","health");
                    pending.TryGetValue(item.Id,out var command);Require(value["version"].Type==JTokenType.Integer&&(int)value["version"]==1&&(string)value["kind"]=="private_reply"&&(string)value["request_id"]==item.Id&&command!=null,"CONTROL_REPLY");
                    Number(value["host_mono_ms"]);Number(value["sim_time"]);Bool(value["duplicate"]);
                    Require(Bool(value["accepted"])&&(string)value["mode"]==mode,"CONTROL_REJECTED");
                    healthGate.Observe((JObject)value["health"],item.Sent,item.Received);
                    if(command=="set_mode")Require((string)value["reason"]=="MODE_CHANGED"&&(mode=="teaching"?value["reset_ok"].Type==JTokenType.Null:Bool(value["reset_ok"])),"CONTROL_REPLY");
                    else Require((string)value["reason"]=="RESET_COMPLETE"&&Bool(value["reset_ok"]),"CONTROL_RESET");
                    persist(new JObject{["kind"]="control_reply",["local_mono_ms"]=item.Received,["reply"]=value.DeepClone()});
                    pending.Remove(item.Id);if(command=="set_mode")modeAcknowledged=true;else resets.Add(item.Id);
                    Require(resets.Count<=512,"CONTROL_CAPACITY");
                }
                catch{Interrupt();throw;}
            }
            }
            finally{pumping=false;}
        }
        internal void ReceiveHealth(string raw,double sent,double received)=>Offer(new Arrival{Health=true,Raw=raw,Sent=sent,Received=received});
        internal void ReceiveReply(string id,string raw,double sent,double received)=>Offer(new Arrival{Id=id,Raw=raw,Sent=sent,Received=received});
        void Offer(Arrival item)
        {if(Interlocked.Increment(ref queued)>8){Interlocked.Decrement(ref queued);failed=true;lifetime.Cancel();return;}incoming.Enqueue(item);}
        async Task Run(Uri endpoint)
        {
            try
            {
                using var client=new ClientWebSocket();socket=client;
                using(var timeout=CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token)){timeout.CancelAfter(3000);await client.ConnectAsync(endpoint,timeout.Token);}
                using var handler=new HttpClientHandler{UseProxy=false,AllowAutoRedirect=false};using var http=new HttpClient(handler){Timeout=TimeSpan.FromMilliseconds(200)};
                var healthEndpoint=new UriBuilder(endpoint){Scheme="http",Path="/health"}.Uri;
                while(!lifetime.IsCancellationRequested)
                {
                    if(outgoing.TryDequeue(out var raw))
                    {
                        string id=(string)StationConfig.ParseStrict(raw)["request_id"];double sent=now();
                        using var timeout=CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);timeout.CancelAfter(3000);
                        byte[] bytes=Encoding.UTF8.GetBytes(raw);await client.SendAsync(new ArraySegment<byte>(bytes),WebSocketMessageType.Text,true,timeout.Token);
                        using var message=new MemoryStream();var buffer=new byte[4096];WebSocketReceiveResult part;
                        do{part=await client.ReceiveAsync(new ArraySegment<byte>(buffer),timeout.Token);if(part.MessageType!=WebSocketMessageType.Text||message.Length+part.Count>16384)throw new IOException();message.Write(buffer,0,part.Count);}while(!part.EndOfMessage);
                        ReceiveReply(id,new UTF8Encoding(false,true).GetString(message.ToArray()),sent,now());
                    }
                    double before=now();
                    using var healthTimeout=CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);healthTimeout.CancelAfter(200);
                    using var response=await http.GetAsync(healthEndpoint,HttpCompletionOption.ResponseHeadersRead,healthTimeout.Token);response.EnsureSuccessStatusCode();
                    using var stream=await response.Content.ReadAsStreamAsync();using var health=new MemoryStream();var chunk=new byte[4096];int count;
                    while((count=await stream.ReadAsync(chunk,0,chunk.Length,healthTimeout.Token))>0){if(health.Length+count>16384)throw new IOException();health.Write(chunk,0,count);}
                    ReceiveHealth(new UTF8Encoding(false,true).GetString(health.ToArray()),before,now());
                    await Task.Delay(75,lifetime.Token);
                }
            }
            catch{if(!disposed)failed=true;}
            finally{socket=null;}
        }
        public void Dispose(){if(disposed)return;disposed=true;failed=true;lifetime.Cancel();socket?.Abort();}
    }
}
