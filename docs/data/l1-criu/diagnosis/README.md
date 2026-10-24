# Diagnosis of the first LibreOffice branching run (T3 failed for BOTH siblings)

Status: MEASURED / DIAGNOSTIC. Nothing here overwrites the first run: it is kept as `../task_branch_probe_run1_T3_dialog_harness_fail.json`
(T3 `own_a: false, own_b: false, a_in_b: false, b_in_a: false, PASS: false`; stages T1, T1b, T2, T4 and latency/memory passed in that run).

| File | What it is |
|---|---|
| `lo_debug_a11y.py` | Why the registered accessibility-tree readiness could not be used: in a COLD session, after the real `SetupController` launched Writer, the a11y tree stayed at 541 bytes with no Writer node while 99.6% of pixels differed from a blank desktop. This LibreOffice build has only the `gen` VCL plugin (no AT-SPI). Led to the amendment in section 18.5 |
| `lo_debug_cold_typing.py` | The cold control for T3: typing into a COLD session (no CRIU involved) changed 0.0 of the pixels and left the clipboard empty |
| `cold_before_typing.png`, `cold_after_typing_dialog_still_modal.png` | Actual screenshots from that cold control: the first-run "Tip of the Day" dialog is modal and swallows the keystrokes |
| `lo_restored_shots.py` | Captures screenshots of a RESTORED sibling |
| `restored_as_restored_dialog_present.png` | A restored sibling exactly as restored: the same dialog is present, i.e. it is part of the preserved state, which is the cause of run 1's T3 failure on both siblings (these screenshots are from a re-created restored sibling; run 1's own siblings were not screenshotted at the time) |
| `restored_after_dialog_dismissed_and_typed.png` | The same sibling after the corrected T3 steps (OK clicked, document clicked, token typed, select-all, copy); the clipboard read back the token |

Conclusion recorded: the T3 failure was a harness defect (an unhandled preserved modal dialog), not a failure of restore; the corrected rerun is `../task_branch_probe.json`.
