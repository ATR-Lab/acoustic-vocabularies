namespace AcousticVocab.Teaching
{
    // What the "Your turn" prompt asks. It depends only on the lesson kind and
    // the atom's role, never on the answer, and is the same for both formats.
    public enum LessonQuestion { Action, Target, Message }

    // Optional presentation hooks for the storyboard lesson card. The content
    // factory calls them right after the existing Show/Hide/Highlight calls,
    // from the same timeline events and tick; they add no timing, records or
    // input. A view that does not implement this interface is unaffected.
    public interface ILessonStoryView
    {
        // Step chips: Definition = Listen, Retrieval = Your turn (the meaning
        // stays hidden), Feedback = Check, Ended = hide.
        void Step(string owner,LessonDisplayPhase phase,LessonQuestion question);
        // Aligned lessons only: the lit label's motif progress, from
        // LessonTimeline.PlayheadAt. Null hides the playhead.
        void Playhead(string owner,LessonHighlight segment,float? fraction);
    }
}
