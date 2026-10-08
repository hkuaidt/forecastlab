"""Create a blind second-reviewer packet from an 证据评估 finding audit sample."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

LABELS = ["supported", "partially_supported", "unsupported", "unclear"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--reviewer-id", default="reviewer_2")
    args = parser.parse_args()
    source = json.loads(args.input.read_text(encoding="utf-8"))
    rows = []
    for row in source["rows"]:
        clean = {k: v for k, v in row.items() if k not in {"human_label", "human_notes", "reviewer_label", "reviewer_notes"}}
        clean["human_label"] = None
        clean["human_notes"] = ""
        rows.append(clean)
    packet = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reviewer": args.reviewer_id,
        "blind_to_first_reviewer": True,
        "label_schema": LABELS,
        "rubric": {
            "supported": "The material claim is directly entailed by the cited exact quote(s).",
            "partially_supported": "The quote supports the core point but the claim adds a material detail/context not directly present.",
            "unsupported": "The quote does not support the material claim or supports a materially different proposition.",
            "unclear": "The quote/sample is too ambiguous to judge reliably.",
        },
        "instructions": "Judge finding.claim only against its cited exact quote(s). Do not use outside knowledge or source metadata to rescue unsupported claim content.",
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {args.output} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
