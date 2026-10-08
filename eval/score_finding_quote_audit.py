"""Score a manually labelled 证据评估 Finding -> exact quote audit sheet."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

VALID = {"supported", "partially_supported", "unsupported", "unclear"}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("audit", type=Path)
    args = p.parse_args()
    data = json.loads(args.audit.read_text(encoding="utf-8"))
    rows = data.get("rows", [])
    unlabeled = [r for r in rows if r.get("human_label") not in VALID]
    if unlabeled:
        raise SystemExit(f"仍有 {len(unlabeled)} 条未标注或标签无效")
    counts = {label: sum(r["human_label"] == label for r in rows) for label in sorted(VALID)}
    n = len(rows)
    strict = counts["supported"] / n if n else None
    lenient = (counts["supported"] + counts["partially_supported"]) / n if n else None
    by_relation = {}
    for relation in sorted({r["relation"] for r in rows}):
        subset = [r for r in rows if r["relation"] == relation]
        by_relation[relation] = {
            "n": len(subset),
            "supported": sum(r["human_label"] == "supported" for r in subset),
            "partial": sum(r["human_label"] == "partially_supported" for r in subset),
            "unsupported": sum(r["human_label"] == "unsupported" for r in subset),
            "unclear": sum(r["human_label"] == "unclear" for r in subset),
        }
    print(json.dumps({
        "n": n,
        "counts": counts,
        "strict_support_rate": strict,
        "lenient_support_rate": lenient,
        "by_relation": by_relation,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
