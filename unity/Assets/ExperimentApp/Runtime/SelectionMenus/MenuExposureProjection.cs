using System;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.SessionEngine;
namespace AcousticVocab.SelectionMenus
{
    // Typed provisional #72 handoff. These are derived exposure facts, not the
    // unavailable methodology CSV header and not acoustic-onset evidence.
    public sealed class MenuExposureRow
    {
        public string EventId{get;}public string AttemptId{get;}public string OpportunityId{get;}public string AudioRequestId{get;}
        public string CandidateId{get;}public string PcmSha256{get;}public string FileSha256{get;}
        public string AcceptedOrRejected{get;}public string ActiveChoiceOrDefault{get;}public string YokedSourceEventId{get;}
        public double PauseMs{get;}public string MatchingDeviationId{get;}public string PlaybackStatus{get;}
        internal MenuExposureRow(MenuEvent request,string accepted,string choice,double pause,string deviation,string playback)
        {EventId=request.EventId;AttemptId=request.AttemptId;OpportunityId=request.OpportunityId;AudioRequestId=request.AudioRequestId;CandidateId=request.CandidateId;PcmSha256=request.PcmSha256;FileSha256=request.FileSha256;AcceptedOrRejected=accepted;ActiveChoiceOrDefault=choice;YokedSourceEventId=request.YokedSourceEventId;PauseMs=pause;MatchingDeviationId=deviation;PlaybackStatus=playback;}
    }
    public static class MenuExposureProjection
    {
        public static IReadOnlyList<MenuExposureRow> Derive(IEnumerable<MenuEvent> events)
        {
            var rows=events?.ToArray();MenuRules.Require(rows!=null&&rows.All(x=>x!=null)&&rows.Select(x=>x.EventId).Distinct().Count()==rows.Length,"MENU_EXPOSURE_EVENTS");
            var result=new List<MenuExposureRow>();double previousEnd=-1;
            foreach(var group in rows.GroupBy(x=>x.AttemptId).OrderBy(x=>x.First().SlotStartMonoMs))
            {
                var menu=group.ToArray();var first=menu.First();MenuRules.Require(menu.All(x=>x.MenuKey==first.MenuKey&&x.OpportunityId==first.OpportunityId&&x.SlotStartMonoMs==first.SlotStartMonoMs),"MENU_EXPOSURE_CONTEXT");
                var choices=menu.Where(x=>x.Kind=="choice_final").ToArray();var receipts=menu.Where(x=>x.Kind=="selection_verified").ToArray();var interruptions=menu.Where(x=>x.Kind=="menu_interrupted").ToArray();MenuRules.Require(choices.Length<=1&&receipts.Length<=1&&interruptions.Length<=1,"MENU_EXPOSURE_EVENTS");
                bool verified=choices.Length==1&&receipts.Length==1;int selected=verified?choices[0].SelectedIndex.Value:0;
                if(verified)MenuRules.Require(receipts[0].SelectedIndex==selected&&choices[0].Defaulted==receipts[0].Defaulted,"MENU_EXPOSURE_CHOICE");
                double pause=previousEnd<0?0:first.SlotStartMonoMs-previousEnd;MenuRules.Require(pause>=0,"MENU_EXPOSURE_OVERLAP");previousEnd=first.SlotStartMonoMs+(first.MenuKey=="profile"?60000:45000);
                foreach(var request in menu.Where(x=>x.Kind=="play_request"))
                {
                    var evidence=menu.Where(x=>x.AudioRequestId==request.AudioRequestId).ToArray();MenuRules.Require(evidence.All(x=>x.CandidateId==request.CandidateId&&x.PcmSha256==request.PcmSha256&&x.FileSha256==request.FileSha256)&&evidence.Count(x=>x.Kind=="play_request")==1&&evidence.Count(x=>x.Kind=="onset_authority")<=1&&evidence.Count(x=>x.Kind=="play_complete")<=1,"MENU_EXPOSURE_AUDIO");
                    int presented=request.PresentationIndex.Value<=6?(request.PresentationIndex.Value+1)/2:selected;
                    string accepted=!verified?"unresolved":presented==selected?"accepted":"rejected",choice=!verified?"unresolved":choices[0].Defaulted==true?"default":"choice";
                    string playback=evidence.Any(x=>x.Kind=="play_complete")?"software_completed":evidence.Any(x=>x.Kind=="onset_authority")?"onset_observed":"requested_uncertain";
                    result.Add(new MenuExposureRow(request,accepted,choice,pause,interruptions.FirstOrDefault()?.MatchingDeviationId,playback));
                }
            }
            return result.AsReadOnly();
        }
    }
}
