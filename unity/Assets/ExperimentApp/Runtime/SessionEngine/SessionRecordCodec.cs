using System;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.SessionEngine
{
    // Shared record contract for a durable ISessionJournal implementation.
    // This validates the record only; the journal still owns transactional
    // persistence, hash chains, ordering and per-epoch monotonic checks.
    public static class SessionRecordCodec
    {
        public static JObject ToJson(SessionRecord record)
        {
            if(record==null)throw new SessionFault("SESSION_RECORD_INVALID");
            return SessionJournal.Serialize(FromJson(SessionJournal.Serialize(record)));
        }

        public static SessionRecord FromJson(JObject value)
        {
            try
            {
                if(value==null)throw new SessionFault("SESSION_RECORD_INVALID");
                return SessionJournal.ParseRecord(value);
            }
            catch(SessionFault){throw;}
            catch(Exception){throw new SessionFault("SESSION_RECORD_INVALID");}
        }
    }
}
