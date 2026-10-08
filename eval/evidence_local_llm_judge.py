"""Local independent LLM entailment judge using a Hugging Face causal LM.

Research-only. This lets ForecastLab evaluate a judge from a different model family
without requiring a second commercial API key. torch/transformers remain optional
research dependencies and are not added to the production backend.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

DEFAULT_MODEL = os.getenv("ENTAILMENT_LOCAL_LLM_MODEL", "Qwen/Qwen2.5-1.5B-Instruct")

PROMPT = """你是严格的文本蕴含判定器。只能依据 exact_quotes 判断 claim，不得使用来源标题、publisher、URL、常识或外部上下文补全。

标签：
- entailed：claim 的所有实质内容都由 exact_quotes 直接蕴含。
- partially_entailed：核心事实被支持，但 claim 添加了 quote 未明确表达的指代、范围、语义角色、因果、时间、状态或上下文。
- not_entailed：claim 的实质内容与 quote 不一致、发生反转或明显无法推出。
- unclear：仅凭 quote 无法可靠判断。

尤其严格检查：
1. 计划/预计/目标 ≠ 已经发生；
2. 可能 ≠ 必然；
3. hosted in ≠ hosted by；
4. 代词 They/It/This 的先行词不可凭外部上下文补；
5. title defence 等短语不可随意补“新赛季”等上下文；
6. 不得把 source metadata 当 quote 内容。

只输出一个 JSON 对象，字段为 label、confidence、unsupported_spans、rationale。
"""


class Decision(BaseModel):
    label: Literal["entailed", "partially_entailed", "not_entailed", "unclear"]
    confidence: float = Field(ge=0, le=1)
    unsupported_spans: list[str | dict[str, int]] = Field(default_factory=list)
    rationale: str = Field(default="", max_length=1000)


def row_quotes(row: dict) -> list[str]:
    if isinstance(row.get("quotes"), list):
        return [str(q) for q in row["quotes"]]
    return [c["quote"] for c in row.get("citations", [])]


def extract_json(text: str) -> dict:
    cleaned = text.strip()
    cleaned = re.sub(r"^\s*\x60\x60\x60(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*\x60\x60\x60\s*$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in model output")
    candidate = cleaned[start:end+1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # Small local models sometimes emit: "rationale": "text".}
        # Repair syntax punctuation only; never change semantic content.
        repaired = re.sub(r'"\s*\.\s*([,}])', r'"\1', candidate)
        repaired = re.sub(r",\s*}", "}", repaired)
        return json.loads(repaired)


def load_runtime(model_name: str):
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        raise SystemExit("本地 LLM Judge 需要可选研究依赖 torch + transformers") from exc
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    dtype = torch.float16 if torch.cuda.is_available() else torch.float32
    model = AutoModelForCausalLM.from_pretrained(model_name, torch_dtype=dtype)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    model.eval()
    model.generation_config.do_sample = False
    model.generation_config.temperature = None
    model.generation_config.top_p = None
    model.generation_config.top_k = None
    return torch, tokenizer, model, device


def judge_row(torch, tokenizer, model, device, row: dict, max_new_tokens: int) -> tuple[Decision, float]:
    payload = {"claim": row["claim"], "exact_quotes": row_quotes(row)}
    messages = [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    prompt_text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    encoded = tokenizer(prompt_text, return_tensors="pt", truncation=True, max_length=1536)
    encoded = {k: v.to(device) for k, v in encoded.items()}
    started = time.monotonic()
    with torch.no_grad():
        output = model.generate(
            **encoded,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            pad_token_id=tokenizer.eos_token_id,
        )
    elapsed = time.monotonic() - started
    generated = output[0, encoded["input_ids"].shape[1]:]
    raw = tokenizer.decode(generated, skip_special_tokens=True)
    try:
        return Decision.model_validate(extract_json(raw)), elapsed
    except (ValidationError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"invalid local judge output: {raw[:500]}") from exc


def make_output(args, judged, total_elapsed: float, device) -> dict:
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_benchmark": str(args.benchmark),
        "judge_model": args.model,
        "same_as_generator_model": False,
        "independent_model": True,
        "judge_type": "local_causal_llm",
        "device": str(device),
        "total_elapsed_seconds": total_elapsed,
        "rows": judged,
    }


def checkpoint(args, judged, total_elapsed: float, device) -> dict:
    out = make_output(args, judged, total_elapsed, device)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("benchmark", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--limit", type=int)
    p.add_argument("--max-new-tokens", type=int, default=192)
    p.add_argument("--resume", action="store_true")
    args = p.parse_args()

    data = json.loads(args.benchmark.read_text(encoding="utf-8"))
    rows = list(data.get("rows", []))
    if args.limit:
        rows = rows[:args.limit]

    torch, tokenizer, model, device = load_runtime(args.model)
    judged, total_elapsed = [], 0.0
    if args.resume and args.output.exists():
        previous = json.loads(args.output.read_text(encoding="utf-8"))
        judged = list(previous.get("rows", []))
        total_elapsed = float(previous.get("total_elapsed_seconds", 0))
    done = {row.get("id") for row in judged}

    for i, row in enumerate(rows, 1):
        if row.get("id") in done:
            continue
        error = None
        try:
            decision, elapsed = judge_row(torch, tokenizer, model, device, row, args.max_new_tokens)
        except Exception as exc:
            elapsed = 0.0
            error = f"{type(exc).__name__}: {str(exc)[:300]}"
            decision = Decision(
                label="unclear",
                confidence=0.0,
                unsupported_spans=[],
                rationale="Judge output could not be parsed; fail-closed as unclear.",
            )
        total_elapsed += elapsed
        item = json.loads(json.dumps(row, ensure_ascii=False))
        item["judge"] = {**decision.model_dump(), "elapsed_seconds": elapsed}
        if error:
            item["judge"]["error"] = error
        judged.append(item)
        checkpoint(args, judged, total_elapsed, device)
        row_name = row.get("id") or row.get("case_id") or str(i)
        print(f"[{i}/{len(rows)}] {row_name} -> {decision.label} {decision.confidence:.2f}", flush=True)

    checkpoint(args, judged, total_elapsed, device)
    print(json.dumps(
        {"rows": len(judged), "model": args.model, "device": str(device), "elapsed": total_elapsed},
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
