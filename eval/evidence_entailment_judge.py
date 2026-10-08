"""LLM semantic-entailment judge for 证据评估 exact-quote findings.

This is an evaluation/research harness first. It does not change the production
acceptance path. The judge sees only the finding claim and exact quote(s), never
publisher/title metadata or another reviewer label.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import config

JUDGE_PROMPT = """Judge whether the exact quote set directly entails the claim.
Use only the quote text. Do not use source title, publisher, URL, outside knowledge,
or likely context to rescue the claim. Cross-language translation and conservative
paraphrase are allowed only when they add no material fact, entity, date, causal role,
measurement semantics, antecedent, or provenance.

Labels:
- entailed: every material part of the claim follows directly from the quote(s).
- partially_entailed: the core proposition is supported, but the claim adds at least
  one material detail, referent, grammatical role, context, or inference not explicit
  in the quote(s).
- not_entailed: the quote does not support the material proposition or supports a
  materially different proposition.
- unclear: the isolated quote is too ambiguous to judge reliably.

Be strict about unresolved pronouns, abbreviations expanded to unseen full names,
"in" vs "by", plans vs actual events, dates/years, measurement labels, and provenance.
Return only JSON matching the schema."""


class EntailmentDecision(BaseModel):
    label: Literal["entailed", "partially_entailed", "not_entailed", "unclear"]
    confidence: float = Field(ge=0, le=1)
    unsupported_spans: list[str] = Field(default_factory=list, max_length=12)
    rationale: str = Field(max_length=1000)


def _judge_config(allow_primary: bool) -> tuple[str, str, str, bool]:
    api_key = os.getenv("ENTAILMENT_JUDGE_API_KEY", "")
    if api_key:
        base_url = os.getenv("ENTAILMENT_JUDGE_BASE_URL", config.MODEL_BASE_URL)
        model = os.getenv("ENTAILMENT_JUDGE_MODEL", config.MODEL_NAME)
        return api_key, base_url, model, False
    if not allow_primary:
        raise SystemExit(
            "未配置 ENTAILMENT_JUDGE_API_KEY。若仅做同模型 bootstrap calibration，"
            "显式加 --allow-primary-model；正式独立评测建议使用不同模型/提供商。"
        )
    if not config.MODEL_API_KEY:
        raise SystemExit("主模型 API Key 也未配置")
    return config.MODEL_API_KEY, config.MODEL_BASE_URL, config.MODEL_NAME, True


def row_quotes(row: dict) -> list[str]:
    if isinstance(row.get("quotes"), list):
        return [str(q) for q in row["quotes"]]
    return [c["quote"] for c in row.get("citations", [])]


def judge_row(client: OpenAI, model: str, row: dict) -> tuple[EntailmentDecision, dict]:
    quotes = row_quotes(row)
    payload = {
        "claim": row["claim"],
        "exact_quotes": quotes,
        "instruction": "Judge claim entailment from exact_quotes only.",
    }
    kwargs = dict(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    JUDGE_PROMPT
                    + "\nJSON Schema: "
                    + json.dumps(EntailmentDecision.model_json_schema(), ensure_ascii=False)
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=1200,
    )
    if "qwen3.8-flash" in model:
        kwargs["extra_body"] = {"enable_thinking": False}
    elif "deepseek" in model.lower():
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    started = time.monotonic()
    response = client.chat.completions.create(**kwargs)
    elapsed = time.monotonic() - started
    try:
        decision = EntailmentDecision.model_validate_json(response.choices[0].message.content or "")
    except (ValidationError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"judge JSON invalid: {exc}") from exc
    usage = getattr(response, "usage", None)
    meta = {
        "elapsed_seconds": elapsed,
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "response_model": getattr(response, "model", None) or model,
    }
    return decision, meta


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("audit", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--allow-primary-model", action="store_true")
    p.add_argument("--limit", type=int)
    args = p.parse_args()

    data = json.loads(args.audit.read_text(encoding="utf-8"))
    rows = list(data.get("rows", []))
    if args.limit:
        rows = rows[: args.limit]

    api_key, base_url, model, same_as_generator = _judge_config(args.allow_primary_model)
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=45, max_retries=1)

    judged = []
    totals = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "elapsed_seconds": 0.0}
    for i, row in enumerate(rows, 1):
        decision, meta = judge_row(client, model, row)
        item = json.loads(json.dumps(row, ensure_ascii=False))
        item["judge"] = {**decision.model_dump(), **meta}
        judged.append(item)
        totals["calls"] += 1
        totals["prompt_tokens"] += meta["prompt_tokens"]
        totals["completion_tokens"] += meta["completion_tokens"]
        totals["elapsed_seconds"] += meta["elapsed_seconds"]
        print(f"[{i}/{len(rows)}] {row['case_id']} {row['finding_id']} -> {decision.label} {decision.confidence:.2f}")

    out = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_audit": str(args.audit),
        "judge_model": model,
        "same_as_generator_model": same_as_generator,
        "independent_model": not same_as_generator,
        "judge_prompt_version": "entailment-v1",
        "rows": judged,
        "usage": totals,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"judge_model": model, "same_as_generator_model": same_as_generator, "usage": totals}, ensure_ascii=False))


if __name__ == "__main__":
    main()
