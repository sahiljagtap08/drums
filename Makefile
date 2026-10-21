# With only the command line tools installed, XCTest is missing and Swift
# Testing sits outside the default search path. Add it for `swift test`.
CLT := /Library/Developer/CommandLineTools
SWIFT_TEST_FLAGS :=
ifeq ($(shell xcode-select -p),$(CLT))
FW := $(CLT)/Library/Developer/Frameworks
SWIFT_TEST_FLAGS := -Xswiftc -F$(FW) -Xlinker -F$(FW) -Xlinker -rpath -Xlinker $(FW)
endif

test:
	uv run pytest -q
	swift test --package-path envd $(SWIFT_TEST_FLAGS)

build-envd:
	swift build -c release --package-path envd

setup-host:
	@echo "see Task N"

probes:
	@echo "see Task N"

sweep:
	@echo "see Task N"

reproduce:
	@echo "see Task N"

.PHONY: test build-envd setup-host probes sweep reproduce
