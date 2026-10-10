using System;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.IO;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.StateSources
{
    // Network workers only exchange bounded immutable text records. Pump is the
    // sole caller of the state source and clock, and belongs to Unity's thread.
    public sealed class LiveSocketClient : IDisposable
    {
        sealed class Arrival { public string Raw, Fault; public double Received; public int Epoch; }
        readonly Uri endpoint;
        readonly SceneRegistry registry;
        readonly LiveIsaacSource source;
        readonly SourceClock clock;
        readonly Func<double> processingClock;
        readonly Func<string,SceneRegistry,SceneFrame> parseFrame;
        readonly ConcurrentQueue<Arrival> queue=new ConcurrentQueue<Arrival>();
        readonly ConcurrentDictionary<double,byte> echoes=new ConcurrentDictionary<double,byte>();
        readonly CancellationTokenSource lifetime=new CancellationTokenSource();
        readonly Task worker;
        ClientWebSocket socket;
        int queued, dropped, totalDropped, epoch, processedEpoch, disposed;
        public int DroppedMessages => Volatile.Read(ref totalDropped);
        public static double Now => (double)Stopwatch.GetTimestamp()/Stopwatch.Frequency;
        public LiveSocketClient(string uri,SceneRegistry expected,LiveIsaacSource destination,SourceClock sourceClock=null)
            :this(uri,expected,destination,sourceClock,()=>Now,(raw,registry)=>StateParser.Parse(raw,registry),true){}
        // Deterministic tests exercise the real bounded queue without opening
        // a socket. Production always uses the same Stopwatch as receipt stamps.
        internal LiveSocketClient(string uri,SceneRegistry expected,LiveIsaacSource destination,SourceClock sourceClock,
            Func<double> processingClock,Func<string,SceneRegistry,SceneFrame> parseFrame,bool connect)
        {
            if(!Uri.TryCreate(uri,UriKind.Absolute,out endpoint) || (endpoint.Scheme!="ws" && endpoint.Scheme!="wss") ||
                endpoint.UserInfo.Length!=0 || endpoint.Fragment.Length!=0 || endpoint.AbsolutePath!="/state")
                throw new ArgumentException("Explicit public /state endpoint required");
            registry=expected; source=destination; clock=sourceClock;
            this.processingClock=processingClock??throw new ArgumentNullException(nameof(processingClock));
            this.parseFrame=parseFrame??throw new ArgumentNullException(nameof(parseFrame));
            worker=connect?Task.Run(Run):Task.CompletedTask;
        }
        internal void ReceiveRaw(string raw,double received,int arrivalEpoch=1)=>Offer(new Arrival { Raw=raw,Received=received,Epoch=arrivalEpoch });
        internal void ReceiveFault(string fault,double received,int arrivalEpoch=1)=>Offer(new Arrival { Fault=fault,Received=received,Epoch=arrivalEpoch });
        void Offer(Arrival arrival)
        {
            if(Interlocked.Increment(ref queued)>8)
            { Interlocked.Decrement(ref queued); Interlocked.Increment(ref dropped); Interlocked.Increment(ref totalDropped); return; }
            queue.Enqueue(arrival);
        }
        async Task Run()
        {
            while(!lifetime.IsCancellationRequested)
            {
                int current=Interlocked.Increment(ref epoch);
                using var client=new ClientWebSocket();
                socket=client;
                using var connection=CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);
                try
                {
                    using(var connect=CancellationTokenSource.CreateLinkedTokenSource(connection.Token))
                    {
                        connect.CancelAfter(TimeSpan.FromSeconds(10));
                        await client.ConnectAsync(endpoint,connect.Token);
                    }
                    echoes.Clear();
                    Offer(new Arrival { Fault="STATE_TRANSPORT_CONNECTED",Received=Now,Epoch=current });
                    var sender=SendEchoes(client,connection.Token);
                    try
                    {
                        var chunk=new byte[4096];
                        while(client.State==WebSocketState.Open && !connection.IsCancellationRequested)
                        {
                            using var message=new MemoryStream();
                            WebSocketReceiveResult received;
                            do
                            {
                                received=await client.ReceiveAsync(new ArraySegment<byte>(chunk),connection.Token);
                                if(received.MessageType!=WebSocketMessageType.Text) throw new StateFault("STATE_SOCKET_MESSAGE_TYPE");
                                if(message.Length+received.Count>65536) throw new StateFault("STATE_SOCKET_MESSAGE_SIZE");
                                message.Write(chunk,0,received.Count);
                            } while(!received.EndOfMessage);
                            Offer(new Arrival { Raw=new UTF8Encoding(false,true).GetString(message.ToArray()),Received=Now,Epoch=current });
                        }
                    }
                    finally
                    {
                        connection.Cancel();
                        try { await sender; } catch(OperationCanceledException) { } catch(WebSocketException) { }
                    }
                }
                catch(Exception) when(!lifetime.IsCancellationRequested)
                { Offer(new Arrival { Fault="STATE_TRANSPORT_DISCONNECTED",Received=Now,Epoch=current }); }
                catch(OperationCanceledException) { }
                finally { socket=null; }
                if(!lifetime.IsCancellationRequested)
                    try { await Task.Delay(1000,lifetime.Token); } catch(OperationCanceledException) { }
            }
        }
        async Task SendEchoes(ClientWebSocket client,CancellationToken cancellation)
        {
            while(!cancellation.IsCancellationRequested)
            {
                double sent=Now;
                if(echoes.Count>=8) echoes.Clear();
                echoes.TryAdd(sent,0);
                byte[] bytes=Encoding.UTF8.GetBytes(new JObject { ["kind"]="echo",["c0_s"]=sent }.ToString(Formatting.None));
                await client.SendAsync(new ArraySegment<byte>(bytes),WebSocketMessageType.Text,true,cancellation);
                await Task.Delay(1000,cancellation);
            }
        }
        // The server echoes c0_s verbatim, but Unity's Mono double parser does
        // not always return the correctly rounded value: about 2 in 10,000
        // Stopwatch-second stamps come back one ULP away. Correlate to exactly
        // one outstanding echo within 1 us (echoes are sent 1 s apart) and keep
        // that echo's original send time. Anything else stays uncorrelated.
        internal const double EchoMatchSeconds=1e-6;
        bool TakeEcho(double echoed,out double sent)
        {
            sent=double.NaN;int matches=0;
            foreach(double pending in echoes.Keys)if(Math.Abs(pending-echoed)<=EchoMatchSeconds){sent=pending;matches++;}
            return matches==1&&echoes.TryRemove(sent,out _);
        }
        double ProcessingTime(double previous)
        {
            double value=processingClock();
            if(double.IsNaN(value)||double.IsInfinity(value)||value<previous)throw new StateFault("HOST_CLOCK_REGRESSED");
            return value;
        }
        // The caller's timestamp is only a monotonic floor. Parsing and source
        // observers can take time while the worker enqueues newer arrivals.
        // Re-sample the same clock after parsing; never alter receipt stamps.
        // Return the final time so a subsequent render cannot use the old floor.
        public double Pump(double now)
        {
            if(double.IsNaN(now)||double.IsInfinity(now))throw new StateFault("HOST_CLOCK_REGRESSED");
            for(int count=0;count<8&&queue.TryDequeue(out var item);count++)
            {
                Interlocked.Decrement(ref queued);
                now=ProcessingTime(now);
                if(item.Epoch<processedEpoch) continue;
                if(item.Epoch!=processedEpoch)
                { processedEpoch=item.Epoch; clock?.Reset(); source.Invalidate("STATE_TRANSPORT_RESTART",now); }
                if(item.Fault!=null) { source.Invalidate(item.Fault,now); continue; }
                SceneFrame frame=null;
                try
                {
                    JObject json=StateParser.Json(item.Raw);
                    if((string)json["kind"]=="echo")
                    {
                        StateParser.Keys(json,"kind","c0_s","s1_ns","s2_ns");
                        if(!TakeEcho(StateParser.Number(json["c0_s"]),out double sent)) throw new StateFault("STATE_ECHO_CORRELATION");
                        clock?.Echo(sent,StateParser.Nanoseconds(json["s1_ns"]),StateParser.Nanoseconds(json["s2_ns"]),item.Received);
                    }
                    else frame=parseFrame(item.Raw,registry);
                }
                catch(StateFault ex) { now=ProcessingTime(now); source.Invalidate(ex.Message,now); continue; }
                catch(Exception) { now=ProcessingTime(now); source.Invalidate("STATE_MALFORMED",now); continue; }
                now=ProcessingTime(now);
                if(frame!=null)
                    try { source.Receive(frame,item.Received,now); }
                    catch(StateFault ex) { now=ProcessingTime(now); source.Invalidate(ex.Message,now); }
                    catch(Exception) { now=ProcessingTime(now); source.Invalidate("STATE_MALFORMED",now); }
            }
            now=ProcessingTime(now);
            if(Interlocked.Exchange(ref dropped,0)>0) source.Invalidate("STATE_RECEIVE_QUEUE_OVERFLOW",now);
            return ProcessingTime(now);
        }
        public void Dispose()
        {
            if(Interlocked.Exchange(ref disposed,1)!=0) return;
            lifetime.Cancel();
            socket?.Abort();
            // No Unity-thread wait on a network operation. Observe completion in
            // the continuation so a worker failure cannot become unobserved.
            _=worker.ContinueWith(task => { _=task.Exception; lifetime.Dispose(); },TaskScheduler.Default);
        }
    }
}
