#!/usr/bin/env python3
"""Import the alert rules in this directory into Grafana Cloud.

Grafana's *Import alert rules* dialog reads Prometheus-format rule files --
`groups[].rules[].expr` -- and creates data-source-managed rules. The files
here are Grafana-managed rules in Grafana's own provisioning format, which that
dialog rejects with "missing or invalid groups array at index 0". They go
through the provisioning API instead, one rule at a time, and the API wants the
rule object itself rather than the apiVersion/groups wrapper around it.

    export GRAFANA_URL=https://<stack>.grafana.net
    export GRAFANA_TOKEN=<service account token, Editor or Admin>
    python3 grafana/alerts/import.py --folder Power

The folder is looked up by title and must already exist. Re-running updates
each rule in place: every rule carries a fixed uid, so this is idempotent.

X-Disable-Provenance keeps the rules editable in the UI. Without it Grafana
marks them provisioned and refuses edits there, which is wrong for rules a
person is still tuning -- the cell-voltage threshold especially.
"""
import argparse, json, os, sys, urllib.error, urllib.request

try:
    import yaml
except ImportError:
    sys.exit("needs pyyaml: pip3 install pyyaml")

HERE = os.path.dirname(os.path.abspath(__file__))


def call(url, token, path, method="GET", body=None):
    req = urllib.request.Request(url.rstrip("/") + path, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Disable-Provenance", "true")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data, timeout=30) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", help="folder title (default: each rule's own)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    url, token = os.environ.get("GRAFANA_URL"), os.environ.get("GRAFANA_TOKEN")
    if not url or not token:
        sys.exit("set GRAFANA_URL and GRAFANA_TOKEN")

    status, folders = call(url, token, "/api/folders?limit=1000")
    if status != 200:
        sys.exit("could not list folders: %s %s" % (status, folders))
    by_title = {f["title"]: f["uid"] for f in folders}

    rc = 0
    for name in sorted(os.listdir(HERE)):
        if not name.endswith((".yaml", ".yml")):
            continue
        doc = yaml.safe_load(open(os.path.join(HERE, name)))
        for group in doc.get("groups", []):
            title = args.folder or group.get("folder")
            uid = by_title.get(title)
            if not uid:
                print("  %-46s FOLDER %r not found (have: %s)"
                      % (name, title, ", ".join(sorted(by_title)) or "none"))
                rc = 1
                continue
            for rule in group.get("rules", []):
                payload = dict(rule)
                payload["folderUID"] = uid
                payload["ruleGroup"] = group["name"]
                payload["orgID"] = group.get("orgId", 1)
                if args.dry_run:
                    print("  %-46s would import %r -> folder %s"
                          % (name, payload["title"], title))
                    continue
                # Fixed uids make this idempotent: update if it is already there.
                st, _ = call(url, token,
                             "/api/v1/provisioning/alert-rules/" + payload["uid"])
                if st == 200:
                    st, body = call(url, token,
                                    "/api/v1/provisioning/alert-rules/" + payload["uid"],
                                    "PUT", payload)
                    verb = "updated"
                else:
                    st, body = call(url, token,
                                    "/api/v1/provisioning/alert-rules", "POST", payload)
                    verb = "created"
                ok = st in (200, 201, 202)
                print("  %-46s %s %s (%s)" % (name, "OK " if ok else "FAIL", verb, st))
                if not ok:
                    print("      %s" % body)
                    rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
