"""Check every env var the code reads is actually delivered to the container.

Twice now a setting has been read by the code but never plumbed through
docker-compose, the deploy action and the workflows -- so setting the
repository variable did nothing, silently. USB_DEVICE was hardcoded past its
own variable; DEBUG_REGISTERS was simply absent from all three. Both looked
configured and were not.

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
    action = (ROOT / ".github/actions/deploy-to-pi/action.yml").read_text()
    workflows = "".join((ROOT / ".github/workflows" / w).read_text()
                        for w in ("update_monitor.yml", "build_container.yml"))

    problems = []
    for name in sorted(env_names_read()):
        if name in LOCAL_ONLY:
            continue
        missing = [layer for layer, text in
                   (("docker-compose.yml", compose),
                    ("deploy action", action),
                    ("workflows", workflows))
                   if name not in text and name.lower() not in text]
        status = "ok" if not missing else "MISSING in " + ", ".join(missing)
        print("  {0:<20} {1}".format(name, status))
        if missing:
            problems.append((name, missing))

    if problems:
        print("\n{0} setting(s) read by the code but not deliverable.".format(len(problems)))
        return 1
    print("\nEvery deployable setting is plumbed end to end.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
