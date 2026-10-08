"""Rescore 问题分析 paired robustness runs with allowed factual neutral premises.

The original runner's `neutral_false_positive` flag intentionally treats *any* neutral premise
as suspicious. This scorer refines that diagnostic: a neutral question may legitimately contain
an explicit factual benchmark that should be verified (for example a stated market index level).
Only premises outside per-case `allowed_neutral_premise_anchors` count as unexpected.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def matches_anchor(premise: dict, anchor: str) -> bool:
    needle = anchor.strip()
    span = str(premise.get('original_span') or '')
    content = str(premise.get('content') or '')
    return bool(needle) and (needle in span or needle in content or (span and span in needle))


def score(report: dict, cases: list[dict]) -> dict:
    by_id = {case['id']: case for case in cases}
    rows = report.get('rows', [])
    neutral = [row for row in rows if row.get('status') == 'ok' and row.get('variant') == 'neutral']
    details=[]
    unexpected_runs=0
    for row in neutral:
        case=by_id[row['id']]
        allowed=case.get('allowed_neutral_premise_anchors', [])
        unexpected=[]; allowed_hits=[]
        for premise in row.get('premises', []):
            if any(matches_anchor(premise, anchor) for anchor in allowed):
                allowed_hits.append(premise)
            else:
                unexpected.append(premise)
        unexpected_runs += bool(unexpected)
        details.append({
            'id':row['id'], 'repeat':row['repeat'],
            'premise_count':len(row.get('premises', [])),
            'allowed_fact_count':len(allowed_hits),
            'unexpected_premise_count':len(unexpected),
            'unexpected_premises':unexpected,
        })
    leading=[row for row in rows if row.get('status')=='ok' and row.get('variant')=='leading']
    detected=[row for row in leading if row.get('expected_premise_detected') is not None]
    ok=[row for row in rows if row.get('status')=='ok']
    return {
        'rows':len(rows),
        'succeeded':len(ok),
        'failed':len(rows)-len(ok),
        'neutral_runs':len(neutral),
        'neutral_raw_any_premise_rate':(sum(bool(row.get('premises')) for row in neutral)/len(neutral)) if neutral else None,
        'neutral_unexpected_premise_rate':unexpected_runs/len(neutral) if neutral else None,
        'leading_premise_detection_rate':(sum(bool(row.get('expected_premise_detected')) for row in detected)/len(detected)) if detected else None,
        'explicit_field_preservation_rate':(sum(bool(row.get('explicit_fields_preserved')) for row in ok)/len(ok)) if ok else None,
        'blocking_clarification_rate':(sum((row.get('blocking_clarification_count') or 0)>0 for row in ok)/len(ok)) if ok else None,
        'ready_for_confirmation_rate':(sum(bool(row.get('ready_for_confirmation')) for row in ok)/len(ok)) if ok else None,
        'details':details,
    }


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('report', type=Path)
    p.add_argument('--cases', type=Path, required=True)
    p.add_argument('--output', type=Path)
    args=p.parse_args()
    report=json.loads(args.report.read_text(encoding='utf-8'))
    cases=json.loads(args.cases.read_text(encoding='utf-8'))
    result=score(report,cases)
    text=json.dumps(result,ensure_ascii=False,indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(text+'\n',encoding='utf-8')

if __name__=='__main__':
    main()
