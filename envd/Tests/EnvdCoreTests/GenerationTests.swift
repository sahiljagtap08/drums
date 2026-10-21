import Testing
@testable import EnvdCore

@Test func generationCheck() async {
    let g = Generation(initial: 1)
    await g.set(4)
    #expect(await g.check(4))
    #expect(!(await g.check(3)))
}
