using System;
using System.IO;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace AcousticVocab.StateIntegration
{
    internal static class PrivateControlExchange
    {
        internal sealed class Reply
        {
            internal readonly string Raw;internal readonly double Sent,Received;
            internal Reply(string raw,double sent,double received){Raw=raw;Sent=sent;Received=received;}
        }
        // One caller owns this socket. The deadline spans send and every receive
        // fragment. Receipt time is measured here, never when Unity pumps it.
        internal static async Task<Reply> Run(WebSocket socket,string request,int timeoutMs,Func<double> now,CancellationToken lifetime)
        {
            byte[] bytes=Encoding.UTF8.GetBytes(request);
            using var timeout=CancellationTokenSource.CreateLinkedTokenSource(lifetime);timeout.CancelAfter(timeoutMs);
            double sent=now();
            await socket.SendAsync(new ArraySegment<byte>(bytes),WebSocketMessageType.Text,true,timeout.Token);
            using var message=new MemoryStream();var buffer=new byte[4096];WebSocketReceiveResult part;
            do
            {
                part=await socket.ReceiveAsync(new ArraySegment<byte>(buffer),timeout.Token);
                if(part.MessageType!=WebSocketMessageType.Text||message.Length+part.Count>16384)throw new ControlFault("CONTROL_TRANSPORT_MESSAGE");
                message.Write(buffer,0,part.Count);
            }while(!part.EndOfMessage);
            string raw=new UTF8Encoding(false,true).GetString(message.ToArray());double received=now();
            timeout.Token.ThrowIfCancellationRequested();
            if(!double.IsFinite(sent)||!double.IsFinite(received)||sent<0||received<sent||received-sent>timeoutMs)throw new ControlFault("CONTROL_TRANSPORT_DEADLINE");
            return new Reply(raw,sent,received);
        }
    }
}
