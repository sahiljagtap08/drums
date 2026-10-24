"""Static survey of the setup work in the public OSWorld task configs (Apache-2.0 xlang-ai/OSWorld). MEASURED from the files, not from any run.

    python -m envd_linux.l1proto.setup_survey <OSWorld checkout> --out setup_survey.json

It counts what each task's `config` asks the guest to do before the agent starts (downloads, app launches, file opens, explicit sleeps, browser or login
steps), where downloads come from, and how many tasks need nothing from the network. It says nothing about how long any of it takes."""

import argparse
import collections
import glob
import json
import os
import statistics
import urllib.parse


def survey(root):
    rows = []
    hosts = collections.Counter()
    launch_cmds = collections.Counter()
    for f in sorted(glob.glob(os.path.join(root, "evaluation_examples", "examples", "*", "*.json"))):
        d = json.load(open(f))
        cfg = d.get("config", [])
        row = {"id": d["id"], "domain": f.split(os.sep)[-2], "steps": len(cfg), "downloads": 0, "launch": 0, "open": 0, "sleep_s": 0.0, "browser_or_login": 0,
               "apps": []}
        for c in cfg:
            ty, p = c["type"], c.get("parameters", {})
            if ty == "download":
                row["downloads"] += len(p.get("files", []))
                for x in p.get("files", []):
                    hosts[urllib.parse.urlparse(x["url"]).netloc] += 1
            elif ty == "launch":
                row["launch"] += 1
                cmd = p.get("command", [])
                cmd = cmd.split() if isinstance(cmd, str) else cmd
                if cmd:
                    row["apps"].append(os.path.basename(cmd[0]))
                    launch_cmds[os.path.basename(cmd[0])] += 1
            elif ty == "open":
                row["open"] += 1
            elif ty == "sleep":
                row["sleep_s"] += p.get("seconds", 0)
            elif ty in ("chrome_open_tabs", "login", "googledrive"):
                row["browser_or_login"] += 1
        rows.append(row)
    n = len(rows)
    sleeps = [r["sleep_s"] for r in rows]
    offline = [r for r in rows if r["downloads"] == 0 and r["browser_or_login"] == 0]
    return {
        "tasks": n, "with_config": sum(1 for r in rows if r["steps"]),
        "with_downloads": sum(1 for r in rows if r["downloads"]), "download_files": sum(r["downloads"] for r in rows), "download_hosts": dict(hosts),
        "with_launch": sum(1 for r in rows if r["launch"]), "with_open": sum(1 for r in rows if r["open"]),
        "with_browser_or_login": sum(1 for r in rows if r["browser_or_login"]),
        "explicit_sleep_s": {"mean": statistics.mean(sleeps), "max": max(sleeps), "tasks_ge_10s": sum(1 for s in sleeps if s >= 10)},
        "launch_commands": dict(launch_cmds.most_common()),
        "no_network_needed_by_setup": len(offline),
        "no_network_and_launches_or_opens": sum(1 for r in offline if r["launch"] or r["open"]),
        "per_domain": {k: {"tasks": len(v), "with_downloads": sum(1 for r in v if r["downloads"])}
                       for k, v in ((dom, [r for r in rows if r["domain"] == dom]) for dom in sorted({r["domain"] for r in rows}))},
        "rows": rows,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    res = survey(a.root)
    json.dump(res, open(a.out, "w"), indent=1)
    print(json.dumps({k: v for k, v in res.items() if k not in ("rows", "per_domain")}, indent=1))


if __name__ == "__main__":
    main()
