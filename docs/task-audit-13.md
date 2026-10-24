# Audit of the 13 smallest-tier candidates, and the evaluator-stub caveat (findings only)

**Status: MEASURED from files and one local import (read-only; nothing was run, installed, downloaded or logged in; `92d4603` code and tests unchanged; the 232-pass suite identity is `d6a9e8a`). Result: 13 candidates become 1 unchanged, 1 conditional, 11 blocked. "No known blocker" from `docs/task-coverage.md` meant a coarse static filter, never compatibility; this audit supersedes that reading. Nothing here is a pass claim.**

Sources are `evaluation_examples/examples/<domain>/<id>.json`, `desktop_env/evaluators/{metrics,getters}/*` (OSWorld `04718ea`), `envd-linux/` and `/opt/drums-osworld-real/server/main.py`.

## 1. The evaluator-stub caveat, and what each run recorded

`import desktop_env.desktop_env` imports the whole `desktop_env.evaluators.metrics` package, whose `vlc.py:11` does `import librosa`. `librosa` is not installed in the OSWorld venv (checked: real import fails with `ModuleNotFoundError`). Every driver that imports `DesktopEnv` loops on `ModuleNotFoundError` and puts a `MagicMock` in `sys.modules` for the missing module. Nothing else in the checkout calls `librosa` except `metrics/vlc.py:248-278` and an OSWorld test file.

| Run | Stub list | Where |
|---|---|---|
| K2 public task (`l1-ksm/ksm_semantics.json`) | **RECORDED: `["librosa"]`** | key `public_task_in_K2.stubbed_modules` (line 44) |
| Task-flow test, in suite12 (`tests/test_osworld_task_flow.py:62`) | asserted `<= {"librosa"}` at run time; ran with the `DRUMS_OSW_*` variables set and no skip reported | test assertion, not a stored list |
| V1 / V2 (`matched_reuse/run1`, `run2`, `run3_final`), keys `harness_validation_only, task, v1_none, v1_post_setup, v2` (`run1`: also `error`, `trace`) | **NOT SAVED: historical unknown** | loop at `tests/helpers/reuse_real_driver.py:15-29` |
| I3 smokes (`first_check_smoke_v1`, `_limits`, original), result keys `driver_smoke_only, task, blocks, ..., summary` | **NOT SAVED: historical unknown** | `envd_linux/l1proto/first_check_smoke.py:20-32` |
| CRIU probes (`probe*.json`, `task_branch_probe*.json`, hygiene) | **NOT SAVED: historical unknown** | `envd_linux/l1proto/criu_probe.py:34-50` |

**Inference only, not a record:** the venv's newest `dist-info` has mtime 2026-10-23 14:27, before every committed scored run (earliest 2026-10-24 06:45), and `librosa` is the only missing module today, so the unrecorded runs were very probably stubbed with `librosa` alone. The current import failure is not itself evidence of the historical stub set, and no unrecorded run is called "fully unmocked". Raw JSON and history are not rewritten.

**Trace of the `e0df059f` scored path** (any stub contact?): setup `execute` steps (task JSON, no `postconfig`) go to the guest server; `desktop_env.py:397-415` only resolves `getattr(metrics, "exact_match")`, `get_vm_command_line`, `get_rule`; `evaluate()` (~`:480-544`) has no postconfig and is not the infeasible type; `getters/general.py:8-20` posts the command to the guest server's `/execute` (a separate process in the guest venv, no stubs); `getters/misc.py:92` returns the rule; `metrics/general.py:68-74` is `result == expect`. **No step reaches `librosa`.**

**What that supports, narrowly:** the scripted scores on `e0df059f` (0.0 before, 1.0 after) are a plain string comparison of one shell command's output (a shell getter and a string metric), valid as such. **Not supported:** that the full OSWorld evaluator stack, or any metric that touches VLC or audio, works. Earlier phrases "real evaluator" in the README, `docs/mechanism-c-criu-restore.md`, the design doc (sections 15, 17, 20, 21) and the plan mean this narrow path; section 25 of the design doc records the correction.

## 2. The 13 candidates (checked versus reasoned)

Checked = read from the task JSON, the evaluator source, `which`, or the one local import. Reasoned = inferred from code, not run in a session. Importing the metrics package: not checked end to end (`librosa`); getters: checked, imports cleanly.

| ID | Verdict | Deciding facts, source | Basis |
|---|---|---|---|
| `e0df059f` | **Unchanged: fit for a scripted check** | `metrics/general.py:68`, `getters/general.py:8`; `~` paths | checked; already run on Drums |
| `2cd43775` | **Conditional** | autosave 3 min; reads `/home/user/.config/libreoffice/4/user/registrymodifications.xcu`; session HOME is `/home/drums` (`session.py:19`); `metrics/slides.py:1060-1089` (ElementTree only) | checked; HOME mismatch reasoned |
| `2373b66a` | Blocked | setup `apt-get update && install sysstat`; `sar` absent | checked |
| `3c8f201a` | Blocked | agent downloads from huggingface.co; GIMP absent; `Ctrl+Alt+T` | checked |
| `e1fc0df3` | Blocked | internet extension install; `/home/user` path | checked |
| `f8369178` | Blocked | network; `gsettings` GNOME theme | checked (network need reasoned from the instruction) |
| `13584542` | Blocked | `vm_terminal_output` needs AT-SPI on `gnome-terminal-server` (`server/main.py:342-360`); openbox has no `Ctrl+Alt+T` (`/etc/xdg/openbox/rc.xml`) | checked |
| `28cc3b7e` | Blocked | `pulseaudio`, `pactl` absent; setup exits 1 with no sink | checked |
| `3ce045a0` | Blocked | GNOME `gsettings` text scaling | checked; "no persistence without dconf or D-Bus" reasoned |
| `5812b315` | Blocked | `postconfig` `apt-get install expect` and an HF download | checked |
| `5ced85fc` | Blocked | `postconfig` HF download; `Ctrl+Alt+T`; `activate_window` | checked |
| `94d95f96` | Blocked | Spotify install needs the network | checked; "also blocked on an offline incumbent" reasoned |
| `b6781586` | Blocked | `timedatectl` needs systemd and D-Bus; `metrics/basic_os.py:35` | checked (`timedatectl` fails here); Drums-session behaviour reasoned |

The static classifier missed these because it scans only `config` commands and getter types: apt and network use inside `command` setup, downloads in `postconfig` and in the instruction, GNOME hotkeys and `gsettings` in `postconfig`, hard-coded `/home/user`, systemd and PulseAudio needs. No classifier change is made (code frozen).

## 3. Smallest credible alignment for the second task (`2cd43775`): DESIGN ONLY, nothing implemented

**Why this one:** it is the only candidate with a real, expensive application state (LibreOffice Impress), no network, and an evaluator that reads a plain XML file with no `librosa` involvement (`slides.py:1060-1089`).

**The blocker:** the unmodified task reads `/home/user/.config/...`; Drums sessions use `HOME=/home/drums`, so LibreOffice would write the file under `/home/drums/.config/...`. By reading the code, the missing file reaches `check_auto_saving_time` as `None` and `ET.parse(None)` raises a `TypeError` that the function does not catch (reasoned, not run), so it would surface as an evaluation fault, not a clean 0.

**Option A (preferred, keeps the public task unmodified):** make the Drums session home `/home/user`, the OSWorld VM user's home, so the task's paths are identical on both arms. It needs the home constant (`session.py:19`) to become a Supervisor parameter with the current default kept (so existing tests and raw results keep their identity), the template built at `<tpl>/home/user`, and the home overlay mounted there (`session_init.py:273`). Side effect to confirm: the full-root overlay currently exposes whatever the host has at `/home/user` read-only to sessions (reasoned from `--root full`, not checked in a live session); a home overlay on `/home/user` would hide it.
**Option B (no code change, a labelled variant):** use `path: "~/.config/libreoffice/4/user/registrymodifications.xcu"` in a copy of the task JSON. The guest server expands `~` in its file endpoint (`server/main.py:1141`), so one modified JSON works on both arms, but it is a **custom variant of `2cd43775`, not the public task**, and must be labelled so on both arms and in every result.

**Rules for either option:** the action sequence changes the setting through LibreOffice's own dialog (Tools, Options, Load/Save, General) by GUI actions, identical on both arms; **nothing writes the `.xcu` directly or otherwise shapes the target to satisfy the evaluator.**

**Clean no-stub path:** the stub is only avoidable by installing `librosa` (and its dependencies) into the OSWorld venv, an owner-approved install not done here. Until then: record the stub list in every result (a small driver change, not made) and rely on the traced fact that this task's evaluator path does not reach `librosa`; a run is described as "stubbed `librosa`, unreached by this evaluator", never as unmocked.

**Remaining checks before this task can be called usable (all need a live session, so none done):**
1. Decide Option A or B.
2. In a Drums session, record what LibreOffice writes for `AutoSaveTimeIntervall`, in which file and WHEN (on dialog confirm or only on exit); the evaluator needs the value present at scoring time (`slides.py:1060-1089` compares the integer to 3).
3. Show the evaluator score 0 before and 1 after the GUI action sequence, on both arms with the same sequence.
4. Record the stub list per run.
5. Confirm the incumbent image has LibreOffice Impress, the same home layout and the same default; unverifiable until an image exists.

**A rented host alone does not unblock this**: it still needs an image, the HOME decision, the GUI-semantics check above, and the unresolved licence items in `docs/incumbent-preflight-plan.md`. No third honest task exists among the 13 and none is forced. No auth, install, download, network, spend, quota or merge action was taken.
