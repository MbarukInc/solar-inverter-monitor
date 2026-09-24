"""Check every env var the code reads is declared in docker-compose.yml.

Twice a setting has been read by the code but never plumbed through, so
setting it did nothing, silently: USB_DEVICE was hardcoded past its own
variable, and DEBUG_REGISTERS was simply absent. Both looked configured and
were not.

Compose is the one place that can still happen. Until 2026-09-24 this also
checked the Pi deploy action and workflows; those are gone, and on the cluster
an env entry on the Deployment reaches the process with nothing in between.

Run from the repo root:  python3 monitor/check_env_plumbing.py
"""

import re
import sys
from pathlib import Path

# Read by the code but deliberately not deployable: local debugging aids you
# set by hand when running the container interactively.
LOCAL_ONLY = {"DUMP_REGISTERS", "DUMP_DIR", "INTER_READ_DELAY",
              "SAMPLE_INTERVAL", "RECONNECT_AFTER", "LOG_LEVEL",
              "DB_HOST", "DB_PORT", "DB_USERNAME", "DB_PASSWORD", "DB_NAME",
              "INVERTER_MODEL"}

ROOT = Path(__file__).resolve().parent.parent


def env_names_read():
    names = set()
    for path in (ROOT / "monitor").rglob("*.py"):
        if path.name == Path(__file__).name:
            continue
        for match in re.finditer(r'os\.environ\.get\(\s*"([A-Z_][A-Z0-9_]*)"',
                                 path.read_text()):
            names.add(match.group(1))
    return names


def main():
    compose = (ROOT / "docker-compose.yml").read_text()

    problems = []
    for name in sorted(env_names_read()):
        if name in LOCAL_ONLY:
            continue
        missing = [layer for layer, text in
                   (("docker-compose.yml", compose),)
                   if name not in text and name.lower() not in text]
        status = "ok" if not missing else "MISSING in " + ", ".join(missing)
        print("  {0:<20} {1}".format(name, status))
        if missing:
            problems.append((name, missing))

    if problems:
        print("\n{0} setting(s) read by the code but not deliverable.".format(len(problems)))
        return 1
    print("\nEvery deployable setting is declared in docker-compose.yml.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
