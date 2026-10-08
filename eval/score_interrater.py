"""Score agreement between two human 证据评估 finding audits."""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path

LABELS = ["supported", "partially_supported", "unsupported", "unclear"]


def key(row: dict) -> tuple[str, str]:
    return str(row["case_id"]), str(row["finding_id"])


def label(row: dict) -> str | None:
    value = row.get("human_label", row.get("reviewer_label"))
    return str(value) if value is not None else None


def kappa(a: list[str], b: list[str], labels: list[str]) -> float | None:
    if not a:
        return None
    observed = sum(x == y for x, y in zip(a, b)) / len(a)
    ca, cb = Counter(a), Counter(b)
    expected = sum((ca[l] / len(a)) * (cb[l] / len(b)) for l in labels)
    if abs(1 - expected) < 1e-12:
        return None
    return round((observed - expected) / (1 - expected), 6)


def binary_kappa(a: list[str], b: list[str], positive: set[str]) -> float | None:
    aa = ["positive" if x in positive else "negative" for x in a]
    bb = ["positive" if x in positive else "negative" for x in b]
    return kappa(aa, bb, ["positive", "negative"])


def score(first: dict, second: dict) -> dict:
    left = {key(row): row for row in first["rows"]}
    right = {key(row): row for row in second["rows"]}
    if set(left) != set(right):
        missing_second = sorted(set(left) - set(right))
        missing_first = sorted(set(right) - set(left))
        raise ValueError(f"review samples differ; missing_second={missing_second}, missing_first={missing_first}")
    pairs = []
    for k in sorted(left):
        a, b = label(left[k]), label(right[k])
        if a not in LABELS or b not in LABELS:
            raise ValueError(f"missing/invalid label for {k}: first={a}, second={b}")
        pairs.append((k, a, b, left[k]))
    aa = [p[1] for p in pairs]; bb = [p[2] for p in pairs]
    confusion = {a: {b: 0 for b in LABELS} for a in LABELS}
    disagreements = []
    for (case_id, finding_id), a, b, row in pairs:
        confusion[a][b] += 1
        if a != b:
            disagreements.append({"case_id": case_id, "finding_id": finding_id, "first": a, "second": b, "claim": row.get("claim")})
    agreement = sum(a == b for a, b in zip(aa, bb)) / len(aa) if aa else 0
    return {
        "n": len(pairs),
        "raw_agreement": round(agreement, 6),
        "cohen_kappa_4class": kappa(aa, bb, LABELS),
        "strict_support_kappa": binary_kappa(aa, bb, {"supported"}),
        "lenient_support_kappa": binary_kappa(aa, bb, {"supported", "partially_supported"}),
        "first_counts": dict(Counter(aa)),
        "second_counts": dict(Counter(bb)),
        "confusion_matrix": confusion,
        "disagreements": disagreements,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = score(json.loads(args.first.read_text(encoding="utf-8")), json.loads(args.second.read_text(encoding="utf-8")))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
