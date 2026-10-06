using System;

namespace AcousticVocab.SessionEngine
{
    public sealed class ContentCueGateRefusal
    {
        public string Code {get;}
        public SlotContext Context {get;}
        public SlotReadiness Readiness {get;}
        public double CheckedMonoMs {get;}
        internal ContentCueGateRefusal(string code,SlotContext context,SlotReadiness readiness,double checkedMonoMs)
        {Code=code;Context=context;Readiness=readiness;CheckedMonoMs=checkedMonoMs;}
    }

    // One instance belongs to one factory lease. This observes the existing
    // inner check, not a new admission authority or a cached readiness grant.
    public sealed class ContentCueGate
    {
        readonly string code;readonly Action<ContentCueGateRefusal> refused;
        bool checking;
        public bool Failed {get;private set;}
        public ContentCueGate(string code,Action<ContentCueGateRefusal> refused=null)
        {
            if(code!="LESSON_CUE_REFUSED"&&code!="MENU_CUE_REFUSED")throw new SessionFault("CONTENT_CUE_DIAGNOSTIC_CODE");
            this.code=code;this.refused=refused;
        }
        public void Check(SlotContext context,Func<bool> prefix,Func<SlotReadiness> read,Func<bool> suffix,Func<double> now)
        {
            if(Failed||checking)throw new SessionFault(code);
            checking=true;
            try
            {
                if(!prefix())throw new SessionFault(code);
                double checkedAt=now();
                if(double.IsNaN(checkedAt)||double.IsInfinity(checkedAt)||checkedAt<0){Failed=true;throw new SessionFault(code);}
                var ready=read();
                if(!ready.Ready)
                {
                    try{refused?.Invoke(new ContentCueGateRefusal(code,context,ready,checkedAt));}
                    catch{Failed=true;}
                    throw new SessionFault(code);
                }
                if(!suffix())throw new SessionFault(code);
            }
            finally{checking=false;}
        }
    }
}
