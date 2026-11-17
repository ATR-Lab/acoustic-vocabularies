using System;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.SessionEngine;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.DataLogging
{
    // Journals every scheduled slot of a visit as one opportunity row before
    // anything can request audio, so a slot that is never reached still has a
    // durable row. On relaunch the rows already in the journal are verified
    // against the schedule and only missing ones are added, never a second copy.
    public static class ScheduledOpportunities
    {
        // The row is the original scheduled opportunity. A technical retry is a
        // later attempt of it (trial-log retry_of), never a second opportunity.
        public const string Role="primary";
        // methodMasked is the package's anonymous ID (book or bank), never a method label.
        public static int Journal(DataJournal journal,VisitSchedule schedule,string methodMasked)
        {
            DataJson.Require(journal!=null&&schedule!=null&&DataJson.Hash(schedule.Sha256)&&DataJson.Id(methodMasked),"DATA_OPPORTUNITY_INPUT");
            var expected=schedule.Blocks.SelectMany(b=>b.Items.Select(i=>DataObservations.Opportunity(i.TrialId,Role,methodMasked,schedule.Sha256,b.Name,i.Pass,i.TrialType))).ToList();
            var scheduled=new HashSet<string>(StringComparer.Ordinal);
            foreach(var draft in expected)DataJson.Require(scheduled.Add(draft.Context.OpportunityId),"DATA_OPPORTUNITY_DUPLICATE");
            var journaled=new Dictionary<string,JObject>(StringComparer.Ordinal);bool audio=false;
            foreach(var record in journal.Records)
            {
                audio|=record.Kind=="audio_request";
                if(record.Kind!="opportunity")continue;
                string id=record.Context.OpportunityId;
                DataJson.Require(scheduled.Contains(id),"DATA_OPPORTUNITY_UNSCHEDULED");
                DataJson.Require(!journaled.ContainsKey(id),"DATA_OPPORTUNITY_DUPLICATE");journaled.Add(id,record.Payload);
            }
            foreach(var draft in expected)
                if(journaled.TryGetValue(draft.Context.OpportunityId,out var prior))DataJson.Require(JToken.DeepEquals(prior,draft.Payload),"DATA_OPPORTUNITY_CHANGED");
            var missing=expected.Where(d=>!journaled.ContainsKey(d.Context.OpportunityId)).ToList();
            // A row added after audio could no longer show the slot was listed first.
            DataJson.Require(missing.Count==0||!audio,"DATA_OPPORTUNITY_AFTER_AUDIO");
            foreach(var draft in missing)journal.Append(draft);
            return missing.Count;
        }
    }
}
