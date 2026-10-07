using System;
using System.IO;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.StateIntegration
{
    internal static class PrivateControlExchange
    {
        internal sealed class Reply
        {
            internal readonly string Raw;internal readonly double Sent,Received;
            internal Reply(string raw,double sent,double received){Raw=raw;Sent=sent;Received=received;}
        }
        internal sealed class Failure
        {
            internal readonly string Stage;
            internal readonly int TimeoutMs,Fragments,Bytes;
            internal readonly double Sent,SendCompleted,ReceiveStarted,FirstFragment,LastFragment,MessageCompleted,Observed;
            internal readonly bool TimeoutCancelled,LifetimeCancelled;
            internal Failure(string stage,int timeoutMs,double sent,double sendCompleted,double receiveStarted,double firstFragment,double lastFragment,double messageCompleted,double observed,int fragments,int bytes,bool timeoutCancelled,bool lifetimeCancelled)
            {Stage=stage;TimeoutMs=timeoutMs;Sent=sent;SendCompleted=sendCompleted;ReceiveStarted=receiveStarted;FirstFragment=firstFragment;LastFragment=lastFragment;MessageCompleted=messageCompleted;Observed=observed;Fragments=fragments;Bytes=bytes;TimeoutCancelled=timeoutCancelled;LifetimeCancelled=lifetimeCancelled;}
            internal JObject ToJson(string kind,string requestId)
            {
                JToken Stamp(double value)=>double.IsFinite(value)&&value>=0?new JValue(value):JValue.CreateNull();
                return new JObject{["exchange_kind"]=kind,["request_id"]=requestId,["stage"]=Stage,["timeout_ms"]=TimeoutMs,
                    ["send_started_local_mono_ms"]=Stamp(Sent),["send_completed_local_mono_ms"]=Stamp(SendCompleted),
                    ["receive_await_started_local_mono_ms"]=Stamp(ReceiveStarted),["first_fragment_local_mono_ms"]=Stamp(FirstFragment),
                    ["last_fragment_local_mono_ms"]=Stamp(LastFragment),["full_message_local_mono_ms"]=Stamp(MessageCompleted),
                    ["failure_observed_local_mono_ms"]=Stamp(Observed),["elapsed_at_failure_ms"]=Stamp(Observed-Sent),
                    ["received_fragments"]=Fragments,["received_bytes"]=Bytes,["timeout_token_cancelled"]=TimeoutCancelled,["lifetime_token_cancelled"]=LifetimeCancelled};
            }
        }
        // One caller owns this socket. The deadline spans send and every receive
        // fragment. Receipt time is measured here, never when Unity pumps it.
        internal static async Task<Reply> Run(WebSocket socket,string request,int timeoutMs,Func<double> now,CancellationToken lifetime,Action<Failure> observeFailure=null)
        {
            byte[] bytes=Encoding.UTF8.GetBytes(request);
            using var timeout=CancellationTokenSource.CreateLinkedTokenSource(lifetime);timeout.CancelAfter(timeoutMs);
            double sent=now();
            double sendCompleted=-1,receiveStarted=-1,firstFragment=-1,lastFragment=-1,messageCompleted=-1;
            int fragments=0,receivedBytes=0;string stage="send";
            try
            {
                await socket.SendAsync(new ArraySegment<byte>(bytes),WebSocketMessageType.Text,true,timeout.Token);
                sendCompleted=now();
                using var message=new MemoryStream();var buffer=new byte[4096];WebSocketReceiveResult part;
                do
                {
                    stage="receive";receiveStarted=now();
                    part=await socket.ReceiveAsync(new ArraySegment<byte>(buffer),timeout.Token);
                    lastFragment=now();if(firstFragment<0)firstFragment=lastFragment;fragments++;receivedBytes+=part.Count;
                    if(part.MessageType!=WebSocketMessageType.Text||message.Length+part.Count>16384)throw new ControlFault("CONTROL_TRANSPORT_MESSAGE");
                    message.Write(buffer,0,part.Count);
                }while(!part.EndOfMessage);
                messageCompleted=lastFragment;stage="decode";
                string raw=new UTF8Encoding(false,true).GetString(message.ToArray());double received=now();
                stage="validate";timeout.Token.ThrowIfCancellationRequested();
                if(!double.IsFinite(sent)||!double.IsFinite(received)||sent<0||received<sent||received-sent>timeoutMs)throw new ControlFault("CONTROL_TRANSPORT_DEADLINE");
                return new Reply(raw,sent,received);
            }
            catch
            {
                // No per-probe writes or payload capture. A failing observer
                // cannot replace the original transport refusal or resume IO.
                try{observeFailure?.Invoke(new Failure(stage,timeoutMs,sent,sendCompleted,receiveStarted,firstFragment,lastFragment,messageCompleted,now(),fragments,receivedBytes,timeout.IsCancellationRequested,lifetime.IsCancellationRequested));}catch{}
                throw;
            }
        }
    }
}
