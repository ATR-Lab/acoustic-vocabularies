import Foundation

/// One line of the bridge log view.
public struct BridgeLogEntry: Sendable, Hashable, Identifiable {
    public enum Source: String, Sendable, Hashable {
        /// A stderr line of the bridge process (engine and uv diagnostics).
        case stderr
        /// A stdout line that is not a protocol message.
        case stdout
        /// A note from the client (launch, exit, protocol problems, timeouts).
        case client
    }

    /// Increasing sequence number (unique for the client's lifetime).
    public let id: Int
    public let date: Date
    public let source: Source
    public let text: String

    public init(id: Int, date: Date, source: Source, text: String) {
        self.id = id
        self.date = date
        self.source = source
        self.text = text
    }
}

/// A first-in, first-out buffer that keeps the last `capacity` elements.
public struct BoundedBuffer<Element: Sendable>: Sendable {
    public let capacity: Int
    private var storage: [Element] = []
    private var head = 0

    public init(capacity: Int) {
        precondition(capacity > 0, "capacity must be positive")
        self.capacity = capacity
        storage.reserveCapacity(capacity)
    }

    public var count: Int { storage.count }
    public var isEmpty: Bool { storage.isEmpty }

    public mutating func append(_ element: Element) {
        if storage.count < capacity {
            storage.append(element)
        } else {
            storage[head] = element
            head = (head + 1) % capacity
        }
    }

    public mutating func removeAll() {
        storage.removeAll(keepingCapacity: true)
        head = 0
    }

    /// The elements, oldest first.
    public var elements: [Element] {
        head == 0 ? storage : Array(storage[head...] + storage[..<head])
    }

    /// The newest `n` elements, oldest first.
    public func suffix(_ n: Int) -> [Element] { Array(elements.suffix(n)) }
}
