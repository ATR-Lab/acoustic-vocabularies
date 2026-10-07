using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using AcousticVocab.DataLogging;
using AcousticVocab.ResponsePanel;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using AcousticVocab.Teaching;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.SelectionMenus.Tests
{
    // SYNTHETIC ENGINE-PRODUCED HISTORIES (#78 AC3/AC4 evidence, not closure).
    // Whole producer visit schedules run through the real FixedSlotEngine,
    // LessonTimeline/LessonDataJournal, MenuCatalog/MenuTimeline/MenuLedger/
    // MenuReplaySequence, DataJournal, deriver and export on a virtual clock.
    // Thin adapters replace MonoBehaviour hosts (display, XR input, AudioPlayer,
    // private backend, #11 store). No audio callback is recorded: every request
    // stays uncertain/consumed. Lesson and menu timelines receive virtual-clock
    // software onsets at their nominal times so they can run; that is not an
    // acoustic or device claim. Menu selection receipts are synthetic (no store).
    // Validity blocks are omitted: they need a reviewed or simulation speech bank.
    public sealed class EngineHistoryExportTests
    {
        const double Step=50,Uncertainty=10;
        static readonly string Hash=new string('a',64);
        static readonly DateTimeOffset Created=new DateTimeOffset(2026,1,1,0,0,0,TimeSpan.Zero);
        static T New<T>(params object[] args)=>(T)Activator.CreateInstance(typeof(T),BindingFlags.Instance|BindingFlags.NonPublic|BindingFlags.Public,null,args,null);
        static object Member(object owner,string name)=>owner.GetType().GetField(name,BindingFlags.Instance|BindingFlags.NonPublic|BindingFlags.Public).GetValue(owner);
        static void Write(string path,JObject value)=>File.WriteAllText(path,value.ToString()+"\n",new UTF8Encoding(false));
        static string Sha(string text)=>PcmWave.Hash(Encoding.UTF8.GetBytes(text));

        sealed class Clock:ISessionClock{public double Time;public double NowMs=>Time;}
        sealed class Selections:ITeachingSelections
        {
            readonly Dictionary<string,TeachingSelection> map=new Dictionary<string,TeachingSelection>(StringComparer.Ordinal);
            public string PackageSha256{get;}public bool OldHashesVerified=>true;public string Profile;
            public Selections(string package){PackageSha256=package;}
            public TeachingSelection Get(string atom)=>map[atom];
            public void Set(string atom,int rank)=>map[atom]=new TeachingSelection(Profile,rank);
        }
        sealed class Fixture
        {
            internal string Root,Output,Examples;internal JObject Index;internal byte[] Allocation,Registry;internal string AllocationSha,RegistrySha;
            internal readonly Dictionary<string,(LoadedAudioPackage package,string dir,byte[] manifest,byte[] permutation)> Packages=new Dictionary<string,(LoadedAudioPackage,string,byte[],byte[])>();
        }
        Fixture Load(string study)
        {
            string index=Environment.GetEnvironmentVariable("AV_ENGINE_HISTORY_FIXTURES");
            if(string.IsNullOrEmpty(index))Assert.Ignore("Run tools/prepare_engine_histories.py and supply pinned AV_ENGINE_HISTORY_FIXTURES (synthetic DEMO only)");
            byte[] bytes=File.ReadAllBytes(index);Assert.That(PcmWave.Hash(bytes),Is.EqualTo(Environment.GetEnvironmentVariable("AV_ENGINE_HISTORY_FIXTURES_SHA256")));
            var f=new Fixture{Root=Path.GetDirectoryName(index),Index=JObject.Parse(Encoding.UTF8.GetString(bytes))};
            Assert.That((string)f.Index["scope"],Is.EqualTo("DEMO_ENGINE_HISTORY"));Assert.That((bool)f.Index["synthetic"],Is.True);
            f.Output=Environment.GetEnvironmentVariable("AV_ENGINE_HISTORY_RESULTS");Assert.That(f.Output,Is.Not.Null.And.Not.Empty);
            var dir=new DirectoryInfo(Directory.GetCurrentDirectory());while(dir!=null&&!Directory.Exists(Path.Combine(dir.FullName,"sound","reserved")))dir=dir.Parent;Assert.That(dir,Is.Not.Null);
            f.Allocation=File.ReadAllBytes(Path.Combine(dir.FullName,(string)f.Index["allocation"]["path"]));f.AllocationSha=PcmWave.Hash(f.Allocation);Assert.That(f.AllocationSha,Is.EqualTo((string)f.Index["allocation"]["sha256"]));
            f.Registry=File.ReadAllBytes(Path.Combine(dir.FullName,(string)f.Index["registry"]["path"]));f.RegistrySha=PcmWave.Hash(f.Registry);Assert.That(f.RegistrySha,Is.EqualTo((string)f.Index["registry"]["sha256"]));
            f.Examples=Path.Combine(f.Root,(string)f.Index["examples"]);
            foreach(var p in ((JObject)f.Index["packages"]).Properties().Where(x=>x.Name==study))
            {
                string root=Path.Combine(f.Root,(string)p.Value["directory"]);var package=PackageLoader.Load(root,(string)p.Value["package_sha256"],true);Assert.That(package.Demo,Is.True);
                f.Packages.Add(p.Name,(package,root,File.ReadAllBytes(Path.Combine(root,"manifest.json")),File.ReadAllBytes(Path.Combine(root,"permutation.json"))));
            }
            return f;
        }

        // Synthetic private assets (as the MenuCatalog/TeachingCatalog tests): no study wording.
        static (string dir,string sha,string review) TeachingAssets(string root,LoadedAudioPackage package,JObject permutation)
        {
            string dir=Path.Combine(root,"teaching");if(Directory.Exists(dir))return (dir,PcmWave.Hash(File.ReadAllBytes(Path.Combine(dir,"catalog.local.json"))),PcmWave.Hash(File.ReadAllBytes(Path.Combine(dir,"review.local.json"))));
            Directory.CreateDirectory(Path.Combine(dir,"images"));byte[] png=Convert.FromBase64String("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7V8AAAAASUVORK5CYII=");File.WriteAllBytes(Path.Combine(dir,"images","fixture.png"),png);
            var ids=permutation["atoms"].Select(x=>(string)x["atom_id"]).Concat(permutation["messages"].Where(x=>(string)x["status"]=="trained").Select(x=>(string)x["message_id"]));
            var catalog=new JObject{["format"]="av-teaching/1",["package_sha256"]=package.PackageSha256,["content"]=new JArray(ids.Select(id=>new JObject{["content_id"]=id,["meaning_display_id"]="DEMO-display-"+id,["definition"]="Synthetic definition",["action_words"]="Synthetic action",["target_words"]="Synthetic target",["image_id"]="fixture"})),["images"]=new JObject{["fixture"]=new JObject{["file"]="images/fixture.png",["sha256"]=PcmWave.Hash(png)}},["feedback"]=new JObject()};
            foreach(string kind in new[]{"atomic","correct","incorrect","timeout"})catalog["feedback"][kind]=new JObject{["id"]="DEMO-"+kind,["text"]="Synthetic feedback"};
            Write(Path.Combine(dir,"catalog.local.json"),catalog);string sha=PcmWave.Hash(File.ReadAllBytes(Path.Combine(dir,"catalog.local.json")));
            Write(Path.Combine(dir,"review.local.json"),new JObject{["version"]=1,["approved"]=true,["catalog_sha256"]=sha,["methodology_sha256"]=Hash});
            return (dir,sha,PcmWave.Hash(File.ReadAllBytes(Path.Combine(dir,"review.local.json"))));
        }
        static (string dir,string sha,string review) MenuAssets(string root,LoadedAudioPackage package,string teachingReview)
        {
            string dir=Path.Combine(root,"menu");Directory.CreateDirectory(dir);
            Write(Path.Combine(dir,"menu-script.local.json"),new JObject{["format"]="av-menu-script/1",["package_sha256"]=package.PackageSha256,["profile_display_id"]="DEMO-profile-display",["profile_names"]=new JObject{["P1"]="DEMO profile 1",["P2"]="DEMO profile 2",["P3"]="DEMO profile 3"},["profile_instructions"]="Synthetic profile instructions",["atom_instructions"]="Synthetic atom instructions",["active_choice_instructions"]="Synthetic choose wording",["yoked_choice_instructions"]="Synthetic assigned wording",["candidate_labels"]=new JArray("DEMO option 1","DEMO option 2","DEMO option 3")});
            string sha=PcmWave.Hash(File.ReadAllBytes(Path.Combine(dir,"menu-script.local.json")));
            Write(Path.Combine(dir,"review.local.json"),new JObject{["version"]=1,["approved"]=true,["script_sha256"]=sha,["teaching_review_sha256"]=teachingReview,["methodology_sha256"]=Hash});
            return (dir,sha,PcmWave.Hash(File.ReadAllBytes(Path.Combine(dir,"review.local.json"))));
        }

        // One visit: engine, router, adapters and journals.
        sealed class Visit:ISlotContentFactory,ISessionContentPump,ISlotStartPlan,IDisposable
        {
            internal readonly Clock Clock;internal readonly LoadedAudioPackage Package;internal readonly Selections Selections;internal readonly VisitSchedule Schedule;
            internal readonly TeachingCatalog Teaching;internal readonly MenuCatalog Menus;internal readonly LessonDataJournal Lessons;
            internal readonly AudioDataAdapter Audio;internal readonly PanelDataAdapter Panel;internal readonly Action<MenuEvent> MenuSink;
            internal FixedSlotEngine Engine;internal MenuReplaySequence Replay;internal Exception Error;internal int MenuIndex;
            internal readonly Dictionary<string,MenuOption[]> Options=new Dictionary<string,MenuOption[]>(StringComparer.Ordinal);
            internal readonly Dictionary<string,string> Meanings=new Dictionary<string,string>(StringComparer.Ordinal);
            internal readonly Dictionary<string,string> Receipts=new Dictionary<string,string>(StringComparer.Ordinal);
            readonly Dictionary<string,AudioRequestContext> bindings=new Dictionary<string,AudioRequestContext>(StringComparer.Ordinal);
            readonly List<Slot> live=new List<Slot>();readonly GameObject host;
            internal Visit(Clock clock,LoadedAudioPackage package,Selections selections,VisitSchedule schedule,TeachingCatalog teaching,MenuCatalog menus,DataJournal journal,Action<MenuEvent> menuSink)
            {
                Clock=clock;Package=package;Selections=selections;Schedule=schedule;Teaching=teaching;Menus=menus;MenuSink=menuSink;Lessons=new LessonDataJournal(journal,schedule);
                host=new GameObject("DEMO engine history inactive adapter host");host.SetActive(false);
                Audio=new AudioDataAdapter(journal,host.AddComponent<AudioPlayer>(),e=>bindings[e.AudioId],false);
                Panel=new PanelDataAdapter(journal,host.AddComponent<ResponsePanelController>(),id=>new EventContext(id,id));
            }
            public ISlotContent Create(SlotItem item){Slot slot=item.Phase=="selection"?new MenuSlot(this,item):item.Phase=="teaching"?new LessonSlot(this,item):new AssessmentSlot(this);live.Add(slot);return slot;}
            public void Pump()
            {
                try{foreach(var slot in live.ToArray())slot.Tick(Clock.Time);live.RemoveAll(x=>x.Retired);}
                catch(Exception e){Error??=e;throw;}
            }
            public double MinimumGapBeforeMs(SlotItem item,double baseline)=>item.Phase=="selection"&&Replay!=null?Replay.MinimumGapBeforeMs(item,baseline):0;
            internal void Request(SlotContext context,int index,PcmWave wave,double at)
            {
                // AUDIO_REQUESTED only, with the simulation software-output estimate
                // at the nominal onset. No callback/observation is ever written.
                string id=context.AudioRequestIds[index];double now=Clock.Time;
                bindings.Add(id,new AudioRequestContext(new EventContext(context.OpportunityId,context.Item.TrialId,id),id,wave.FileSha256));
                var timing=New<AudioScheduleTiming>(now/1000,at/1000,at/1000,(double?)null,(double?)null,(double?)null,(double?)(at/1000),(double?)Uncertainty);
                Audio.Record(New<AudioPlaybackEvent>("AUDIO_REQUESTED",id,wave.PcmSha256,wave.ActionPcmSha256,wave.ReferentPcmSha256,timing,now/1000,0L,0L,(double?)null));
            }
            internal void Respond(string code){if(Engine.CurrentState==ItemState.ResponseOpen)Engine.RecordResponse(code);}
            public void Dispose(){Panel.Dispose();Audio.Dispose();UnityEngine.Object.DestroyImmediate(host);}
        }
        abstract class Slot:ISlotContent
        {
            protected readonly Visit V;protected SlotContext Context;protected bool ResetWanted;
            readonly List<(string id,double at,double end,Action<string,double> onset,Action<string> done)> plays=new List<(string,double,double,Action<string,double>,Action<string>)>();
            readonly HashSet<string> started=new HashSet<string>(),finished=new HashSet<string>();
            protected Slot(Visit visit){V=visit;}
            public SlotReadiness Readiness=>new SlotReadiness(true,true,true,true,true,true,true,true);
            public virtual bool ResetComplete=>ResetWanted;
            public virtual void Prepare(SlotContext context){Context=context;}
            public abstract void RequestCue(SlotContext context,INovelSlotAuthorization permit);
            public virtual void OpenResponse(SlotContext context){}
            public virtual void CloseResponse(SlotContext context){}
            public virtual void RequestReset(SlotContext context){ResetWanted=true;}
            public virtual void Interrupt(string code){}
            internal abstract bool Retired{get;}
            protected bool Playing=>plays.Any(p=>!finished.Contains(p.id));
            // Virtual-clock software onset/completion for the timelines only.
            protected void Track(string id,double at,PcmWave wave,Action<string,double> onset,Action<string> done)=>plays.Add((id,at,at+wave.SampleCount/48d,onset,done));
            internal virtual void Tick(double now)
            {
                foreach(var p in plays)
                {
                    if(now>=p.at&&started.Add(p.id))p.onset(p.id,now);
                    if(now>=p.end&&started.Contains(p.id)&&finished.Add(p.id))p.done(p.id);
                }
            }
        }
        sealed class LessonSlot:Slot
        {
            readonly SlotItem item;PcmWave wave,action,referent;bool atomic,aligned;string display;LessonTimeline timeline;ResponseState state;
            internal LessonSlot(Visit visit,SlotItem item):base(visit){this.item=item;}
            public override void Prepare(SlotContext context)
            {
                base.Prepare(context);V.Lessons.Bind(context);
                // The catalog's own lesson material (internal to Teaching): same audio as production.
                var material=typeof(TeachingCatalog).GetMethod("Prepare",BindingFlags.Instance|BindingFlags.NonPublic,null,new[]{typeof(SlotItem),typeof(ITeachingSelections)},null).Invoke(V.Teaching,new object[]{item,V.Selections});
                wave=(PcmWave)Member(material,"Wave");action=(PcmWave)Member(material,"Action");referent=(PcmWave)Member(material,"Referent");atomic=(bool)Member(material,"Atomic");aligned=(bool)Member(material,"Aligned");
                display=((TeachingDisplay)Member(material,"Display")).MeaningDisplayId;
                timeline=new LessonTimeline(context,aligned,action.SampleCount,referent?.SampleCount??0,display,wave.PcmSha256,atomic?null:action.PcmSha256,referent?.PcmSha256,V.Lessons.Append,()=>V.Clock.Time);
                timeline.PlayRequested+=(index,id,expected)=>{V.Request(Context,index-1,wave,expected);Track(id,expected,wave,(x,now)=>timeline.Onset(x,expected,Uncertainty,now),x=>timeline.Completed(x,V.Clock.Time));};
            }
            public override void RequestCue(SlotContext context,INovelSlotAuthorization permit){Assert.That(permit,Is.Null);timeline.Start(V.Clock.Time);}
            public override void OpenResponse(SlotContext context)
            {
                // Retrieval is left to time out: feedback and engine response follow production order.
                state=new ResponseState(()=>V.Clock.Time,V.Panel.Process);
                state.Responded+=r=>{V.Panel.Response(r);timeline.Response(atomic?"DEMO-atomic":"DEMO-timeout",r.ResponseMonoMs,r.Code==ResponseCode.Timeout);V.Respond("timeout");};
                state.Open(new PanelRequest(item.TrialId,atomic?PanelMode.LessonAtomic:PanelMode.LessonMessage,atomic?(item.Role=="action"?PanelRole.Action:PanelRole.Target):PanelRole.Command,context.OnsetMonoMs));
            }
            public override void RequestReset(SlotContext context){base.RequestReset(context);timeline.RequestReset();}
            public override bool ResetComplete=>ResetWanted&&timeline.ResetMayBegin&&!Playing;
            internal override bool Retired=>timeline!=null&&timeline.Ended;
            internal override void Tick(double now){if(timeline==null)return;base.Tick(now);state?.Tick();timeline.Tick(now);}
            public override void Interrupt(string code){timeline?.Interrupt(V.Clock.Time);}
        }
        sealed class MenuSlot:Slot
        {
            readonly SlotItem item;MenuMaterial material;MenuTimeline timeline;int? selected,choice;string receipt;bool chosen;
            internal MenuSlot(Visit visit,SlotItem item):base(visit){this.item=item;}
            public override void Prepare(SlotContext context)
            {
                base.Prepare(context);bool profile=item.TrialType=="profile_menu";material=V.Menus.Prepare(item,V.Selections.Profile);
                V.Options[material.Key]=material.Options.ToArray();V.Meanings[material.Key]=material.MeaningDisplayId;
                var replay=V.Replay?.For(item);int index=V.MenuIndex++;
                // Active choices vary (one menu in four keeps the stored default).
                if(replay==null)choice=profile?2:index%4==3?(int?)null:index%3+1;
                timeline=new MenuTimeline(context,material.Options.ToArray(),material.MeaningDisplayId,V.MenuSink,replay,()=>V.Clock.Time);
                timeline.PlayRequested+=(n,id,option,at)=>{V.Request(Context,n-1,option.Wave,at);Track(id,at,option.Wave,(x,now)=>timeline.Onset(x,at,Uncertainty,now),x=>timeline.Completed(x,V.Clock.Time));};
                timeline.SelectionRequested+=(n,unused)=>{selected=n;receipt=Sha("DEMO-synthetic-store-receipt|"+V.Schedule.Visit+"|"+material.Key+"|"+n);V.Receipts[material.Key+"|"+n]=receipt;Commit(n);};
                if(replay!=null){V.Receipts[material.Key+"|"+replay.SelectedIndex]=replay.SelectionReceiptSha256;Commit(replay.SelectedIndex);}
            }
            void Commit(int index){if(material.Key=="profile")V.Selections.Profile=V.Menus.ProfileAt(index);else V.Selections.Set(material.Key,index);}
            public override void RequestCue(SlotContext context,INovelSlotAuthorization permit){Assert.That(permit,Is.Null);timeline.Start(V.Clock.Time);}
            public override bool ResetComplete=>ResetWanted&&(timeline.Phase==MenuPhase.Neutral||timeline.Phase==MenuPhase.Ended)&&!Playing;
            internal override bool Retired=>timeline!=null&&timeline.Phase==MenuPhase.Ended;
            internal override void Tick(double now)
            {
                if(timeline==null)return;base.Tick(now);
                if(receipt!=null&&selected.HasValue){timeline.ConfirmSelection(selected.Value,receipt,now);selected=null;}
                if(choice.HasValue&&!chosen&&timeline.Phase==MenuPhase.Choice&&now-Context.OnsetMonoMs>=item.ChoiceOpensSeconds*1000+1000){timeline.Choose(choice.Value,now);chosen=true;}
                timeline.Tick(now);
            }
            public override void Interrupt(string code){timeline?.Interrupt(V.Clock.Time);}
        }
        sealed class AssessmentSlot:Slot
        {
            ResponseState state;bool answered;
            internal AssessmentSlot(Visit visit):base(visit){}
            (string profile,int a,int r) Ranks(string message)
            {
                if(V.Package.Study=="A")return (null,0,0);
                string action=message.Substring(0,4),referent=message[0]+"-"+message.Substring(5);
                return (V.Selections.Profile,V.Selections.Get(action).Rank,V.Selections.Get(referent).Rank);
            }
            public override void RequestCue(SlotContext context,INovelSlotAuthorization permit)
            {
                var item=context.Item;if(item.Plays==0)return;PcmWave wave;
                if(item.TrialType=="atomic")wave=V.Package.Study=="A"?V.Package.ReadAtom(item.ContentId):V.Package.ReadAtom(item.ContentId,V.Selections.Profile,V.Selections.Get(item.ContentId).Rank);
                else{var s=Ranks(item.ContentId);wave=item.Heldout?V.Package.ComposeApprovedNovel(item.ContentId,permit,s.profile,s.a,s.r):V.Package.ReadTrainedMessage(item.ContentId,s.profile,s.a,s.r);}
                V.Request(context,0,wave,context.OnsetMonoMs);
            }
            public override void OpenResponse(SlotContext context)
            {
                var item=context.Item;state=new ResponseState(()=>V.Clock.Time,V.Panel.Process);
                state.Responded+=r=>{V.Panel.Response(r);V.Respond(r.Code==ResponseCode.DontKnow?"dont_know":"timeout");};
                state.Open(new PanelRequest(item.TrialId,item.TrialType=="atomic"?PanelMode.AtomicProbe:PanelMode.FullMessage,item.TrialType=="atomic"?(item.Role=="action"?PanelRole.Action:PanelRole.Target):PanelRole.Command,context.OnsetMonoMs));
            }
            internal override bool Retired=>ResetWanted;
            internal override void Tick(double now){state?.Tick();if(state!=null&&!answered&&state.WindowOpen)answered=state.DontKnow();}
            public override void Interrupt(string code){state?.Abort();}
        }

        // Runs one visit to completion; returns its index entry.
        JObject RunVisit(Fixture f,string study,string person,string visitId,string role,Selections selections,Clock clock,string unitBinding,ref (MenuLedgerBinding binding,string path,string sha,Dictionary<string,MenuOption[]> options,Dictionary<string,string> meanings,Dictionary<string,string> receipts)? active)
        {
            var (package,root,manifest,permutationBytes)=f.Packages[study];var permutation=JObject.Parse(Encoding.UTF8.GetString(permutationBytes));
            string scheduleRel="schedules/"+person+"/"+visitId+".json";byte[] scheduleBytes=File.ReadAllBytes(Path.Combine(root,scheduleRel));var doc=JObject.Parse(Encoding.UTF8.GetString(scheduleBytes));
            var blocks=ScheduleLoader.ValidateBlocks(doc,permutation).Where(b=>b.Name!="validity").ToArray();
            var schedule=new VisitSchedule(PcmWave.Hash(scheduleBytes),package.PackageSha256,person,visitId,true,blocks,study,(string)doc["set"],null);
            string run=Path.Combine(f.Output,study,person,visitId);Assert.That(Directory.Exists(run),Is.False,"Use a fresh output root");Directory.CreateDirectory(run);
            bool teaching=blocks.Any(b=>b.Items.Any(i=>i.Phase=="teaching")),menus=blocks.Any(b=>b.Items.Any(i=>i.Phase=="selection"));
            TeachingCatalog catalog=null;MenuCatalog menuCatalog=null;
            if(teaching)
            {
                var t=TeachingAssets(Path.Combine(f.Output,study,"assets"),package,permutation);
                catalog=study=="B"?TeachingCatalog.Load(t.dir,t.sha,t.review,package,schedule,manifest,permutationBytes,f.Allocation,f.AllocationSha):TeachingCatalog.Load(t.dir,t.sha,t.review,package,schedule,manifest,permutationBytes);
                if(menus)
                {
                    var m=MenuAssets(Path.Combine(run,"assets"),package,t.review);
                    menuCatalog=MenuCatalog.Load(m.dir,m.sha,m.review,package,schedule,catalog,manifest,permutationBytes,f.Allocation,f.AllocationSha,(string)JObject.Parse(Encoding.UTF8.GetString(manifest))["bank"]["bank_sha256"],f.Examples,f.Registry,f.RegistrySha,true);
                    Assert.That(menuCatalog.Role,Is.EqualTo(role));
                }
            }
            MenuLedger ledger=null;string ledgerPath=null;
            if(menuCatalog!=null){ledgerPath=Path.Combine(run,"menus.local.jsonl");ledger=new MenuLedger(ledgerPath,menuCatalog.Binding(unitBinding),Created);}
            var identity=new DataIdentity(Guid.NewGuid().ToString("N"),person,visitId,"DEMO-STATION","DEMO-protocol",Hash);string raw=Path.Combine(run,"raw");
            using(var journal=new DataJournal(raw,identity,Guid.NewGuid().ToString("N"),()=>clock.Time))
            using(var visit=new Visit(clock,package,selections,schedule,catalog,menuCatalog,journal,e=>ledger.Append(e)))
            {
                foreach(var item in blocks.SelectMany(b=>b.Items))journal.Append(DataObservations.Opportunity(item.TrialId,"primary","DEMO-masked",schedule.Sha256));
                var engine=new FixedSlotEngine(schedule,clock,new SessionDataJournal(journal),visit);visit.Engine=engine;int block=0;double limit=clock.Time+4*3600*1000;
                while(engine.Status!=SessionState.Complete)
                {
                    Assert.That(engine.Status!=SessionState.Faulted&&engine.PrimaryFaultCode==null,Is.True,()=>"engine fault "+engine.PrimaryFaultCode+" at "+person+" "+visitId+" t="+clock.Time+": "+visit.Error);
                    if(engine.NeedsOperatorConfirmation)
                    {
                        if(role=="yoked"&&visit.Replay==null&&blocks[block].Items.Any(i=>i.Phase=="selection"))
                        {
                            var a=active.Value;var verification=new MenuLedgerVerification(k=>a.options[k],(k,i,h)=>a.receipts.TryGetValue(k+"|"+i,out string x)&&x==h,k=>a.meanings[k]);
                            visit.Replay=MenuReplaySequence.Load(a.path,a.sha,a.binding,verification,Created.AddHours(1),clock.Time+750+1000,()=>clock.Time);
                        }
                        engine.ConfirmResume();block++;
                    }
                    clock.Time+=Step;engine.Tick();Assert.That(clock.Time,Is.LessThan(limit));
                }
                // Let retired lesson/menu tails finish on the same clock.
                for(int i=0;i<1200;i++){clock.Time+=Step;visit.Pump();}
                if(ledger!=null)
                {
                    var verification=new MenuLedgerVerification(k=>visit.Options[k],(k,i,h)=>visit.Receipts.TryGetValue(k+"|"+i,out string x)&&x==h,k=>visit.Meanings[k]);
                    if(role=="active"){ledger.Seal(verification);ledger.Dispose();string sha=PcmWave.Hash(File.ReadAllBytes(ledgerPath));active=(menuCatalog.Binding(unitBinding),ledgerPath,sha,new Dictionary<string,MenuOption[]>(visit.Options),new Dictionary<string,string>(visit.Meanings),new Dictionary<string,string>(visit.Receipts));}
                    else
                    {
                        var comparison=ledger.SealYoked(visit.Replay,verification);ledger.Dispose();
                        Assert.That(comparison.AudioPlays,Is.EqualTo(8*menuCatalog.MenuKeys.Count));
                    }
                }
            }
            clock.Time+=60000; // inter-visit gap on one virtual clock (no UTC claim)
            var snapshot=DataJournal.Verify(raw,identity);var tables=DataDeriver.Derive(snapshot,identity);derived[person+"/"+visitId]=tables;
            Assert.That(tables.Exposures.All(r=>r["callback_observed"]=="false"&&r["audible_status"]=="uncertain"&&r["exposure_consumed"]=="true"),Is.True);
            var bundle=ExportBundle.Create(raw,Path.Combine(run,"export"),identity,ExportHeaders.Provisional());
            string Rel(string path)=>path==null?null:Path.GetRelativePath(f.Output,path).Replace('\\','/');
            return new JObject{["study"]=study,["person_id"]=person,["role"]=role,["visit"]=visitId,["schedule"]=scheduleRel,["schedule_sha256"]=schedule.Sha256,
                ["omitted_blocks"]=new JArray(doc["blocks"].Select(b=>(string)b["block"]).Where(b=>blocks.All(x=>x.Name!=b))),
                ["export_manifest"]=Rel(Path.Combine(run,"export","manifest.json")),["export_manifest_sha256"]=bundle.ManifestSha256,
                ["menu_ledger"]=Rel(ledgerPath),["menu_ledger_sha256"]=ledgerPath==null?null:PcmWave.Hash(File.ReadAllBytes(ledgerPath)),
                ["exposure_rows"]=tables.Exposures.Count,["trial_rows"]=tables.Trials.Count};
        }
        static int Plays(DerivedTables tables,string code)=>tables.Exposures.Count(r=>r["attempt_id"].Contains("-"+code+"-"));
        void Finish(Fixture f,string study,JArray visits)
        {
            var index=new JObject{["scope"]="SYNTHETIC_ENGINE_HISTORY",["synthetic"]=true,["acoustic_evidence"]=false,["callbacks_recorded"]=false,["participant_ready"]=false,
                ["fixtures_sha256"]=Environment.GetEnvironmentVariable("AV_ENGINE_HISTORY_FIXTURES_SHA256"),["package_directory"]=(string)f.Index["packages"][study]["directory"],
                ["package_sha256"]=(string)f.Index["packages"][study]["package_sha256"],["visits"]=visits};
            byte[] bytes=Encoding.UTF8.GetBytes(index.ToString()+"\n");string path=Path.Combine(f.Output,"histories-"+study+".local.json");
            Assert.That(File.Exists(path),Is.False);File.WriteAllBytes(path,bytes);File.WriteAllText(path+".sha256",PcmWave.Hash(bytes)+"\n",new UTF8Encoding(false));
        }
        readonly Dictionary<string,DerivedTables> derived=new Dictionary<string,DerivedTables>(StringComparer.Ordinal);

        [Test]public void StudyALearnerD0AndD7RunThroughEngineLessonsAndJournal()
        {
            var f=Load("A");string person=(string)f.Index["study_a_person"];var clock=new Clock();var selections=new Selections(f.Packages["A"].package.PackageSha256);
            (MenuLedgerBinding,string,string,Dictionary<string,MenuOption[]>,Dictionary<string,string>,Dictionary<string,string>)? none=null;var visits=new JArray();
            foreach(string v in new[]{"D0","D7"})visits.Add(RunVisit(f,"A",person,v,"reference",selections,clock,Hash,ref none));
            var d0=derived[person+"/D0"];
            Assert.That(Plays(d0,"AL"),Is.EqualTo(48));Assert.That(Plays(d0,"ML"),Is.EqualTo(108));
            Finish(f,"A",visits);
        }
        [Test]public void StudyBActiveAndYokedV1ToW4RunThroughEngineMenusLessonsAndJournal()
        {
            var f=Load("B");var roles=(JObject)f.Index["study_b_roles"];string activePerson=roles.Properties().Single(p=>(string)p.Value=="active").Name,yokedPerson=roles.Properties().Single(p=>(string)p.Value=="yoked").Name;
            var clock=new Clock();string package=f.Packages["B"].package.PackageSha256;var activeSel=new Selections(package);var yokedSel=new Selections(package);
            string unit=Sha("DEMO-engine-history-unit|"+(string)f.Index["packages"]["B"]["unit_id"]);var visits=new JArray();
            foreach(string v in new[]{"V1","V2","V3","W1","W4"})
            {
                (MenuLedgerBinding,string,string,Dictionary<string,MenuOption[]>,Dictionary<string,string>,Dictionary<string,string>)? active=null;
                visits.Add(RunVisit(f,"B",activePerson,v,"active",activeSel,clock,unit,ref active));
                visits.Add(RunVisit(f,"B",yokedPerson,v,"yoked",yokedSel,clock,unit,ref active));
            }
            foreach(string person in new[]{activePerson,yokedPerson})
            {
                var mine=new[]{"V1","V2","V3"}.Select(v=>derived[person+"/"+v]).ToArray();
                Assert.That(mine.Sum(t=>Plays(t,"PM")),Is.EqualTo(8),person);Assert.That(mine.Sum(t=>Plays(t,"AM")),Is.EqualTo(128),person);
            }
            Finish(f,"B",visits);
        }
    }
}
