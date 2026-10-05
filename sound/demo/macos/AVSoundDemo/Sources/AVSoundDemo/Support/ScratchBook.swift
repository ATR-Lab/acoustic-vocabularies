import AVSoundDemoCore
import Foundation

/// The 16 atom slots of a book: families K and Q, actions a1-a4 and referents r1-r4.
enum AtomSlots {
    static let families = ["K", "Q"]

    struct Group: Identifiable, Hashable {
        let family: String
        let role: String
        let ids: [String]

        var id: String { "\(family)-\(role)" }
        var title: String { "\(family) \u{00B7} \(role == "a" ? "actions" : "referents")" }
    }

    static let groups: [Group] = families.flatMap { family in
        ["a", "r"].map { role in Group(family: family, role: role, ids: (1...4).map { "\(family)-\(role)\($0)" }) }
    }

    /// `K-a1` ... `K-r4`, `Q-a1` ... `Q-r4` (the grammar's order).
    static let all: [String] = groups.flatMap(\.ids)

    /// The matrix index (1-4) of an atom ID such as `K-a3`.
    static func index(of atomID: String) -> Int? {
        atomID.last.flatMap { Int(String($0)) }
    }
}

/// A user-editable book of atom recipes for one profile.
///
/// `origin` is the synthetic book ID (`DEMO-P2`) while the book is exactly the synthetic
/// DEMO book; it is sent as the `book_id` of `compose`. `label` names the fixed book the
/// scratch book is an unchanged copy of (the synthetic DEMO book, or the DEMO fallback
/// book), for display only. Any change clears both.
struct ScratchBook: Hashable, Sendable {
    var atoms: [String: Recipe] = [:]
    var origin: String?
    var label: String?

    var count: Int { atoms.count }
    var isEmpty: Bool { atoms.isEmpty }

    /// Atom references in slot order.
    var references: [AtomReference] {
        AtomSlots.all.compactMap { id in atoms[id].map { AtomReference(refID: id, recipe: $0) } }
    }

    func reference(_ atomID: String) -> AtomReference? {
        atoms[atomID].map { AtomReference(refID: atomID, recipe: $0) }
    }

    mutating func set(_ atomID: String, _ recipe: Recipe?) {
        guard atoms[atomID] != recipe else { return }
        atoms[atomID] = recipe
        origin = nil
        label = nil
    }

    static func demo(_ book: SyntheticBook) -> ScratchBook {
        ScratchBook(
            atoms: Dictionary(book.atoms.map { ($0.atomID, $0.recipe) }, uniquingKeysWith: { first, _ in first }),
            origin: book.bookID, label: book.bookID)
    }

    /// The DEMO fallback book of `profile` (from `fallback_demo`): no synthetic book ID,
    /// labeled with its seed (`DEMO fallback P2 (DEMO-fallback-v1)`).
    static func fallback(_ demo: FallbackDemo, profile: Profile) -> ScratchBook {
        ScratchBook(
            atoms: Dictionary(demo.book.map { ($0.atomID, $0.recipe) }, uniquingKeysWith: { first, _ in first }),
            origin: nil, label: fallbackLabel(seed: demo.seedLabel, profile: profile))
    }

    static func fallbackLabel(seed: String, profile: Profile) -> String {
        "DEMO fallback \(profile.rawValue) (\(seed))"
    }
}

/// The store's semantic-label ontology (sound/docs/store.md, "semantic_label"): each atom
/// of a synthetic book must carry one label of its family and role, each label at most
/// once per book. The bridge protocol does not report it, so it is listed here.
enum SemanticLabels {
    static let byGroup: [String: [String]] = [
        "K-a": ["ADD_ONE", "REMOVE_ONE", "FLIP_CARD", "ALIGN_ARROW"],
        "K-r": ["A", "B", "C", "D"],
        "Q-a": ["SCAN", "TAG", "CLOSE", "QUARANTINE"],
        "Q-r": ["E", "F", "G", "H"],
    ]

    /// The labels allowed for an atom ID such as `K-a3`.
    static func labels(for atomID: String) -> [String] {
        byGroup[String(atomID.prefix(3))] ?? []
    }

    /// The demo binding: the label at the atom's matrix index (`K-a1` -> `ADD_ONE`).
    static func demoLabel(for atomID: String) -> String? {
        let labels = labels(for: atomID)
        guard let index = AtomSlots.index(of: atomID), index >= 1, index <= labels.count else { return nil }
        return labels[index - 1]
    }
}
