using System;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.ResponsePanel;
using AcousticVocab.SessionEngine;
using AcousticVocab.StateIntegration;
using AcousticVocab.StudyAudio;
using AcousticVocab.Teaching;

namespace AcousticVocab.SelectionMenus
{
    public interface IMenuStore : ITeachingSelections
    {
        string BankSha256{get;}string Profile{get;}
        bool Ready{get;}
        void Pump();
        void RequestVerification(bool waveComplete=false);
        string RequestSelection(string menuKey,string profile,int? rank);
        bool TryGetReceipt(string exactRequestId,out string receiptSha256);
        bool VerifyRecordedSelection(string menuKey,int selectedIndex,string receiptSha256,MenuCatalog catalog);
    }
    public interface IMenuView
    {
        bool InputAvailable{get;}
        event Action<int> Chosen;
        void Prepare(MenuMaterial material);
        void Show(string owner,MenuMaterial material,MenuPhase phase,int? selected,bool readOnly);
        void Hide(string owner);
    }
    // Sole module owner while menus are active. A multiplexer must dispose this
    // before subscribing a lesson/assessment module to the same AudioPlayer.
    public sealed class MenuContentFactory:ISlotContentFactory,ISessionContentPump,ISlotStartPlan,IDisposable
    {
        readonly MenuCatalog catalog;readonly IMenuStore store;readonly PrivateModeResetClient backend;
        readonly AudioPlayer audio;readonly ResponsePanelController panel;readonly StateSourceHost source;readonly IMenuView view;
        readonly Func<bool> focused;readonly Action<MenuEvent> persist;readonly Action<AudioPlaybackEvent> audioPersist;readonly Action<string> faultSink;
        readonly Action<SlotContext,int,PcmWave> bindAudio;readonly MenuReplaySequence replay;readonly List<Content> live=new List<Content>();bool disposed;
        public MenuContentFactory(MenuCatalog catalog,IMenuStore store,PrivateModeResetClient backend,AudioPlayer audio,ResponsePanelController panel,
            StateSourceHost source,IMenuView view,Func<bool> focused,Action<MenuEvent> durableMenuSink,Action<AudioPlaybackEvent> durableAudioSink,Action<string> faultSink,MenuReplaySequence replay=null,Action<SlotContext,int,PcmWave> bindAudio=null)
        {
            MenuRules.Require(catalog!=null&&store!=null&&store.PackageSha256==catalog.PackageSha256&&store.BankSha256==catalog.BankSha256&&backend!=null&&backend.RequiredMode=="test"&&audio!=null&&panel!=null&&source!=null&&view!=null&&focused!=null&&durableMenuSink!=null&&durableAudioSink!=null&&faultSink!=null&&(catalog.Role=="yoked")== (replay!=null),"MENU_DEPENDENCIES");
            this.catalog=catalog;this.store=store;this.backend=backend;this.audio=audio;this.panel=panel;this.source=source;this.view=view;this.focused=focused;persist=durableMenuSink;audioPersist=durableAudioSink;this.faultSink=faultSink;this.replay=replay;this.bindAudio=bindAudio;
            audio.Event+=AudioEvent;view.Chosen+=Choose;
            try{if(!backend.ModeAcknowledged)backend.RequestMode();}catch{audio.Event-=AudioEvent;view.Chosen-=Choose;throw;}
        }
        public bool ExposureGate=>!disposed&&focused()&&view.InputAvailable&&panel.ReadyForTrial&&source.CheckExposureReady()&&backend.ModeAcknowledged&&backend.NeutralHoldHealthy&&store.Ready&&store.OldHashesVerified;
        public double MinimumGapBeforeMs(SlotItem item,double baseline)=>replay?.MinimumGapBeforeMs(item,baseline)??0;
        public ISlotContent Create(SlotItem item)
        {MenuRules.Require(!disposed&&store.Ready&&store.OldHashesVerified,"MENU_STORE_UNVERIFIED");var content=new Content(this,catalog.Prepare(item,store.Profile));live.Add(content);return content;}
        public void Pump()
        {
            if(disposed)return;
            try
            {
                backend.Pump();store.Pump();double now=AudioPlayer.Now*1000;
                foreach(var item in live.ToArray())item.Tick(now);live.RemoveAll(x=>x.Retired);
            }
            catch(SessionFault e){faultSink(e.Code);}catch(AudioFault e){faultSink(e.Code);}catch(ControlFault e){faultSink(e.Code);}catch{faultSink("MENU_INTEGRATION_FAILED");}
        }
        void Choose(int index)
        {
            var target=live.Where(x=>x.Timeline?.Phase==MenuPhase.Choice&&!x.Interrupted).ToArray();MenuRules.Require(target.Length==1&&catalog.Role=="active"&&ExposureGate,"MENU_CHOICE_REFUSED");target[0].Timeline.Choose(index,AudioPlayer.Now*1000);
        }
        void AudioEvent(AudioPlaybackEvent value)
        {
            audioPersist(value);var owner=live.SingleOrDefault(x=>x.Context.AudioRequestIds?.Contains(value.AudioId)==true);MenuRules.Require(owner!=null,"MENU_AUDIO_CONTEXT");
            if(value.Code=="AUDIO_REQUESTED")return;
            if(value.Code is "AUDIO_ONSET_ESTIMATED" or "SIMULATION_DELIVERY_OBSERVED")
            {MenuRules.Require(!value.Timing.CalibrationOnly&&value.CallbackCount>0&&value.Timing.PresentationAnchorMonoSeconds.HasValue&&value.Timing.PresentationUncertaintyMs.HasValue&&(value.Timing.RouteOffsetMs.HasValue||value.Timing.SimulationOnly),"MENU_ONSET_AUTHORITY");owner.Timeline.Onset(value.AudioId,value.Timing.PresentationAnchorMonoSeconds.Value*1000,value.Timing.PresentationUncertaintyMs.Value,value.ObservedMonoSeconds*1000);}
            else if(value.Code=="AUDIO_PLAYBACK_COMPLETED")owner.Timeline.Completed(value.AudioId,value.ObservedMonoSeconds*1000);
            else{owner.Interrupt(value.Code);faultSink(value.Code);}
        }
        static Exception Attempt(params Action[] stages){Exception first=null;foreach(var stage in stages)try{stage();}catch(Exception e){if(first==null)first=e;}return first;}
        static void Throw(Exception error){if(error!=null)throw new SessionFault(error is SessionFault s?s.Code:error is AudioFault a?a.Code:"MENU_CLEANUP_FAILED");}
        public void Dispose()
        {
            if(disposed)return;disposed=true;Exception first=null;
            try{foreach(var item in live.ToArray()){var e=Attempt(()=>item.Interrupt("MENU_SHUTDOWN"));if(first==null)first=e;}var last=Attempt(()=>audio.Abort("MENU_SHUTDOWN"),backend.Interrupt);if(first==null)first=last;}
            finally{audio.Event-=AudioEvent;view.Chosen-=Choose;live.Clear();}Throw(first);
        }
        sealed class Content:ISlotContent
        {
            readonly MenuContentFactory owner;readonly MenuMaterial material;internal SlotContext Context;internal MenuTimeline Timeline;
            string initialReset,finalReset,commitRequest;bool prepared,initialRenderer,finalRenderer,resetWanted,interrupted,storeFinalRequested;int? selected;
            internal bool Interrupted=>interrupted;
            internal Content(MenuContentFactory owner,MenuMaterial material){this.owner=owner;this.material=material;}
            internal bool Retired=>interrupted||Timeline!=null&&Timeline.Complete&&finalRenderer;
            void Preload(int chosen)
            {owner.audio.Preload(Context.AudioRequestIds.Select((id,i)=>(id,i)).ToDictionary(x=>x.id,x=>material.Options[x.i<6?x.i/2:chosen-1].Wave),16*1024*1024);}
            public void Prepare(SlotContext context)
            {
                MenuRules.Require(!prepared&&context.RetryOf==null,"MENU_PREPARE_REFUSED");Context=context;owner.view.Prepare(material);Preload(1);
                var replay=owner.replay?.For(context.Item);Timeline=new MenuTimeline(context,material.Options.ToArray(),material.MeaningDisplayId,owner.persist,replay,()=>AudioPlayer.Now*1000);
                Timeline.PlayRequested+=(index,id,option,at)=>{MenuRules.Require(ExposureReady,"MENU_EXPOSURE_GATE");owner.bindAudio?.Invoke(Context,index-1,option.Wave);owner.audio.Schedule(id,at/1000);};
                Timeline.DisplayChanged+=(phase,choice)=>{if(phase is MenuPhase.Hidden or MenuPhase.Neutral or MenuPhase.Ended)owner.view.Hide(Context.Item.TrialId);else owner.view.Show(Context.Item.TrialId,material,phase,choice,owner.catalog.Role=="yoked");};
                Timeline.SelectionRequested+=(index,unused)=>
                {MenuRules.Require(commitRequest==null&&owner.catalog.Role=="active","MENU_COMMIT_DUPLICATE");selected=index;commitRequest=owner.store.RequestSelection(material.Key,material.Key=="profile"?owner.catalog.ProfileAt(index):owner.store.Profile,material.Key=="profile"?(int?)null:index);};
                if(replay!=null){selected=replay.SelectedIndex;MenuRules.Require(owner.store.VerifyRecordedSelection(material.Key,replay.SelectedIndex,replay.SelectionReceiptSha256,owner.catalog),"MENU_REPLAY_RECEIPT");Preload(replay.SelectedIndex);}
                initialReset=owner.backend.RequestReset();prepared=true;
            }
            bool ExposureReady=>!interrupted&&owner.ExposureGate;
            public SlotReadiness Readiness=>new SlotReadiness(prepared&&owner.store.Ready&&owner.store.OldHashesVerified,prepared&&!interrupted&&(owner.audio.TrialReady||owner.audio.Playing),initialReset!=null&&owner.backend.ResetAcknowledged(initialReset),initialRenderer&&owner.source.CheckExposureReady(),owner.panel.State!=null&&(owner.panel.State.Request==null||owner.panel.State.Locked),owner.focused(),owner.view.InputAvailable&&owner.panel.ReadyForTrial,owner.backend.ModeAcknowledged&&owner.backend.NeutralHoldHealthy);
            public bool ResetComplete=>owner.store.Ready&&owner.store.OldHashesVerified&&finalReset!=null&&owner.backend.ResetAcknowledged(finalReset)&&finalRenderer&&owner.source.CheckExposureReady();
            public void RequestCue(SlotContext context,INovelSlotAuthorization authorization){MenuRules.Require(authorization==null&&Readiness.Ready&&!interrupted,"MENU_CUE_REFUSED");Timeline.Start(AudioPlayer.Now*1000);}
            public void OpenResponse(SlotContext context){} // Choice is the private menu deadline, not the engine tail.
            public void CloseResponse(SlotContext context){}
            public void RequestReset(SlotContext context){resetWanted=true;}
            internal void Tick(double now)
            {
                if(interrupted)return;
                if(!initialRenderer&&initialReset!=null&&owner.backend.ResetAcknowledged(initialReset))initialRenderer=owner.source.ConfirmReset();
                if(commitRequest!=null&&owner.store.TryGetReceipt(commitRequest,out string receipt))
                {MenuRules.Require(selected.HasValue&&owner.store.Ready&&owner.store.OldHashesVerified,"MENU_COMMIT_UNVERIFIED");Preload(selected.Value);Timeline.ConfirmSelection(selected.Value,receipt,now);commitRequest=null;}
                Timeline?.Tick(now);
                if(resetWanted&&!storeFinalRequested&&material.Key==owner.catalog.MenuKeys.Last()){owner.store.RequestVerification(true);storeFinalRequested=true;}
                if(resetWanted&&finalReset==null&&(Timeline?.Phase is MenuPhase.Neutral or MenuPhase.Ended)&&!owner.audio.Playing)finalReset=owner.backend.RequestReset();
                if(!finalRenderer&&finalReset!=null&&owner.backend.ResetAcknowledged(finalReset))finalRenderer=owner.source.ConfirmReset();
            }
            public void Interrupt(string code)
            {if(interrupted)return;interrupted=true;Throw(Attempt(()=>Timeline?.Interrupt(AudioPlayer.Now*1000),()=>owner.view.Hide(Context.Item?.TrialId),()=>owner.audio.Abort(code),owner.backend.Interrupt));}
        }
    }
}
