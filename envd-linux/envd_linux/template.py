"""Build the shared read-only template tree used as the overlay lower layer: a Chromium
profile after its first run, so every session starts from an initialised profile and only
pays (copy-on-write) for what it changes."""

import os
import subprocess

DEFAULT_CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"


def build_home_template(path, chrome=DEFAULT_CHROME, timeout=90):
    os.makedirs(path, exist_ok=True)
    profile = os.path.join(path, "profile")
    env = dict(os.environ, HOME=path)
    subprocess.run([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--no-first-run",
                    "--no-default-browser-check", f"--user-data-dir={profile}", "--dump-dom", "about:blank"],
                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    return path
