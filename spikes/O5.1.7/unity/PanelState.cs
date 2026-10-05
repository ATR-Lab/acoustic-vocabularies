using System;

namespace AcousticVocab.Spikes.Input
{
    public sealed class PanelState
    {
        public string Target { get; private set; } = "";
        public string Action { get; private set; } = "";
        public static string Family(string target) => target.Length == 1 && "ABCDEFGH".Contains(target)
            ? ("ABCD".Contains(target) ? "tray" : "container") : "";
        public static bool Legal(string target, string action)
        {
            string family = Family(target);
            if (family == "") return false;
            for (int i = 1; i <= 4; i++) if (action == family + "-" + i) return true;
            return false;
        }
        public bool CanCommit => Legal(Target, Action);
        public void Reset() { Target = ""; Action = ""; }
        public bool SelectTarget(string target)
        {
            if (Family(target) == "") return false;
            if (Family(Target) != Family(target)) Action = "";
            Target = target;
            return true;
        }
        public bool SelectAction(int index)
        {
            if (Family(Target) == "" || index < 1 || index > 4) return false;
            Action = Family(Target) + "-" + index;
            return true;
        }
        // Invoked by the editor setup/CI method against the actual runtime state model.
        public static void VerifyLegality()
        {
            var state = new PanelState();
            if (state.CanCommit || state.SelectAction(1)) throw new Exception("Preselection violated");
            int combinations = 0;
            foreach (char target in "ABCDEFGH") for (int action = 1; action <= 4; action++)
            {
                state.Reset(); state.SelectTarget(target.ToString()); state.SelectAction(action);
                if (!state.CanCommit) throw new Exception("Legal tuple refused");
                combinations++;
            }
            state.Reset(); state.SelectTarget("A"); state.SelectAction(1); state.SelectTarget("B");
            if (!state.CanCommit) throw new Exception("Compatible action should survive same-family target change");
            state.SelectTarget("E");
            if (state.CanCommit || state.Action != "") throw new Exception("Cross-family selection must clear action");
            if (combinations != 32 || state.SelectTarget("Z") || state.SelectAction(5)) throw new Exception("Invalid tuple accepted");
        }
    }
}
