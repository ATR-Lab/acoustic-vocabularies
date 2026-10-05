using System;
using System.Diagnostics;
using System.Threading;
using UnityEngine;
using RosSharp.RosBridgeClient;
using RosSharp.RosBridgeClient.Protocols;
using RosString = RosSharp.RosBridgeClient.MessageTypes.Std.String;

namespace AcousticVocab.Spikes.Bridge
{
    // Requires approved ROS# 2.3.0 transport source and ROS2 scripting symbol.
    // Uses the official RosSocket, Newtonsoft serializer and WebSocketSharpProtocol.
    public sealed class RosSharpBenchmarkTransport : MonoBehaviour, IRosSharpBridgeTransport
    {
        sealed class DeferredProtocol : IProtocol
        {
            readonly IProtocol inner;
            public DeferredProtocol(IProtocol value) { inner = value; }
            public event EventHandler OnReceive { add => inner.OnReceive += value; remove => inner.OnReceive -= value; }
            public event EventHandler OnConnected { add => inner.OnConnected += value; remove => inner.OnConnected -= value; }
            public event EventHandler OnClosed { add => inner.OnClosed += value; remove => inner.OnClosed -= value; }
            public void Connect() { } // RosSocket constructor requests this before its variable is assigned.
            public void StartConnection() => inner.Connect();
            public void Close() => inner.Close();
            public bool IsAlive() => inner.IsAlive();
            public void Send(byte[] data) => inner.Send(data);
        }

        BridgeBenchmark benchmark;
        DeferredProtocol protocol;
        RosSocket socket;
        string echoPublisher;
        EventHandler receiver, connected, closed;
        int connectedFlag, closedFlag, epoch, failures;
        bool running, subscriptionsReady;
        double connectStarted, retryAt, nextEcho;
        long lastReceiveTicks;
        long typedStateMessages, typedEchoMessages;
        static double Now => (double)Stopwatch.GetTimestamp() / Stopwatch.Frequency;
        static readonly double[] Backoff = { .25, .5, 1, 2, 5 };
        public long TypedStateMessages => Interlocked.Read(ref typedStateMessages);
        public long TypedEchoMessages => Interlocked.Read(ref typedEchoMessages);

        public void Begin(BridgeBenchmark target)
        { benchmark = target; running = true; retryAt = Now; }

        void Connect()
        {
            connectedFlag = closedFlag = 0; subscriptionsReady = false;
            protocol = new DeferredProtocol(new WebSocketSharpProtocol(benchmark.settings.uri));
            epoch = benchmark.BeginExternalConnection();
            int thisEpoch = epoch;
            receiver = (sender, args) =>
            {
                long ticks = Stopwatch.GetTimestamp(); Interlocked.Exchange(ref lastReceiveTicks, ticks);
                // Hook is registered before RosSocket's deserializer so receipt time includes its cost downstream.
                benchmark.ExternalReceive(((MessageEventArgs)args).RawData, (double)ticks / Stopwatch.Frequency, thisEpoch);
            };
            connected = (_, __) => Interlocked.Exchange(ref connectedFlag, 1);
            closed = (_, __) => Interlocked.Exchange(ref closedFlag, 1);
            protocol.OnReceive += receiver; protocol.OnConnected += connected; protocol.OnClosed += closed;
            socket = new RosSocket(protocol, RosSocket.SerializerEnum.Newtonsoft_JSON);
            connectStarted = Now; Interlocked.Exchange(ref lastReceiveTicks, Stopwatch.GetTimestamp());
            protocol.StartConnection();
        }

        void Update()
        {
            if (!running) return;
            try
            {
                if (protocol == null) { if (Now >= retryAt) Connect(); return; }
                if (Volatile.Read(ref closedFlag) != 0 ||
                    (Volatile.Read(ref connectedFlag) == 0 && Now - connectStarted > 5) ||
                    (subscriptionsReady && Now - (double)Interlocked.Read(ref lastReceiveTicks) / Stopwatch.Frequency > 2))
                { Reconnect(); return; }
                if (Volatile.Read(ref connectedFlag) != 0 && !subscriptionsReady)
                {
                    benchmark.ExternalConnected();
                    socket.Subscribe<RosString>("/spike/state", _ => Interlocked.Increment(ref typedStateMessages), queue_length: 1);
                    socket.Subscribe<RosString>("/spike/echo/reply", _ => Interlocked.Increment(ref typedEchoMessages), queue_length: 1);
                    echoPublisher = socket.Advertise<RosString>("/spike/echo/request");
                    subscriptionsReady = true; failures = 0; nextEcho = Now;
                }
                if (subscriptionsReady && Now >= nextEcho)
                { socket.Publish(echoPublisher, new RosString(benchmark.ExternalEchoRequest())); nextEcho = Now + 1; }
            }
            catch (Exception) { benchmark.ExternalProtocolError(); Reconnect(); }
        }

        void Reconnect()
        {
            Release(); benchmark.ExternalDisconnected();
            retryAt = Now + Backoff[Math.Min(failures++, Backoff.Length - 1)];
        }
        void Release()
        {
            if (protocol == null) return;
            protocol.OnReceive -= receiver; protocol.OnConnected -= connected; protocol.OnClosed -= closed;
            try { socket?.Close(); } catch (Exception) { benchmark.ExternalProtocolError(); }
            try { protocol.Close(); } catch (Exception) { benchmark.ExternalProtocolError(); }
            socket = null; protocol = null; subscriptionsReady = false;
        }
        public void Stop() { running = false; Release(); }
        void OnDisable() { Stop(); }
    }
}
