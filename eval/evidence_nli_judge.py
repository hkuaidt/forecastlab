"""Optional multilingual NLI baseline for 证据评估 quote entailment.

This is research-only and intentionally uses lazy imports so core ForecastLab/CI
does not depend on torch/transformers. Install those packages only in the
evaluation environment when running this script.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path


DEFAULT_MODEL = os.getenv(
    "ENTAILMENT_NLI_MODEL",
    "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli",
)


def _label_kind(label: str) -> str:
    value = label.casefold().replace("_", " ").replace("-", " ")
    if "entail" in value:
        return "entailment"
    if "contrad" in value:
        return "contradiction"
    if "neutral" in value:
        return "neutral"
    return value.strip()


def load_runtime(model_name: str):
    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError as exc:
        raise SystemExit(
            "NLI baseline 需要可选研究依赖 torch + transformers；"
            "请在独立评测环境安装，不要加入生产运行时依赖。"
        ) from exc
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSequenceClassification.from_pretrained(model_name)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    model.eval()
    return torch, tokenizer, model, device


def row_quotes(row: dict) -> list[str]:
    if isinstance(row.get("quotes"), list):
        return [str(q) for q in row["quotes"]]
    return [c["quote"] for c in row.get("citations", [])]


def judge_pair(torch, tokenizer, model, device, premise: str, hypothesis: str) -> dict:
    encoded = tokenizer(
        premise,
        hypothesis,
        return_tensors="pt",
        truncation=True,
        max_length=512,
    )
    encoded = {key: value.to(device) for key, value in encoded.items()}
    with torch.no_grad():
        logits = model(**encoded).logits[0]
        probs = torch.softmax(logits, dim=-1).tolist()

    id2label = {int(k): v for k, v in model.config.id2label.items()}
    scores = {_label_kind(id2label[i]): float(probs[i]) for i in range(len(probs))}
    entail = scores.get("entailment", 0.0)
    neutral = scores.get("neutral", 0.0)
    contradiction = scores.get("contradiction", 0.0)
    if entail >= max(neutral, contradiction):
        label = "entailed"
    elif contradiction > neutral:
        label = "not_entailed"
    else:
        label = "unclear"
    return {
        "label": label,
        "confidence": entail,
        "entailment_probability": entail,
        "neutral_probability": neutral,
        "contradiction_probability": contradiction,
        "unsupported_spans": [],
        "rationale": "Multilingual NLI baseline; probabilities come from entailment/neutral/contradiction logits.",
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("audit", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--limit", type=int)
    args = p.parse_args()

    data = json.loads(args.audit.read_text(encoding="utf-8"))
    rows = list(data.get("rows", []))
    if args.limit:
        rows = rows[: args.limit]

    torch, tokenizer, model, device = load_runtime(args.model)
    judged = []
    for i, row in enumerate(rows, 1):
        quote_text = "\n".join(row_quotes(row))
        decision = judge_pair(torch, tokenizer, model, device, quote_text, row["claim"])
        item = json.loads(json.dumps(row, ensure_ascii=False))
        item["judge"] = decision
        judged.append(item)
        print(
            f"[{i}/{len(rows)}] {row['case_id']} {row['finding_id']} "
            f"-> {decision['label']} entail={decision['entailment_probability']:.4f}"
        )

    out = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_audit": str(args.audit),
        "judge_model": args.model,
        "same_as_generator_model": False,
        "independent_model": True,
        "judge_type": "multilingual_nli",
        "device": str(device),
        "rows": judged,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
