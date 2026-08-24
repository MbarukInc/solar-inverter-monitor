#!/usr/bin/env python3
"""Normalise a Grafana dashboard export before committing it.

A raw export carries server-side metadata that either churns on every save or
identifies the Grafana Cloud tenant. This repo is public, so both matter:

  resourceVersion, generation, creationTimestamp   change every save -> diff noise
  namespace (stack id), createdBy/updatedBy        tenant and user identifiers

`name` and `uid` are kept: re-importing with the same uid updates the existing
dashboard in place rather than creating a duplicate.

Usage:
    python3 grafana/normalise.py ~/Downloads/export.json grafana/dashboard.json
"""
import json
import sys

VOLATILE = ("resourceVersion", "generation", "creationTimestamp")
ANNOTATION_PREFIXES = ("grafana.app/created", "grafana.app/updated", "grafana.app/saved")


def normalise(doc: dict) -> dict:
    meta = doc.get("metadata", {})
    for key in VOLATILE:
        meta.pop(key, None)
    meta.pop("namespace", None)
    anns = meta.get("annotations", {})
    for key in [k for k in anns if k.startswith(ANNOTATION_PREFIXES)]:
        anns.pop(key)
    if not anns:
        meta.pop("annotations", None)
    return doc


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    with open(src) as handle:
        doc = json.load(handle)
    with open(dst, "w") as handle:
        json.dump(normalise(doc), handle, indent=2)
        handle.write("\n")
    print("wrote {0}".format(dst))
    return 0


if __name__ == "__main__":
    sys.exit(main())
