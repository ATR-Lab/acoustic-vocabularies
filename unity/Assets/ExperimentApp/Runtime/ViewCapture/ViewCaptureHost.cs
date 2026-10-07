using System;
using System.IO;
using System.Text;
using AcousticVocab.Foundation;
using AcousticVocab.ResponsePanel;
using AcousticVocab.StateIntegration;
using AcousticVocab.StateSources;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace AcousticVocab.ViewCapture
{
    // Native entry for the explicitly compiled SIMULATION_TEST view-capture
    // player. It has no session engine, schedule, response handling, demo or
    // participant path. Without its complete pinned arguments it stays idle.
    [DisallowMultipleComponent]
    public sealed class ViewCaptureHost:MonoBehaviour
    {
        public FoundationBootstrap foundation;public StateSourceHost source;public ResponsePanelController panel;
        public string StatusCode{get;private set;}="VIEW_CAPTURE_NOT_STARTED";
        public bool ParticipantAdmission=>false;
        ViewCapturePlan plan;SimulationTestAuthority authority;ViewCaptureSettings settings;PrivateModeResetClient client;ControlJournal journal;
        ViewCaptureDriver driver;LiveAppliedStateReadback live;string endpoint,session,runRoot;long planSeenNs;double modeRequestedAt;bool configured,modeRequested,running,closed,quitOnComplete;
        readonly StopwatchCaptureClock clock=new StopwatchCaptureClock();

        static string Argument(string key){var args=Environment.GetCommandLineArgs();for(int i=0;i<args.Length-1;i++)if(args[i]==key)return args[i+1];return null;}
        static int Integer(string key){string value=Argument(key);ViewCaptureFault.Require(value!=null&&int.TryParse(value,out int n),"VIEW_CAPTURE_ARGUMENTS");return int.Parse(value);}

        void Start()
        {
            string planPath=Argument("-viewCapturePlan"),planPin=Argument("-viewCapturePlanSha256");
            if(planPath==null&&planPin==null){Report("VIEW_CAPTURE_CONFIG_REQUIRED");return;}
            try
            {
                ViewCaptureFault.Require(SimulationTestAuthority.CompiledCapability,"VIEW_CAPTURE_NOT_COMPILED");
                var identity=Resources.Load<TextAsset>("BuildIdentity");ViewCaptureFault.Require(identity!=null,"VIEW_CAPTURE_BUILD_IDENTITY");
                var build=StationConfig.ParseStrict(identity.text);
                string capability=Argument("-simulationTestConfig"),capabilityPin=Argument("-simulationTestConfigSha256");
                ViewCaptureFault.Require(capability!=null&&capabilityPin!=null,"VIEW_CAPTURE_SIMULATION_AUTHORITY_REQUIRED");
                try{authority=SimulationTestAuthority.Load(capability,capabilityPin,(string)build["build_id"],(string)build["protocol_version"]);}
                catch(InvalidDataException){throw new ViewCaptureFault("VIEW_CAPTURE_SIMULATION_AUTHORITY_REQUIRED");}
                plan=ViewCapturePlan.Load(planPath,planPin);planSeenNs=clock.NowNs;
                endpoint=Argument("-viewCaptureControlEndpoint");session=Argument("-viewCaptureControlSession");
                ViewCaptureFault.Require(endpoint!=null&&session!=null,"VIEW_CAPTURE_CONTROL_REQUIRED");
                settings=new ViewCaptureSettings(Integer("-viewCapturePoseSettleFrames"),Integer("-viewCapturePreCueFrames"),Integer("-viewCaptureStepTimeoutMs"),Argument("-viewCaptureRunName"));
                quitOnComplete=Array.IndexOf(Environment.GetCommandLineArgs(),"-viewCaptureQuitOnComplete")>=0;
                ViewCaptureFault.Require(foundation!=null&&source!=null&&panel!=null&&foundation.observerCamera!=null,"VIEW_CAPTURE_SCENE_BINDING");
                configured=true;Report("VIEW_CAPTURE_WAITING_FOUNDATION");
            }
            catch(ViewCaptureFault error){Fail(error.Code);}
            catch(Exception){Fail("VIEW_CAPTURE_CONFIGURATION_INVALID");}
        }

        void Update()
        {
            if(!configured||closed||running)return;
            try
            {
                if(client==null)
                {
                    if(!foundation.Ready||!source.Initialized||panel.State==null||panel.LoadedConfigurationSha256==null)return;
                    ViewCaptureFault.Require(foundation.Configuration!=null&&(string)foundation.Configuration["station_id"]==plan.StationId,"VIEW_CAPTURE_STATION_MISMATCH");
                    ViewCaptureFault.Require((string)foundation.Configuration["robot_state_source"]==plan.SourceKind&&source.Kind==plan.SourceKind,"VIEW_CAPTURE_SOURCE_MISMATCH");
                    ViewCaptureFault.Require(source.LoadedNeutralSha256==plan.SnapshotSha256,"VIEW_CAPTURE_SNAPSHOT_MISMATCH");
                    if(plan.SourceKind=="live")source.EnableSimulationChecks(authority);
                    // The control journal lives beside, not inside, the run
                    // directory so the run itself stays exclusively owned.
                    runRoot=authority.OutputDirectory;
                    journal=new ControlJournal(Path.Combine(runRoot,settings.RunName+"-control.jsonl"));
                    client=new PrivateModeResetClient(endpoint,session,"test",journal.Append);
                    client.RequestMode();modeRequested=true;modeRequestedAt=Time.realtimeSinceStartupAsDouble;Report("VIEW_CAPTURE_WAITING_TEST_MODE");return;
                }
                if(modeRequested&&!client.ModeAcknowledged)
                {
                    client.Pump();
                    ViewCaptureFault.Require(Time.realtimeSinceStartupAsDouble-modeRequestedAt<=15,"VIEW_CAPTURE_TEST_MODE_TIMEOUT");
                    if(!client.ModeAcknowledged)return;
                }
                IStateReadback state;
                if(plan.SourceKind=="snapshot")
                {
                    var setup=StateSourceConfiguration.Load(new UTF8Encoding(false,true).GetString(File.ReadAllBytes(Path.Combine(Application.persistentDataPath,"state-source.local.json"))));
                    state=new SnapshotStateReadback(source,setup.LoadNeutralBytes(Application.persistentDataPath),plan.SnapshotSha256,clock);
                }
                else state=live=new LiveAppliedStateReadback(source);
                var camera=foundation.observerCamera;
                var ports=new ViewCapturePorts{Clock=clock,Control=new PrivateControlPort(client,session),Rig=new TransformHeadRig(camera,plan.CaptureSurface=="desktop_engineering"),
                    Grabber=new CameraFrameGrabber(camera),State=state,Audio=new AudioSourceRouting(),View=new SceneViewInventory(camera,panel),Boundary=new DriverOnlyTrialBoundary(clock)};
                driver=new ViewCaptureDriver(plan,authority,ports,settings,planSeenNs);driver.Start();
                running=true;Report("VIEW_CAPTURE_RUNNING");StartCoroutine(Drive());
            }
            catch(ViewCaptureFault error){Fail(error.Code);}
            catch(ControlFault error){Fail(error.Code);}
            catch(StateFault){Fail("VIEW_CAPTURE_STATE_SOURCE");}
            catch(Exception){Fail("VIEW_CAPTURE_RUNTIME_FAILED");}
        }

        System.Collections.IEnumerator Drive()
        {
            yield return driver.Run();
            running=false;Report(driver.Complete?"VIEW_CAPTURE_COMPLETE":driver.Fault??"VIEW_CAPTURE_INCOMPLETE");Close();
            if(quitOnComplete)Application.Quit(driver.Complete?0:1);
        }

        void Report(string code)
        {
            if(StatusCode==code)return;StatusCode=code;
            Debug.Log("VIEW_CAPTURE_STATUS "+code+" captures="+(driver?.CapturesCompleted??0)+" participant_admission=false");
        }
        void Fail(string code)
        {
            if(closed)return;driver?.Abort(code);Report(driver?.Fault??code);Close();
            if(quitOnComplete)Application.Quit(1);
        }
        // Releases the private control session so the separate lock probe
        // can run. Never sends stop/hold or any other mutation on the way out.
        void Close()
        {
            if(closed)return;closed=true;
            try{live?.Dispose();}catch(Exception){}
            try{client?.Dispose();}catch(Exception){}
            try{journal?.Dispose();}catch(Exception){}
        }
        void OnDisable(){if(configured&&!closed)Fail("VIEW_CAPTURE_HOST_DISABLED");}
        void OnApplicationQuit(){if(configured&&!closed)Fail("VIEW_CAPTURE_APPLICATION_QUIT");}

        // Durable, create-new JSONL sink for the private control requests and
        // replies. A failed write propagates into the control client.
        sealed class ControlJournal:IDisposable
        {
            readonly FileStream stream;
            internal ControlJournal(string path){CaptureJson.NoLinks(path);stream=new FileStream(path,FileMode.CreateNew,FileAccess.Write,FileShare.Read);}
            internal void Append(JObject value){byte[] line=Encoding.UTF8.GetBytes(value.ToString(Newtonsoft.Json.Formatting.None)+"\n");stream.Write(line,0,line.Length);stream.Flush(true);}
            public void Dispose()=>stream.Dispose();
        }
    }
}
