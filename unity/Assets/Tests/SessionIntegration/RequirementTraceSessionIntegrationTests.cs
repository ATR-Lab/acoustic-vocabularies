using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using AcousticVocab.SessionEngine;
using NUnit.Framework;

namespace AcousticVocab.SessionIntegration.Tests
{
    // Requirement trace checks over the compiled runtime assemblies. These read
    // method bodies only; no scene, device or participant file is opened.
    public sealed class RequirementTraceSessionIntegrationTests
    {
        static IEnumerable<Assembly> RuntimeAssemblies() => AppDomain.CurrentDomain.GetAssemblies().Where(a=>
        {
            string name=a.GetName().Name;
            return name.StartsWith("AcousticVocab.",StringComparison.Ordinal) && !name.Contains(".Tests") && !name.Contains("PlayModeTests") && !name.EndsWith(".Editor",StringComparison.Ordinal);
        });
        static IEnumerable<Type> Types(Assembly assembly)
        {
            try { return assembly.GetTypes(); }
            catch(ReflectionTypeLoadException error) { return error.Types.Where(t=>t!=null); }
        }
        const BindingFlags Declared=BindingFlags.DeclaredOnly|BindingFlags.Instance|BindingFlags.Static|BindingFlags.Public|BindingFlags.NonPublic;

        // Methods (including compiler-generated lambda bodies) whose IL calls,
        // or creates a delegate for, the target method.
        static List<string> Callers(MethodInfo target)
        {
            var found=new List<string>();
            foreach(var assembly in RuntimeAssemblies()) foreach(var type in Types(assembly))
                foreach(var method in type.GetMethods(Declared).Cast<MethodBase>().Concat(type.GetConstructors(Declared)))
                {
                    byte[] il;try { il=method.GetMethodBody()?.GetILAsByteArray(); } catch { continue; }
                    if(il==null) continue;
                    for(int i=0;i<il.Length;i++)
                    {
                        int at=-1;
                        if(il[i]==0x28 || il[i]==0x6F || il[i]==0x73) at=i+1; // call, callvirt, newobj
                        else if(il[i]==0xFE && i+1<il.Length && (il[i+1]==0x06 || il[i+1]==0x07)) at=i+2; // ldftn, ldvirtftn
                        if(at<0 || at+4>il.Length) continue;
                        int token=BitConverter.ToInt32(il,at);int table=(int)((uint)token>>24);
                        if(table!=0x06 && table!=0x0A && table!=0x2B) continue;
                        MethodBase resolved;
                        try { resolved=method.Module.ResolveMethod(token,type.IsGenericType?type.GetGenericArguments():null,method.IsGenericMethod?method.GetGenericArguments():null); }
                        catch { continue; }
                        if(resolved!=null && resolved.MetadataToken==target.MetadataToken && resolved.Module==target.Module) { found.Add(type.FullName+"."+method.Name);break; }
                    }
                }
            return found;
        }

        [Test, Category("REQ_INT_06"), NUnit.Framework.Property("Requirement","REQ-INT-06")]
        [Ignore("Requirement gap: no runtime reporter passes a confirmed no-onset to FixedSlotEngine.ObserveAudible, so the block-end retry is never scheduled in the app")]
        public void ARuntimeReporterForwardsConfirmedNoOnsetEvidenceToTheEngine()
        {
            var recordResponse=typeof(FixedSlotEngine).GetMethod(nameof(FixedSlotEngine.RecordResponse));
            Assert.That(Callers(recordResponse),Is.Not.Empty,"positive control: the joined host binds the engine's response sink");
            var observe=typeof(FixedSlotEngine).GetMethod(nameof(FixedSlotEngine.ObserveAudible));
            Assert.That(Callers(observe),Is.Not.Empty,"a runtime component must report ConfirmedNoOnset (for example a missing-playback delivery fault) to the engine");
        }

        [Test, Category("REQ_LOG_01"), NUnit.Framework.Property("Requirement","REQ-LOG-01")]
        [Ignore("Requirement gap: no runtime code writes the opportunity record (DataObservations.Opportunity), so scheduled slots that are never reached leave no journal row")]
        public void TheJoinedRuntimeListsEveryScheduledOpportunityInTheJournal()
        {
            var device=typeof(AcousticVocab.DataLogging.DataObservations).GetMethod(nameof(AcousticVocab.DataLogging.DataObservations.Device));
            Assert.That(Callers(device),Is.Not.Empty,"positive control: runtime device observations are journaled");
            var opportunity=typeof(AcousticVocab.DataLogging.DataObservations).GetMethod(nameof(AcousticVocab.DataLogging.DataObservations.Opportunity));
            Assert.That(Callers(opportunity),Is.Not.Empty,"each scheduled opportunity (role, masked method, schedule hash) must be journaled before the visit runs");
        }

        [Test, Category("REQ_ETH_01"), NUnit.Framework.Property("Requirement","REQ-ETH-01")]
        public void NoRuntimeEntryPointCanReportParticipantAdmission()
        {
            var owners=new List<string>();
            foreach(var assembly in RuntimeAssemblies()) foreach(var type in Types(assembly))
            {
                var property=type.GetProperty("ParticipantAdmission",Declared);
                if(property==null) continue;
                owners.Add(type.Name);
                Assert.That(property.PropertyType,Is.EqualTo(typeof(bool)),type.FullName);
                Assert.That(property.CanWrite,Is.False,type.FullName);
                // The getter body is the constant false: ldc.i4.0; ret (debug nops ignored).
                byte[] il=property.GetGetMethod(true).GetMethodBody().GetILAsByteArray().Where(b=>b!=0x00).ToArray();
                Assert.That(il,Is.EqualTo(new byte[]{0x16,0x2A}),type.FullName);
            }
            Assert.That(owners,Is.SupersetOf(new[]{"JoinedEngineeringConfig","JoinedEngineeringBootstrap","PreallocationEngineeringHost","SimulationTestAuthority"}));
        }
    }
}
