// The generation number for this environment. Every mutating request carries
// the generation it was made for. After a reset the generation goes up, so a
// late request from the old run is refused instead of applied.

/// The answer from `Generation.applyIfCurrent`.
public enum GenGate<T: Sendable>: Sendable {
    /// The generation matched and the work ran. Holds the work's result.
    case applied(T)
    /// The generation did not match. The work did not run. Holds the current one.
    case stale(current: Int)
}

public actor Generation {
    private var value: Int

    public init(initial: Int) {
        value = initial
    }

    public func current() -> Int { value }

    public func set(_ g: Int) { value = g }

    /// True when `g` is the current generation.
    public func check(_ g: Int) -> Bool { g == value }

    /// Runs `work` only if `g` is the current generation, as one step.
    ///
    /// This is how a mutating route stays safe against a reset. `work` is a
    /// plain (not async) closure, and an actor runs one method at a time. So
    /// while `work` runs, `set` cannot run, and the check and the effect cannot
    /// be split by a reset. If `set` ran first, the check fails and `work` never
    /// runs. If this ran first, the reset waits until `work` is done.
    ///
    /// Do not await anything inside `work`, and keep it short: it holds up
    /// every other request that needs the generation.
    public func applyIfCurrent<T: Sendable>(_ g: Int, _ work: @Sendable () -> T) -> GenGate<T> {
        guard g == value else { return .stale(current: value) }
        return .applied(work())
    }
}
