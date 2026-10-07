import AppKit
import SwiftUI

/// A plain-text editor for machine-read text (the raw-JSON editor of Validator & Book).
///
/// SwiftUI's `TextEditor` keeps the system's smart quotes, smart dashes and text
/// replacement on (`.autocorrectionDisabled()` turns off only spelling correction). With
/// the macOS defaults a typed `"` became `“`, so JSON typed by hand reached the engine as
/// malformed (`E_JSON`) although it looked valid. This editor is an `NSTextView` with
/// every automatic substitution and checking off: the text goes to the engine exactly as
/// typed.
struct PlainTextEditor: NSViewRepresentable {
    @Binding var text: String
    /// Called when the editor gains (`true`) or loses (`false`) the keyboard focus.
    var onFocusChange: (Bool) -> Void = { _ in }
    var accessibilityLabel = "Text"

    func makeCoordinator() -> Coordinator { Coordinator(text: $text) }

    func makeNSView(context: Context) -> NSScrollView {
        let scrollView = NSScrollView()
        scrollView.hasVerticalScroller = true
        scrollView.autohidesScrollers = true
        scrollView.borderType = .noBorder
        scrollView.drawsBackground = false
        let size = scrollView.contentSize
        let textView = FocusReportingTextView(frame: NSRect(origin: .zero, size: size))
        Self.configure(textView)
        textView.minSize = NSSize(width: 0, height: size.height)
        textView.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        textView.isVerticallyResizable = true
        textView.isHorizontallyResizable = false
        textView.autoresizingMask = [.width]
        textView.textContainer?.containerSize = NSSize(width: size.width, height: CGFloat.greatestFiniteMagnitude)
        textView.textContainer?.widthTracksTextView = true
        textView.string = text
        textView.delegate = context.coordinator
        textView.setAccessibilityLabel(accessibilityLabel)
        scrollView.documentView = textView
        return scrollView
    }

    func updateNSView(_ scrollView: NSScrollView, context: Context) {
        context.coordinator.text = $text
        guard let textView = scrollView.documentView as? FocusReportingTextView else { return }
        // A focus change can come during a SwiftUI update: report it after the update.
        textView.onFocusChange = { focused in
            DispatchQueue.main.async { onFocusChange(focused) }
        }
        textView.setAccessibilityLabel(accessibilityLabel)
        // Only an outside change (an example, "Current recipe") replaces the text, so the
        // selection and the undo stack survive typing.
        if textView.string != text {
            textView.string = text
        }
    }

    /// Switches off every automatic change of typed text and the checks that underline
    /// it: quote, dash and text-replacement substitution, spelling correction and
    /// checking, grammar checking, link and data detection, completion and smart
    /// insert/delete. Plain text, monospaced, undoable.
    static func configure(_ textView: NSTextView) {
        textView.isRichText = false
        textView.importsGraphics = false
        textView.usesFindBar = true
        textView.allowsUndo = true
        textView.isAutomaticQuoteSubstitutionEnabled = false
        textView.isAutomaticDashSubstitutionEnabled = false
        textView.isAutomaticTextReplacementEnabled = false
        textView.isAutomaticSpellingCorrectionEnabled = false
        textView.isContinuousSpellCheckingEnabled = false
        textView.isGrammarCheckingEnabled = false
        textView.isAutomaticLinkDetectionEnabled = false
        textView.isAutomaticDataDetectionEnabled = false
        textView.isAutomaticTextCompletionEnabled = false
        textView.smartInsertDeleteEnabled = false
        textView.font = .monospacedSystemFont(ofSize: NSFont.systemFontSize, weight: .regular)
        textView.textColor = .textColor
        textView.insertionPointColor = .textColor
        textView.drawsBackground = false
        textView.textContainerInset = NSSize(width: 0, height: 2)
    }

    @MainActor
    final class Coordinator: NSObject, NSTextViewDelegate {
        var text: Binding<String>

        init(text: Binding<String>) {
            self.text = text
        }

        func textDidChange(_ notification: Notification) {
            guard let textView = notification.object as? NSTextView else { return }
            text.wrappedValue = textView.string
        }
    }
}

/// An `NSTextView` that reports when it gains or loses the keyboard focus.
final class FocusReportingTextView: NSTextView {
    var onFocusChange: ((Bool) -> Void)?

    override func becomeFirstResponder() -> Bool {
        let became = super.becomeFirstResponder()
        if became { onFocusChange?(true) }
        return became
    }

    override func resignFirstResponder() -> Bool {
        let resigned = super.resignFirstResponder()
        if resigned { onFocusChange?(false) }
        return resigned
    }
}
