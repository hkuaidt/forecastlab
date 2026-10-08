"""Build a larger hard-negative entailment benchmark from prior human-role audits.

The benchmark combines unique natural labelled claim/quote pairs from five historical
证据评估 audits, then creates exactly one controlled contradiction for every natural
supported row. Generated negatives alter the claim only; exact quotes stay frozen.

This avoids LLM-generated gold labels and makes every synthetic negative reproducible.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

SOURCES = [
    "experiment/evidence-quality/2026-10-08-boundary-v2/results/reviewer2-labeled.json",
    "experiment/evidence-quality/2026-10-08-boundary-v1/results/reviewer2-labeled.json",
    "experiment/evidence-quality/2026-10-07-semantic/results/evidence-audit-labeled-after.json",
    "experiment/evidence-quality/2026-10-07-semantic/results/evidence-audit-labeled-before.json",
    "experiment/question-framing/2026-10-07-robustness/results/evidence-finding-quote-audit-labeled.json",
]

TEST_CASES = {
    "C03-apple-m4-mac", "C07-gpt5-availability",
    "C10-real-madrid-ucl", "C13-worldcup-southamerica",
    "C16-cop30-roadmap-adopted", "C19-copernicus-h1",
    "C22-ndx-h2-2025", "C24-gold-q3-2026",
}


def split_for_case(case_id: str) -> str:
    return "test" if case_id in TEST_CASES else "dev"


LABEL_MAP = {
    "supported": "entailed",
    "partially_supported": "partially_entailed",
    "unsupported": "not_entailed",
    "unclear": "unclear",
}

NO_DIGIT_MUTATIONS = [
    ("并非全部", "全部", "quantifier_flip"),
    ("卫冕冠军", "非卫冕冠军", "entity_status_flip"),
    ("法国居首", "法国居第二", "ranking_flip"),
    ("宣布推出", "尚未宣布推出", "event_negation"),
    ("表示反对", "表示支持", "stance_flip"),
    ("必须推动", "无需推动", "modality_flip"),
    ("已进入活动极大期", "尚未进入活动极大期", "event_negation"),
    ("公布了", "从未公布", "event_negation"),
]


def quote_text(row: dict) -> str:
    return "\n".join(c.get("quote", "") for c in row.get("citations", []))


def stable_key(row: dict) -> str:
    return json.dumps([row.get("claim", ""), quote_text(row)], ensure_ascii=False, separators=(",", ":"))



def mutate_number(claim: str) -> tuple[str, str] | None:
    match = re.search(r"\d+(?:\.\d+)?", claim)
    if not match:
        return None
    token = match.group(0)
    if "." in token:
        whole, frac = token.split(".", 1)
        replacement = f"{int(whole) + 1}.{frac}"
    else:
        replacement = str(int(token) + 1)
    return claim[:match.start()] + replacement + claim[match.end():], "numeric_shift"


def contradiction(row: dict) -> tuple[str, str]:
    numeric = mutate_number(row["claim"])
    if numeric is not None:
        return numeric
    for old, new, phenomenon in NO_DIGIT_MUTATIONS:
        if old in row["claim"]:
            return row["claim"].replace(old, new, 1), phenomenon
    raise ValueError(f"no deterministic contradiction operator for {row['case_id']} {row['finding_id']}: {row['claim']}")


def load_natural() -> list[dict]:
    unique: dict[str, dict] = {}
    for source in SOURCES:
        data = json.loads((ROOT / source).read_text(encoding="utf-8"))
        for row in data.get("rows", []):
            key = stable_key(row)
            if key in unique:
                continue
            item = {
                "id": "",
                "origin": "human_role_audit",
                "source_dataset": source,
                "case_id": row["case_id"],
                "finding_id": row["finding_id"],
                "split": split_for_case(row["case_id"]),
                "category": row.get("category", "unknown"),
                "claim": row["claim"],
                "quotes": [c["quote"] for c in row.get("citations", [])],
                "source_titles": [c.get("source_title", "") for c in row.get("citations", [])],
                "publishers": [c.get("publisher", "") for c in row.get("citations", [])],
                "gold_label": LABEL_MAP[row["human_label"]],
                "phenomenon": "natural",
                "mutation": None,
                "human_label": row["human_label"],
                "human_notes": row.get("human_notes", ""),
            }
            unique[key] = item
    return list(unique.values())



def semantic_hard_negative(item: dict, index: int) -> tuple[str, str, str]:
    """Create a Chinese semantic strengthening/role mutation that avoids numeric/metadata leakage."""
    claim = item["claim"].rstrip("。")
    replacements = [
        ("预计", "已经", "plan_to_actual", "not_entailed"),
        ("将于", "已经于", "plan_to_actual", "not_entailed"),
        ("将", "已经", "plan_to_actual", "not_entailed"),
        ("目标", "实际", "target_to_actual", "not_entailed"),
        ("可能", "必然", "uncertainty_to_certainty", "not_entailed"),
        ("反对", "支持", "stance_flip", "not_entailed"),
        ("不会全部", "全部都会", "quantifier_flip", "not_entailed"),
        ("并非全部", "全部", "quantifier_flip", "not_entailed"),
    ]
    for old, new, phenomenon, label in replacements:
        if old in claim:
            return claim.replace(old, new, 1) + "。", phenomenon, label

    if index % 3 == 0:
        return claim + "，因此这足以证明研究问题中的最终结论。", "causal_strengthening", "partially_entailed"
    if index % 3 == 1:
        return claim + "，且这一结论适用于整个预测期。", "scope_expansion", "partially_entailed"
    return claim + "，而且不存在其他可能解释。", "exclusivity_strengthening", "partially_entailed"


def build_post_boundary(natural: list[dict] | None = None) -> dict:
    natural = natural or load_natural()
    for index, item in enumerate(natural, 1):
        item["id"] = f"natural-{index:04d}"
    generated = []
    supported = [item for item in natural if item["gold_label"] == "entailed"]
    for index, item in enumerate(supported, 1):
        claim, phenomenon, label = semantic_hard_negative(item, index)
        mutated = dict(item)
        mutated.update({
            "id": f"semantic-hardneg-{index:04d}",
            "origin": "controlled_post_boundary_hard_negative",
            "claim": claim,
            "gold_label": label,
            "phenomenon": phenomenon,
            "mutation": {
                "operator": phenomenon,
                "base_id": item["id"],
                "base_claim": item["claim"],
            },
            "human_label": None,
            "human_notes": "Controlled semantic strengthening/reversal designed to avoid numeric/entity/source-metadata leakage and challenge the post-boundary semantic judge.",
        })
        generated.append(mutated)

    rows = natural + generated
    counts, phenomena, split_counts = {}, {}, {}
    for row in rows:
        counts[row["gold_label"]] = counts.get(row["gold_label"], 0) + 1
        phenomena[row["phenomenon"]] = phenomena.get(row["phenomenon"], 0) + 1
        split_counts[row["split"]] = split_counts.get(row["split"], 0) + 1
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "description": "ForecastLab post-boundary semantic entailment benchmark: natural audits plus semantic hard negatives designed to survive deterministic boundary checks.",
        "source_files": SOURCES,
        "row_count": len(rows),
        "label_counts": counts,
        "phenomenon_counts": phenomena,
        "split_counts": split_counts,
        "split_policy": "case-grouped dev/test holdout; all variants from a case stay in the same split",
        "rows": rows,
    }


def build() -> dict:
    natural = load_natural()
    for index, item in enumerate(natural, 1):
        item["id"] = f"natural-{index:04d}"
    generated = []
    attribution = []
    for item in natural:
        if item["gold_label"] != "entailed":
            continue
        row = {
            "case_id": item["case_id"],
            "finding_id": item["finding_id"],
            "claim": item["claim"],
            "citations": [{"quote": q} for q in item["quotes"]],
        }
        mutated_claim, phenomenon = contradiction(row)
        mutated = dict(item)
        mutated.update({
            "id": f"hardneg-{len(generated)+1:04d}",
            "origin": "controlled_hard_negative",
            "source_dataset": item["source_dataset"],
            "claim": mutated_claim,
            "gold_label": "not_entailed",
            "phenomenon": phenomenon,
            "mutation": {
                "operator": phenomenon,
                "base_id": item["id"],
                "base_claim": item["claim"],
            },
            "human_label": None,
            "human_notes": "Controlled contradiction generated from a human-supported base; quote is unchanged.",
        })
        generated.append(mutated)

        quote_blob = "\n".join(item["quotes"])
        publisher = next((p for p in item["publishers"] if p and p not in quote_blob and p not in item["claim"]), "")
        if publisher:
            attributed = dict(item)
            attributed.update({
                "id": f"partial-attribution-{len(attribution)+1:04d}",
                "origin": "controlled_hard_negative",
                "claim": f"{publisher}称，{item['claim']}",
                "gold_label": "partially_entailed",
                "phenomenon": "quote_external_source_attribution",
                "mutation": {
                    "operator": "quote_external_source_attribution",
                    "base_id": item["id"],
                    "base_claim": item["claim"],
                    "injected_publisher": publisher,
                },
                "human_label": None,
                "human_notes": "Publisher attribution is correct metadata but is not stated by the exact quote; strict quote-only entailment is therefore partial.",
            })
            attribution.append(attributed)

    rows = natural + generated + attribution
    counts = {}
    phenomena = {}
    split_counts = {}
    for row in rows:
        counts[row["gold_label"]] = counts.get(row["gold_label"], 0) + 1
        phenomena[row["phenomenon"]] = phenomena.get(row["phenomenon"], 0) + 1
        split_counts.setdefault(row["split"], {})
        split_counts[row["split"]][row["gold_label"]] = split_counts[row["split"]].get(row["gold_label"], 0) + 1
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "description": "ForecastLab 证据评估 exact-quote entailment benchmark: natural human-role audit pairs plus reproducible controlled contradictions.",
        "source_files": SOURCES,
        "row_count": len(rows),
        "label_counts": counts,
        "phenomenon_counts": phenomena,
        "split_counts": {name: sum(1 for row in rows if row["split"] == name) for name in ("dev", "test")},
        "split_policy": "case-grouped dev/test holdout; all variants from a case stay in the same split",
        "rows": rows,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=ROOT / "eval/benchmarks/evidence-entailment-hard-v1.json")
    p.add_argument("--post-boundary-output", type=Path, default=ROOT / "eval/benchmarks/evidence-entailment-post-boundary-v1.json")
    args = p.parse_args()
    natural = load_natural()
    result = build()
    post = build_post_boundary(natural)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.post_boundary_output.write_text(json.dumps(post, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "broad": {"row_count": result["row_count"], "label_counts": result["label_counts"], "output": str(args.output)},
        "post_boundary": {"row_count": post["row_count"], "label_counts": post["label_counts"], "output": str(args.post_boundary_output)},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
