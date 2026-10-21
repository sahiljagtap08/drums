// The generation number for this environment. Every mutating request carries
// the generation it was made for. After a reset the generation goes up, so a
// late request from the old run is refused instead of applied.
public actor Generation {
    private var value: Int

    public init(initial: Int) {
        value = initial
    }

    public func current() -> Int { value }

    public func set(_ g: Int) { value = g }

    /// True when `g` is the current generation.
    public func check(_ g: Int) -> Bool { g == value }
}
