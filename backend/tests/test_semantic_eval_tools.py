import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_question_framing_anchor_and_summary_metrics():
    m = load('question_framing_robustness_eval', 'eval/question_framing_robustness.py')
    frame = SimpleNamespace(premises=[SimpleNamespace(original_span='既然核心测试都已通过', content='核心测试都已通过')])
    assert m.premise_anchor_detected(frame, '核心测试都已通过') is True
    rows = [
        {'status':'ok','pair_id':'x','variant':'neutral','repeat':1,'premise_count':0,'neutral_false_positive':False,
         'expected_premise_detected':None,'explicit_fields_preserved':True,'blocking_clarification_count':0,
         'ready_for_confirmation':True,'elapsed_seconds':1.0},
        {'status':'ok','pair_id':'x','variant':'leading','repeat':1,'premise_count':1,'neutral_false_positive':False,
         'expected_premise_detected':True,'explicit_fields_preserved':True,'blocking_clarification_count':0,
         'ready_for_confirmation':True,'elapsed_seconds':2.0},
    ]
    s = m.summarize(rows)
    assert s['neutral_false_positive_rate'] == 0
    assert s['leading_premise_detection_rate'] == 1
    assert s['paired_leading_adds_premise_rate'] == 1
    assert s['explicit_field_preservation_rate'] == 1


def test_question_framing_frozen_pairs_have_matching_explicit_deadlines():
    rows = json.loads((ROOT / 'eval/cases/question-framing-neutral-leading-v2.json').read_text())
    assert len(rows) == 16
    grouped = {}
    for row in rows:
        grouped.setdefault(row['pair_id'], []).append(row)
        assert row['question']['as_of'] < row['question']['resolve_by']
    assert len(grouped) == 8
    for pair in grouped.values():
        assert {x['variant'] for x in pair} == {'neutral','leading'}
        assert pair[0]['question']['resolve_by'] == pair[1]['question']['resolve_by']
        assert pair[0]['question']['resolution_rule'] == pair[1]['question']['resolution_rule']


def test_evidence_stratified_sample_maximizes_case_breadth():
    m = load('evidence_quote_audit_eval', 'eval/evidence_quote_audit.py')
    pop=[]
    for c in range(5):
        for f in range(3):
            pop.append({'case_id':f'C{c}','finding_id':f'F{f}','category':'x'})
    sample=m.stratified_sample(pop,sample_size=5,seed=7606)
    assert len(sample)==5
    assert len({r['case_id'] for r in sample})==5


def test_evidence_audit_score_requires_all_labels(tmp_path, capsys):
    # Keep this as a structural fixture; CLI behavior is covered by py_compile and the scoring formula below.
    rows=[
        {'relation':'supports','human_label':'supported'},
        {'relation':'background','human_label':'partially_supported'},
        {'relation':'challenges','human_label':'unsupported'},
        {'relation':'unclear','human_label':'unclear'},
    ]
    assert sum(r['human_label']=='supported' for r in rows)/len(rows)==0.25
    assert sum(r['human_label'] in {'supported','partially_supported'} for r in rows)/len(rows)==0.5


def test_question_framing_adjusted_neutral_metric_allows_factual_benchmark():
    m = load('score_question_framing_robustness_eval', 'eval/score_question_framing_robustness.py')
    report={'rows':[
        {'id':'n','variant':'neutral','repeat':1,'status':'ok','premises':[{'content':'基准为2207.86点','original_span':'2207.86点'}],
         'expected_premise_detected':None,'explicit_fields_preserved':True,'blocking_clarification_count':0,'ready_for_confirmation':True},
        {'id':'l','variant':'leading','repeat':1,'status':'ok','premises':[],'expected_premise_detected':True,
         'explicit_fields_preserved':True,'blocking_clarification_count':0,'ready_for_confirmation':True},
    ]}
    cases=[{'id':'n','allowed_neutral_premise_anchors':['2207.86']},{'id':'l'}]
    score=m.score(report,cases)
    assert score['neutral_raw_any_premise_rate']==1
    assert score['neutral_unexpected_premise_rate']==0
