using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
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
        readonly struct ResetReceipt
        {internal readonly double Received,Sample;internal ResetReceipt(double received,double sample){Received=received;Sample=sample;}}
        readonly string session,mode;
        readonly ConcurrentQueue<string> outgoing=new ConcurrentQueue<string>();
        readonly ConcurrentQueue<Arrival> incoming=new ConcurrentQueue<Arrival>();
        readonly Dictionary<string,string> pending=new Dictionary<string,string>(StringComparer.Ordinal);
        readonly Dictionary<string,ResetReceipt> resets=new Dictionary<string,ResetReceipt>(StringComparer.Ordinal);
        readonly CancellationTokenSource lifetime=new CancellationTokenSource();
        readonly Action<JObject> persist;
        readonly Func<double> now;
        readonly Task worker;
        ClientWebSocket socket;
        volatile bool failed;
        bool disposed,modeAcknowledged,pumping,requestPersisting;
        int queued;
        readonly object diagnosticLock=new object();
        string workerPhase="not_started",firstFailureCode,firstFailurePhase;
        string failedExchangeKind,failedExchangeRequestId;PrivateControlExchange.Failure exchangeFailure;
        double phaseStarted,lastSent=-1,lastReceived=-1,firstFailureAt;
        double probeSent=-1,probeSample=-1,commandReceived=-1,commandSample=-1;bool latestObservationWasProbe;
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
        public bool NeutralHoldHealthy {get{if(failed||disposed||requestPersisting)return false;Pump();return ModeAcknowledged&&pending.Count==0&&latestObservationWasProbe&&probeSent>=commandReceived&&probeSample>commandSample&&healthGate.Fresh;}}
        // A reset command may finish while the last published frame is already
        // old. Keep its exact durable ACK, but require an actual progressing
        // probe sent after that ACK's full receipt before admitting exposure.
        // Its original RTT, source ages and receipt age still obey the same gate.
        public bool ResetAcknowledged(string exactRequestId) => exactRequestId!=null&&NeutralHoldHealthy&&PostResetProbeObserved(exactRequestId);
        bool PostResetProbeObserved(string id)=>id!=null&&resets.TryGetValue(id,out var receipt)&&latestObservationWasProbe&&probeSent>=receipt.Received&&probeSample>receipt.Sample;
        public JObject ReadinessDiagnostic(string exactRequestId)
        {
            var value=healthGate.Diagnostic();value["failed"]=failed;value["disposed"]=disposed;value["mode_acknowledged"]=modeAcknowledged;
            value["exact_reset_recorded"]=exactRequestId==null?JValue.CreateNull():new JValue(resets.ContainsKey(exactRequestId));value["queued_arrivals"]=Volatile.Read(ref queued);
            value["post_reset_probe_observed"]=exactRequestId==null?JValue.CreateNull():new JValue(PostResetProbeObserved(exactRequestId));
            value["latest_observation_was_probe"]=latestObservationWasProbe;
            value["last_probe_sent_local_mono_ms"]=probeSent<0?JValue.CreateNull():new JValue(probeSent);
            value["last_probe_health_sample_host_mono_ms"]=probeSample<0?JValue.CreateNull():new JValue(probeSample);
            value["pending_commands"]=pending.Count;
            value["last_command_received_local_mono_ms"]=commandReceived<0?JValue.CreateNull():new JValue(commandReceived);
            value["last_command_health_sample_host_mono_ms"]=commandSample<0?JValue.CreateNull():new JValue(commandSample);
            lock(diagnosticLock)
            {
                value["worker_phase"]=workerPhase;value["phase_started_local_mono_ms"]=phaseStarted;
                value["last_completed_sent_local_mono_ms"]=lastSent<0?JValue.CreateNull():new JValue(lastSent);
                value["last_completed_received_local_mono_ms"]=lastReceived<0?JValue.CreateNull():new JValue(lastReceived);
                value["first_failure_code"]=firstFailureCode==null?JValue.CreateNull():new JValue(firstFailureCode);value["first_failure_phase"]=firstFailurePhase==null?JValue.CreateNull():new JValue(firstFailurePhase);
                value["first_failure_local_mono_ms"]=firstFailureCode==null?JValue.CreateNull():new JValue(firstFailureAt);
                value["exchange_failure"]=exchangeFailure==null?JValue.CreateNull():exchangeFailure.ToJson(failedExchangeKind,failedExchangeRequestId);
            }
            return value;
        }
        string Request(string command)
        {
            Require(!failed&&!disposed&&!requestPersisting&&pending.Count<4,"CONTROL_UNAVAILABLE");
            string id=Guid.NewGuid().ToString("N");var request=new JObject{["version"]=1,["kind"]="private_command",["control_session_id"]=session,["request_id"]=id,["command"]=command,["args"]=command=="set_mode"?new JObject{["mode"]=mode}:new JObject()};
            // No earlier probe may authorize exposure while a new mutation is
            // pending, even if another fresh probe is already in the queue.
            healthGate.Invalidate();latestObservationWasProbe=false;requestPersisting=true;
            try
            {
                persist(new JObject{["kind"]="control_request",["local_mono_ms"]=now(),["request"]=request.DeepClone()});
                pending.Add(id,command);outgoing.Enqueue(request.ToString(Formatting.None));return id;
            }
            catch(Exception error){RecordFailure(FailureCode(error),"request_persist");Interrupt();throw;}
            finally{requestPersisting=false;}
        }
        public void RequestMode(){Require(!modeAcknowledged&&!pending.Values.Contains("set_mode"),"CONTROL_MODE_PENDING");Request("set_mode");}
        public string RequestReset()=>Request("reset");
        public void Interrupt(){RecordFailure("CONTROL_EXPLICIT_INTERRUPT","owner");healthGate.Invalidate();modeAcknowledged=false;failed=true;lifetime.Cancel();socket?.Abort();}
        void Phase(string value){lock(diagnosticLock){workerPhase=value;phaseStarted=now();}}
        void Completed(PrivateControlExchange.Reply reply){lock(diagnosticLock){lastSent=reply.Sent;lastReceived=reply.Received;}}
        void ExchangeFailed(string kind,string requestId,PrivateControlExchange.Failure failure)
        {lock(diagnosticLock){if(exchangeFailure!=null)return;failedExchangeKind=kind;failedExchangeRequestId=requestId;exchangeFailure=failure;}}
        void RecordFailure(string code,string phase=null)
        {lock(diagnosticLock){if(firstFailureCode!=null)return;firstFailureCode=code;firstFailurePhase=phase??workerPhase;firstFailureAt=now();}}
        internal static string FailureCode(Exception error)=>error is ControlFault bounded?bounded.Code:
            error is OperationCanceledException?"CONTROL_OPERATION_CANCELLED":error is WebSocketException?"CONTROL_SOCKET_ERROR":
            error is DecoderFallbackException?"CONTROL_UTF8_INVALID":error is IOException?"CONTROL_IO_FAILED":"CONTROL_WORKER_EXCEPTION";
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
                    if(item.Health)
                    {
                        var health=PrivateHealthProbe.Payload(value,session,item.Id);healthGate.Observe(health,item.Sent,item.Received);
                        probeSent=item.Sent;probeSample=Number(health["health_sample_host_mono_ms"]);latestObservationWasProbe=true;continue;
                    }
                    Keys(value,"version","kind","request_id","accepted","reason","mode","host_mono_ms","sim_time","reset_ok","duplicate","health");
                    pending.TryGetValue(item.Id,out var command);Require(value["version"].Type==JTokenType.Integer&&(int)value["version"]==1&&(string)value["kind"]=="private_reply"&&(string)value["request_id"]==item.Id&&command!=null,"CONTROL_REPLY");
                    Number(value["host_mono_ms"]);Number(value["sim_time"]);Require(!Bool(value["duplicate"]),"CONTROL_UNEXPECTED_DUPLICATE");
                    Require(Bool(value["accepted"])&&(string)value["mode"]==mode,"CONTROL_REJECTED");
                    double sample=healthGate.ObserveCommandCompletion((JObject)value["health"],item.Sent,item.Received);
                    latestObservationWasProbe=false;
                    if(command=="set_mode")Require((string)value["reason"]=="MODE_CHANGED"&&(mode=="teaching"?value["reset_ok"].Type==JTokenType.Null:Bool(value["reset_ok"])),"CONTROL_REPLY");
                    else Require((string)value["reason"]=="RESET_COMPLETE"&&Bool(value["reset_ok"]),"CONTROL_RESET");
                    persist(new JObject{["kind"]="control_reply",["local_mono_ms"]=item.Received,["reply"]=value.DeepClone()});
                    commandReceived=item.Received;commandSample=sample;
                    pending.Remove(item.Id);if(command=="set_mode")modeAcknowledged=true;else resets.Add(item.Id,new ResetReceipt(item.Received,sample));
                    Require(resets.Count<=512,"CONTROL_CAPACITY");
                }
                catch(Exception error){RecordFailure(FailureCode(error),"pump");Interrupt();throw;}
            }
            }
            finally{pumping=false;}
        }
        internal void ReceiveHealthProbe(string id,string raw,double sent,double received)=>Offer(new Arrival{Health=true,Id=id,Raw=raw,Sent=sent,Received=received});
        internal void ReceiveReply(string id,string raw,double sent,double received)=>Offer(new Arrival{Id=id,Raw=raw,Sent=sent,Received=received});
        void Offer(Arrival item)
        {if(Interlocked.Increment(ref queued)>8){Interlocked.Decrement(ref queued);RecordFailure("CONTROL_ARRIVAL_CAPACITY","receive_queue");failed=true;lifetime.Cancel();return;}incoming.Enqueue(item);}
        async Task Run(Uri endpoint)
        {
            try
            {
                using var client=new ClientWebSocket();socket=client;Phase("connect");
                using(var timeout=CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token)){timeout.CancelAfter(3000);await client.ConnectAsync(endpoint,timeout.Token);}
                while(!lifetime.IsCancellationRequested)
                {
                    if(outgoing.TryDequeue(out var raw))
                    {
                        string id=(string)StationConfig.ParseStrict(raw)["request_id"];
                        Phase("command_exchange");
                        var reply=await PrivateControlExchange.Run(client,raw,ControlHealthGate.CommandDeadlineMs,now,lifetime.Token,failure=>ExchangeFailed("command",id,failure));
                        Completed(reply);
                        ReceiveReply(id,reply.Raw,reply.Sent,reply.Received);
                    }
                    string probeId=Guid.NewGuid().ToString("N");
                    string probe=PrivateHealthProbe.Request(session,probeId).ToString(Formatting.None);
                    Phase("health_exchange");
                    var health=await PrivateControlExchange.Run(client,probe,200,now,lifetime.Token,failure=>ExchangeFailed("health_probe",probeId,failure));
                    Completed(health);
                    ReceiveHealthProbe(probeId,health.Raw,health.Sent,health.Received);
                    Phase("cadence_delay");
                    await Task.Delay(ControlPollCadence.DelayMilliseconds(health.Sent,now()),lifetime.Token);
                }
            }
            catch(Exception error){if(!disposed){RecordFailure(FailureCode(error));failed=true;}}
            finally{socket=null;}
        }
        public void Dispose(){if(disposed)return;RecordFailure("CONTROL_DISPOSED","owner");disposed=true;failed=true;lifetime.Cancel();socket?.Abort();}
    }
}
