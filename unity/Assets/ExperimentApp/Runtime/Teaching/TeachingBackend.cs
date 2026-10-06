using System;
using AcousticVocab.StateIntegration;

namespace AcousticVocab.Teaching
{
    public sealed class TeachingBackend : ITeachingBackend,IDisposable
    {
        readonly PrivateModeResetClient client;
        public TeachingBackend(string endpoint,string independentControlSessionId,Action<Newtonsoft.Json.Linq.JObject> durableSink)
        {client=new PrivateModeResetClient(endpoint,independentControlSessionId,"teaching",durableSink);}
        public void Pump()=>client.Pump();
        public void RequestTeachingMode()=>client.RequestMode();
        public bool TeachingModeAcknowledged=>client.ModeAcknowledged;
        public bool NeutralHoldHealthy=>client.NeutralHoldHealthy;
        public string RequestReset()=>client.RequestReset();
        public bool ResetAcknowledged(string id)=>client.ResetAcknowledged(id);
        public void Interrupt()=>client.Interrupt();
        public void Dispose()=>client.Dispose();
    }
}
