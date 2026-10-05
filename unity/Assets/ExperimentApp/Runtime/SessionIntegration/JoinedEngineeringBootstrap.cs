using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.Assessment;
using AcousticVocab.DataLogging;
using AcousticVocab.Foundation;
using AcousticVocab.FrameBudget;
using AcousticVocab.OperatorConsole;
using AcousticVocab.ResponsePanel;
using AcousticVocab.SelectionMenus;
using AcousticVocab.SessionEngine;
using AcousticVocab.StateIntegration;
using AcousticVocab.StateSources;
using AcousticVocab.StudyAudio;
using AcousticVocab.Teaching;
using AcousticVocab.Soak;
using Newtonsoft.Json.Linq;
using UnityEngine;
namespace AcousticVocab.SessionIntegration
{
    // Explicit DEMO development entry point. No orientation/allocation, runtime
    // selection, calibration creation, private bridge process or automatic Resume.
    [DisallowMultipleComponent]
    public sealed class JoinedEngineeringBootstrap:MonoBehaviour
    {
        public FoundationBootstrap foundation;public StateSourceHost source;public ResponsePanelController panel;public AudioPlayer player;public FrameCaptureHost frames;
        public Font font;public Shader unlitShader,dictionaryShader;
        public bool requirePreallocation;
        AllocationJoinBinding allocation;string authorizedConfig,authorizedPin;
        internal void StartAllocated(AllocationJoinBinding binding,string configPath,string rawPin)
        {if(enabled||!requirePreallocation||allocation!=null||binding==null||attempted||assets!=null)throw new SessionFault("JOIN_ALLOCATION_ALREADY_CONSUMED");allocation=binding;authorizedConfig=configPath;authorizedPin=rawPin;enabled=true;}
        public string StatusCode{get;private set;}="JOIN_NOT_STARTED";
        public bool ParticipantAdmission=>false;
        public FixedSlotEngine Engine=>owner?.Engine;
        public AssessmentSessionHost ActiveAssessment{get;private set;}
        internal JoinedEngineeringConfig ObservationConfig=>config;
        internal JoinedVisitArtifacts ObservationAssets=>assets;
        internal void ObservationFailed(string code)=>Fail(code);
        internal SoakContext ObservationContext()
        {
            if(owner?.Engine==null)return new SoakContext("paused","startup",StatusCode,null);
            var engine=owner.Engine;string kind="paused",block=engine.CurrentBlock??"complete";
            if(engine.Status==SessionState.Running&&assets.Blocks.TryGetValue(block,out var module))
                kind=module==JoinedModuleKind.Teaching?"teaching":module==JoinedModuleKind.Assessment?"protected":"selection";
            return new SoakContext(kind,block,engine.Status.ToString(),engine.CurrentTrialId);
        }
        JoinedGrammarStage grammarStage;bool grammarInterrupted;
        JoinedEngineeringConfig config;JoinedVisitArtifacts assets;DataJournal data;JoinedAudit audit;FileMenuStore store;JoinedSelections selections;MenuLedger menuLedger;bool menuSealed;
        SessionIntegrationOwner owner;StagedModuleCoordinator staged;OperatorMailbox mailbox;FileOperatorCommandJournal commands;CompletedFormsRecovery formsRecovery;YokedReplayAuthority yokedAuthority;OperatorRequest resumeRequest;
        readonly ModuleConstructionScope visit=new ModuleConstructionScope();bool attempted,installedFrames,closed,failed,formsShown;string evidenceRoot,nonce;
        double? diagnosticQuitAt;
        sealed class Clock:ISessionClock{public double NowMs=>AudioPlayer.Now*1000;}
        readonly Clock clock=new Clock();
        static string Argument(string key)
        {var args=Environment.GetCommandLineArgs();for(int i=0;i<args.Length-1;i++)if(args[i]==key)return args[i+1];return null;}
        void Start()
        {
            if(requirePreallocation&&allocation==null){Fail("JOIN_PREALLOCATION_REQUIRED");return;}
            string path=authorizedConfig??Argument("-joinedConfig"),pin=authorizedPin??Argument("-joinedConfigSha256");
            if(path==null&&pin==null){Report("JOIN_CONFIG_REQUIRED");return;}
            try
            {
                var build=Resources.Load<TextAsset>("BuildIdentity");if(build==null)throw new SessionFault("JOIN_BUILD_IDENTITY");var identity=JoinedVisitArtifacts.Json(Encoding.UTF8.GetBytes(build.text));
                config=JoinedEngineeringConfig.Load(path,pin,(string)identity["protocol_version"]);
                if(config.BuildId!=(string)identity["build_id"])throw new SessionFault("JOIN_BUILD_IDENTITY");
                allocation?.Validate(config);
                assets=new JoinedVisitArtifacts(config);ValidateProvisioned();
                if(allocation!=null&&assets.Menus!=null&&allocation.Role!=assets.Menus.Role)throw new SessionFault("JOIN_ALLOCATION_ROLE");
                if(assets.MissingAuthority!=null)
                {
                    Report(assets.MissingAuthority);string quit=Argument("-joinedDiagnosticExitSeconds");
                    if(quit!=null){if(!int.TryParse(quit,out int seconds)||seconds<1||seconds>30)throw new SessionFault("JOIN_DIAGNOSTIC_EXIT_INVALID");diagnosticQuitAt=Time.realtimeSinceStartupAsDouble+seconds;}
                    return;
                }
                if(Argument("-joinedDiagnosticExitSeconds")!=null)throw new SessionFault("JOIN_DIAGNOSTIC_EXIT_REQUIRES_BLOCKED_STARTUP");
                attempted=true;Report("JOIN_WAITING_FOUNDATION");
            }
            catch(SessionFault error){Fail(error.Code);}catch{Fail("JOIN_CONFIGURATION_INVALID");}
        }
        void ValidateProvisioned()
        {
            if(foundation==null||source==null||panel==null||player==null||frames==null||foundation.Configuration==null||
                !JToken.DeepEquals(foundation.Configuration,JoinedVisitArtifacts.Json(config.RequireFile("station").ReadVerified())))throw new SessionFault("JOIN_SCENE_BINDING");
            foreach(var pair in new[]{("station","station.local.json"),("state_source","state-source.local.json"),("response_panel","response-panel.local.json")})
                MatchPrivate(pair.Item1,pair.Item2);
            var sourceSetup=StateSourceConfiguration.Load(Encoding.UTF8.GetString(config.RequireFile("state_source").ReadVerified()));
            if(config.RequireFile("neutral").Sha256!=sourceSetup.NeutralHash)throw new SessionFault("JOIN_NEUTRAL_PIN");MatchPrivate("neutral",sourceSetup.NeutralFile);
        }
        void MatchPrivate(string role,string name)
        {
            string path=Path.Combine(Application.persistentDataPath,name);for(FileSystemInfo entry=new FileInfo(path);entry!=null;entry=entry is FileInfo f?f.Directory:((DirectoryInfo)entry).Parent)
                if(entry.Exists&&(entry.Attributes&FileAttributes.ReparsePoint)!=0)throw new SessionFault("JOIN_PROVISIONED_LINK");
            var file=new FileInfo(path);var pin=config.RequireFile(role);if(!file.Exists||file.Length!=pin.Length||PcmWave.Hash(File.ReadAllBytes(path))!=pin.Sha256)throw new SessionFault("JOIN_PROVISIONED_HASH");
        }
        void Update()
        {
            if(diagnosticQuitAt.HasValue&&Time.realtimeSinceStartupAsDouble>=diagnosticQuitAt.Value)
            {diagnosticQuitAt=null;Report("JOIN_DIAGNOSTIC_QUIT_REQUESTED");Close();Application.Quit(0);return;}
            if(closed||failed||!attempted)return;
            try
            {
                if(data==null)
                {
                    if(!foundation.Ready||!source.Initialized||!panel.ReadyForTrial)return;ValidateProvisioned();
                    if(source.LoadedConfigurationSha256!=config.RequireFile("state_source").Sha256||source.LoadedNeutralSha256!=config.RequireFile("neutral").Sha256||panel.LoadedConfigurationSha256!=config.RequireFile("response_panel").Sha256)throw new SessionFault("JOIN_LOADED_CONFIG_MISMATCH");
                    // Identity and cross-file pins have already passed before
                    // creating any private output or starting control services.
                    nonce=Guid.NewGuid().ToString("N");evidenceRoot=Path.Combine(config.Directory("evidence"),"joined-"+nonce);Directory.CreateDirectory(evidenceRoot);
                    var build=Resources.Load<TextAsset>("BuildIdentity");data=visit.Own(new DataJournal(Path.Combine(config.Directory("evidence"),"data"),new DataIdentity(config.SessionId,config.CodedId,config.VisitId,config.StationId,config.ProtocolVersion,PcmWave.Hash(Encoding.UTF8.GetBytes(build.text))),Guid.NewGuid().ToString("N"),()=>clock.NowMs));
                    audit=visit.Own(new JoinedAudit(Path.Combine(evidenceRoot,"joined.local.jsonl"),()=>clock.NowMs));
                    audit.Write("configuration",new JObject{["config_sha256"]=config.ConfigSha256,["schedule_sha256"]=assets.Schedule.Sha256,["package_sha256"]=assets.Package.PackageSha256,["scope"]="DEMO_ENGINEERING",["participant_admission"]=false});
                    if(allocation!=null)audit.Write("configuration",new JObject{["orientation_receipt_sha256"]=allocation.OrientationReceiptSha256,["allocation_receipt_sha256"]=allocation.RevealReceiptSha256,["slot_id"]=allocation.SlotId,["unit_id"]=allocation.UnitId});
                    installedFrames=true;frames.Install(config.RequireFile("frame").ReadVerified(),Path.Combine(evidenceRoot,"frames"),data,Fail);Report("JOIN_WAITING_RENDER_BASELINE");
                }
                if(owner==null)
                {
                    if(!frames.Ready)return;
                    if(assets.Package.Study=="B")
                    {
                        var binding=JoinedSelections.CreateStoreBinding(config.RequireFile("menu_bridge_config").ReadVerified(),config.RequireFile("menu_bridge_config").Sha256,config.RequireFile("package_manifest").ReadVerified(),assets.Package,config.UnitId,config.Pin("bank_sha256"));
                        var wave=assets.Menus==null?JoinedVisitArtifacts.Json(assets.Permutation)["wave_atom_order"]["3"].Select(x=>(string)x).ToArray():assets.Menus.MenuKeys.Where(k=>k!="profile").ToArray();
                        audit.Write("configuration",new JObject{["producer_unit_id"]=config.UnitId,["bridge_unit_id"]=binding.UnitId,["bridge_config_sha256"]=binding.ConfigSha256});
                        store=visit.Own(new FileMenuStore(config.Directory("menu_mailbox"),binding,assets.Package,config.RequireFile("menu_snapshot").ReadVerified(),config.Pin("menu_manifest_sha256"),config.Pin("menu_head_sha256"),config.Pin("menu_snapshot_sha256"),wave,x=>audit.Write("store",x),()=>clock.NowMs,assets.Menus==null||assets.Menus.Role=="yoked"));
                    }
                    selections=new JoinedSelections(assets.Package,assets.Permutation,store);
                    if(assets.Menus!=null)menuLedger=visit.Own(new MenuLedger(Path.Combine(evidenceRoot,"menus.local.jsonl"),assets.Menus.Binding(JoinedSelections.SharedUnitBindingSha256(assets.Package.PackageSha256,config.RequireFile("permutation").Sha256,config.Pin("bank_sha256"),config.UnitId)),DateTimeOffset.UtcNow));
                    if(assets.YokedActiveBinding!=null)
                    {
                        var ledger=config.RequireFile("menu_replay_ledger");
                        yokedAuthority=new YokedReplayAuthority(config.YokedAnchorLeadMs.Value,nonce,ledger.Sha256,assets.YokedActiveSchedule.Sha256,()=>clock.NowMs,
                            record=>audit.Write("module",record.ToJson()),anchor=>
                            {
                                if(store==null||!store.Ready||!store.OldHashesVerified)throw new SessionFault("JOIN_YOKED_STORE_NOT_READY");
                                ledger.ReadVerified();return MenuReplaySequence.Load(ledger.Path,ledger.Sha256,assets.YokedActiveBinding,MenuVerification(),DateTimeOffset.UtcNow,anchor,()=>clock.NowMs);
                            });
                    }
                    var placeholders=assets.Blocks.ToDictionary(p=>p.Key,p=>(Func<SessionIntegrationOwner.Resources,ModuleConstructionScope,ISlotContentFactory>)((r,s)=>throw new SessionFault("JOIN_STAGED_CREATOR_REQUIRED")));
                    owner=new SessionIntegrationOwner(assets.Schedule,clock,data,player,panel,frames,placeholders);
                    if(assets.Grammar!=null)
                    {
                        grammarStage=new JoinedGrammarStage(data,assets.Grammar,assets.GrammarReview,assets.Schedule.Sha256,()=>clock.NowMs);
                        grammarStage.RequireBeforeTeachingHistory(owner.SessionJournal.Records,assets.Schedule.Blocks.Where(b=>assets.Blocks[b.Name]==JoinedModuleKind.Teaching).SelectMany(b=>b.Items).Select(i=>i.TrialId));
                    }
                    var routes=assets.Blocks.ToDictionary(p=>p.Key,p=>(Func<ModuleConstructionScope,IModulePreflight>)(scope=>new Preflight(this,p.Key,p.Value,scope)));
                    staged=new StagedModuleCoordinator(owner.Engine,owner.Modules,routes);
                    Directory.CreateDirectory(config.Directory("operator_mailbox"));commands=visit.Own(new FileOperatorCommandJournal(evidenceRoot,nonce));
                    mailbox=new OperatorMailbox(config.Directory("operator_mailbox"),nonce,config.RequireFile("run_sheet_manifest").Sha256,owner.Engine,commands,Admission,Health,()=>clock.NowMs,stageBeforeResume:CommitOperatorResume,boundaryControl:_=>{if(grammarStage?.Running==true)grammarInterrupted=true;});
                    Report("JOIN_PREFLIGHT");
                }
                store?.Pump();bool stageHold=HandleAssessmentBoundary();if(!stageHold)staged.Pump();mailbox.Tick(); // sole engine.Tick owner
                if(grammarInterrupted){Fail("JOIN_GRAMMAR_INTERRUPTED");return;}
                if(stageHold){staged.PumpPending();return;}
                if(grammarStage?.Running==true){Report("JOIN_GRAMMAR_RUNNING");return;}
                Report(owner.Engine.NeedsOperatorConfirmation?(staged.Ready?"JOIN_READY_EXPLICIT_RESUME":"JOIN_PREFLIGHT"):"JOIN_"+owner.Engine.Status.ToString().ToUpperInvariant());
            }
            catch(SessionFault error){Fail(error.Code);}catch{Fail("JOIN_RUNTIME_FAILED");}
        }
        bool CommitOperatorResume(FixedSlotEngine engine,OperatorRequest request)
        {
            if(resumeRequest!=null||request==null)throw new SessionFault("JOIN_OPERATOR_REENTRANCY");resumeRequest=request;
            try{if(!staged.PrepareExplicitResume(engine,request))return false;staged.CommitForResume(engine);return true;}finally{resumeRequest=null;}
        }
        MenuLedgerVerification MenuVerification()
        {
            var items=assets.Schedule.Blocks.SelectMany(b=>b.Items).Where(i=>i.Phase=="selection").ToDictionary(i=>i.TrialType=="profile_menu"?"profile":i.ContentId);
            return new MenuLedgerVerification(key=>assets.Menus.Prepare(items[key],store.Profile).Options.ToArray(),
                (key,index,receipt)=>store.VerifyRecordedSelection(key,index,receipt,assets.Menus),key=>assets.Menus.Prepare(items[key],store.Profile).MeaningDisplayId);
        }
        bool HandleAssessmentBoundary()
        {
            if(owner.Engine.Status!=SessionState.Complete&&!owner.Engine.NeedsOperatorConfirmation)return false;
            var assessment=ActiveAssessment;if(assessment?.Stages==null)
            {
                if(owner.Engine.Status==SessionState.Complete)
                {
                    formsRecovery??=visit.Own(new CompletedFormsRecovery(assets.Schedule,owner.Engine,clock,owner.SessionJournal,owner.StageJournal,assets.RatingsReviewed,PresentRecoveredForms));
                    formsRecovery.Pump();Report(formsRecovery.Complete?"JOIN_COMPLETE_FORMS_RECORDED":"JOIN_FORMS_RECOVERY_ACTIVE");return true;
                }
                return false;
            }
            if(assessment.Stages.ProtectedComplete&&!assessment.Stages.FormsComplete)
            {
                owner.PumpRetainedAtBoundary();if(!formsShown){assessment.BeginForms();formsShown=true;}Report("JOIN_FORMS_REQUIRED");return true;
            }
            if(owner.Engine.CurrentBlock=="validity"&&assets.Schedule.Demo)
            {owner.PumpRetainedAtBoundary();Report("JOIN_DEMO_VALIDITY_UNSUPPORTED");return true;}
            if(owner.Engine.Status==SessionState.Complete)
            {owner.PumpRetainedAtBoundary();Report(assessment.Stages.FormsComplete?"JOIN_COMPLETE_FORMS_RECORDED":"JOIN_FORMS_REQUIRED");return true;}
            return false;
        }
        IDisposable PresentRecoveredForms(AssessmentStages stages)
        {
            var scope=new ModuleConstructionScope();
            try
            {
                var go=new GameObject("Recovered forms only");scope.RegisterCleanup(()=>UnityEngine.Object.Destroy(go));
                var screen=go.AddComponent<AssessmentScreen>();scope.RegisterCleanup(screen.ReleaseView);
                screen.foundation=foundation;screen.trackingSpace=foundation.observerCamera.transform.parent;screen.inputSource=panel;
                screen.font=font;screen.unlitShader=unlitShader;screen.dictionaryShader=dictionaryShader;screen.Faulted+=Fail;
                scope.RegisterCleanup(()=>screen.Faulted-=Fail);screen.Configure(stages);screen.BeginForms();return scope;
            }
            catch{scope.Dispose();throw;}
        }
        OperatorAdmission Admission()=>new OperatorAdmission(!failed&&assets?.MissingAuthority==null,store==null||store.OldHashesVerified,!failed&&data!=null&&!data.Failed);
        OperatorHealth Health()
        {
            double age=source.SampleAgeSeconds*1000;if(!double.IsFinite(age)||age<0)age=1000000;
            return owner.Health(new OperatorHealth(foundation.Ready,assets.Route.IsQualified&&assets.Route.UncertaintyMs<=20,staged.Ready&&source.CheckExposureReady(),panel.ReadyForTrial,age,0,0));
        }
        void Report(string code){if(StatusCode==code)return;StatusCode=code;Debug.Log("JOINED_ENGINEERING_STATUS "+code+" participant_admission=false");}
        void Fail(string code)
        {
            if(failed||closed)return;failed=true;Report(new SessionFault(code).Code);
            try{owner?.Engine.Fault(StatusCode);}catch{}try{player?.Abort(StatusCode);}catch{}try{audit?.Write("fault",new JObject{["code"]=StatusCode});}catch{}Close();
        }
        void OnApplicationFocus(bool focused)
        {if(data!=null&&!data.Closed)try{data.Append(DataObservations.Device(null,"focus",clock.NowMs,focused));}catch{Fail("JOIN_DATA_APPEND_FAILED");}if(!focused&&owner!=null)Fail("JOIN_FOCUS_LOST");}
        void OnApplicationPause(bool paused){if(paused&&owner!=null)Fail("JOIN_APPLICATION_PAUSED");}
        void OnDisable(){if(owner!=null)Fail("JOIN_HOST_DISABLED");Close();}
        void OnDestroy()=>Close();
        void Close()
        {
            if(closed)return;closed=true;Exception first=null;
            foreach(Action action in new Action[]{()=>mailbox?.Dispose(),()=>staged?.Dispose(),()=>owner?.Dispose(),()=>{if(installedFrames)frames.FinishCapture();},()=>visit.Dispose()})try{action();}catch(Exception e){first??=e;}
            if(first!=null)Report("JOIN_DISPOSE_FAILED");
        }
        sealed class TeachingControl:ITeachingBackend
        {
            readonly PrivateModeResetClient client;internal TeachingControl(PrivateModeResetClient client){this.client=client;}
            public void Pump()=>client.Pump();public void RequestTeachingMode()=>client.RequestMode();public bool TeachingModeAcknowledged=>client.ModeAcknowledged;
            public bool NeutralHoldHealthy=>client.NeutralHoldHealthy;public string RequestReset()=>client.RequestReset();public bool ResetAcknowledged(string id)=>client.ResetAcknowledged(id);public void Interrupt()=>client.Interrupt();
        }
        sealed class Preflight:IModulePreflight,IExplicitBoundaryStage
        {
            readonly JoinedEngineeringBootstrap host;readonly string block;readonly JoinedModuleKind kind;PrivateModeResetClient control;readonly double started;readonly ModuleConstructionScope scope;ISlotContentFactory preparedFactory;bool wasReady;
            string reset;bool committed;bool renderer;TeachingSessionHost teachingView;
            internal Preflight(JoinedEngineeringBootstrap host,string block,JoinedModuleKind kind,ModuleConstructionScope scope)
            {
                this.host=host;this.block=block;this.kind=kind;this.scope=scope;started=host.clock.NowMs;
            }
            bool SealBeforePostMenu()
            {
                int next=host.assets.Schedule.Blocks.ToList().FindIndex(b=>b.Name==block),lastMenu=host.assets.Schedule.Blocks.ToList().FindLastIndex(b=>host.assets.Blocks[b.Name]==JoinedModuleKind.Menus);
                if(lastMenu<0||next<=lastMenu||host.menuSealed)return true;
                if(!host.store.Ready)return false;
                if(host.menuLedger!=null&&host.assets.Schedule.Blocks.Where(b=>host.assets.Blocks[b.Name]==JoinedModuleKind.Menus).All(b=>host.owner.Engine.CompletedCounts[host.assets.Schedule.Blocks.ToList().IndexOf(b)]==b.Items.Count))
                {
                    var verification=host.MenuVerification();
                    if(host.assets.Menus.Role=="yoked")
                    {
                        var comparison=host.menuLedger.SealYoked(host.yokedAuthority.Replay,verification);
                        host.audit.Write("module",new JObject{["kind"]="yoked_sealed",["active_ledger_sha256"]=comparison.ActiveLedgerSha256,["yoked_event_sha256"]=comparison.YokedEventSha256,["menus"]=comparison.Menus,["audio_plays"]=comparison.AudioPlays});
                    }
                    else host.menuLedger.Seal(verification);host.menuSealed=true;
                }
                if(!host.menuSealed)throw new SessionFault("JOIN_MENU_LEDGER_INCOMPLETE");return true;
            }
            void StartControl()
            {
                control=scope.Own(new PrivateModeResetClient(host.config.ControlEndpoint,host.config.ControlSessionId,kind==JoinedModuleKind.Teaching?"teaching":"test",x=>host.audit.Write("control",x)));
                host.audit.Write("module",new JObject{["kind"]="prepare",["block"]=block,["module"]=kind.ToString()});control.RequestMode();
            }
            public void Pump()
            {
                if(committed)throw new SessionFault("JOIN_PREFLIGHT_CONSUMED");if(!wasReady&&host.clock.NowMs-started>15000)throw new SessionFault("JOIN_PREFLIGHT_TIMEOUT");
                if(control==null){if(!SealBeforePostMenu())return;StartControl();}
                control.Pump();if(control.ModeAcknowledged&&reset==null)reset=control.RequestReset();
                if(reset!=null&&control.ResetAcknowledged(reset))renderer=host.source.ConfirmReset();
                if(teachingView!=null&&teachingView.GrammarComplete&&!host.grammarStage.Complete)host.grammarStage.Finish();
                if(ControlReady&&preparedFactory==null&&!AwaitingYokedAnchor&&!AwaitingGrammar){preparedFactory=CreateFactory();scope.Own((IDisposable)preparedFactory);if(kind==JoinedModuleKind.Assessment&&block!="validity")host.ActiveAssessment.ShowInstruction(block);}
                if(Ready)wasReady=true;
            }
            bool ControlReady=>control!=null&&reset!=null&&control.ResetAcknowledged(reset)&&renderer&&host.source.CheckExposureReady()&&host.foundation.Ready&&host.panel.ReadyForTrial&&host.frames.Ready&&(host.store==null||host.store.OldHashesVerified);
            bool AwaitingGrammar=>kind==JoinedModuleKind.Teaching&&host.grammarStage!=null&&!host.grammarStage.Complete;
            bool AwaitingGrammarStart=>AwaitingGrammar&&!host.grammarStage.Started;
            bool AwaitingYokedAnchor=>kind==JoinedModuleKind.Menus&&host.yokedAuthority!=null&&host.yokedAuthority.Replay==null;
            public bool Ready=>!committed&&(preparedFactory!=null||AwaitingYokedAnchor||AwaitingGrammarStart)&&ControlReady&&!(block=="validity"&&host.assets.Schedule.Demo);
            public bool PrepareExplicitResume(OperatorRequest request)
            {
                if(!Ready)throw new SessionFault("JOIN_PREFLIGHT_NOT_READY");
                if(!AwaitingGrammar)return true;
                if(!AwaitingGrammarStart||!host.assets.Route.IsQualified)throw new SessionFault("JOIN_GRAMMAR_NOT_READY");
                host.grammarStage.Begin(request); // consumes exposure before any player setup
                EnsureTeachingView();
                teachingView.BeginGrammar(host.assets.Grammar,host.assets.Route,host.assets.Gain,()=>ControlReady&&control.NeutralHoldHealthy,host.grammarStage.Observe,host.grammarStage.Audio);
                host.Report("JOIN_GRAMMAR_RUNNING");return false;
            }
            void EnsureTeachingView()
            {
                if(teachingView!=null)return;
                var node=new GameObject("Joined Teaching lease");scope.RegisterCleanup(()=>UnityEngine.Object.Destroy(node));
                teachingView=node.AddComponent<TeachingSessionHost>();scope.RegisterCleanup(teachingView.Uninstall);
                teachingView.foundation=host.foundation;teachingView.source=host.source;teachingView.panel=host.panel;teachingView.player=host.player;teachingView.presentationParent=host.foundation.presentationRoot.transform;teachingView.font=host.font;teachingView.Faulted+=host.Fail;
            }
            public ISlotContentFactory Commit(ModuleConstructionScope target)
            {
                if(!ReferenceEquals(target,scope)||!Ready||AwaitingGrammar)throw new SessionFault("JOIN_PREFLIGHT_NOT_READY");
                if(AwaitingYokedAnchor)
                {
                    var request=host.resumeRequest??throw new SessionFault("JOIN_YOKED_OPERATOR_REQUIRED");
                    host.yokedAuthority.BindForExplicitStart(request.RequestId,request.Sequence,request.Command);
                    preparedFactory=CreateFactory();scope.Own((IDisposable)preparedFactory);
                }
                committed=true;
                host.audit.Write("module",new JObject{["kind"]="commit",["block"]=block,["module"]=kind.ToString()});return preparedFactory;
            }
            ISlotContentFactory CreateFactory()
            {
                var shared=host.owner.Shared;
                GameObject go=null;if(kind!=JoinedModuleKind.Teaching){go=new GameObject("Joined "+kind+" lease");scope.RegisterCleanup(()=>UnityEngine.Object.Destroy(go));}
                ISlotContentFactory result;
                if(kind==JoinedModuleKind.Teaching)
                {
                    EnsureTeachingView();var view=teachingView;
                    result=view.Install(host.assets.Teaching,host.selections,new TeachingControl(control),host.assets.Route,host.assets.Gain,host.audit.Lesson,shared.DurableAudioSink,host.owner.Engine.RecordResponse,host.Fail,true,shared.BindAudio);view.BindEngine(host.owner.Engine);
                }
                else if(kind==JoinedModuleKind.Assessment)
                {
                    var screen=go.AddComponent<AssessmentScreen>();screen.foundation=host.foundation;screen.trackingSpace=host.foundation.observerCamera.transform.parent;screen.inputSource=host.panel;screen.font=host.font;screen.unlitShader=host.unlitShader;screen.dictionaryShader=host.dictionaryShader;
                    var view=go.AddComponent<AssessmentSessionHost>();view.foundation=host.foundation;view.source=host.source;view.panel=host.panel;view.player=host.player;view.screen=screen;view.Faulted+=host.Fail;
                    scope.RegisterCleanup(()=>{host.ActiveAssessment=null;view.Uninstall();});
                    result=view.Install(host.assets.Schedule,host.assets.Package,host.clock,host.owner.SessionJournal,shared.StageJournal,control,host.assets.Route,host.assets.Gain,host.assets.Speech,host.selections,host.assets.Scripts,host.assets.RatingsReviewed,shared.DurableAudioSink,true,shared.BindAudio);view.BindEngine(host.owner.Engine);host.ActiveAssessment=view;
                }
                else
                {
                    var view=go.AddComponent<MenuSessionHost>();view.foundation=host.foundation;view.source=host.source;view.panel=host.panel;view.player=host.player;view.presentationParent=host.foundation.presentationRoot.transform;view.font=host.font;view.Faulted+=host.Fail;scope.RegisterCleanup(view.Uninstall);
                    result=view.Install(host.assets.Menus,host.store,control,host.assets.Route,host.assets.Gain,host.menuLedger.Append,shared.DurableAudioSink,host.Fail,replay:host.yokedAuthority?.Replay,engineeringPreview:true,bindAudio:shared.BindAudio);view.BindEngine(host.owner.Engine);
                }
                host.audit.Write("module",new JObject{["kind"]="prepared_view",["block"]=block,["module"]=kind.ToString()});return result;
            }
        }
    }
}
