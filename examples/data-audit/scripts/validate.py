"""Skill-owned integrity check, invoked by the IR `run_script` op.

This file is an example of what a real skill ships in its own scripts/
directory. It prints a single JSON object on stdout; skill-forge captures it,
parses it and exposes it as a normal emitted variable.
"""
import json

METRICS = "data/metrics.csv"
REQUIRED_COLUMNS = ["name", "value", "status", "owner"]


def main():
    with open(METRICS, "r", encoding="utf-8-sig") as fh:
        header = fh.readline().strip().split(",")
        rows = [line for line in fh if line.strip()]
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    print(json.dumps({
        "ok": not missing,
        "checked": len(rows),
        "missing_columns": missing,
        "issues": [] if not missing else ["missing: " + ",".join(missing)],
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
