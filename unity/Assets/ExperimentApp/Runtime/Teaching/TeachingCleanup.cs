using System;
using AcousticVocab.SessionEngine;
using AcousticVocab.StateIntegration;
using AcousticVocab.StudyAudio;

namespace AcousticVocab.Teaching
{
    // A broken durable observer must not prevent later physical/output cleanup.
    // Keep the first failure while independently attempting every stage.
    internal static class TeachingCleanup
    {
        internal static Exception Attempt(params Action[] stages)
        {
            Exception first=null;
            foreach(var stage in stages)try{stage?.Invoke();}catch(Exception error){if(first==null)first=error;}
            return first;
        }
        internal static string Code(Exception error)=>error is SessionFault session?session.Code:
            error is AudioFault audio?audio.Code:error is ControlFault control?control.Code:"LESSON_CLEANUP_FAILED";
        internal static void ThrowFirst(Exception error){if(error!=null)throw new SessionFault(Code(error));}
        internal static void Notify(Action<string> handlers,string code)
        {
            Exception first=null;
            if(handlers!=null)foreach(Action<string> handler in handlers.GetInvocationList())
            {var error=Attempt(()=>handler(code));if(first==null)first=error;}
            ThrowFirst(first);
        }
    }
}
