"""PROTOTYPE of L1 mechanism F (fork server). See docs/l1-shared-runtime-design.md section 8 (pre-registration).

Nothing here is an L1 result: Chromium is started by exec in every arm, so the largest memory component is not
shared by fork. Scorer runs that use it must still record drums_level=L0."""
