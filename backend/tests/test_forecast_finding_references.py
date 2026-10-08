from copy import deepcopy
import json
from pathlib import Path

import pytest

from app import graph
from app.schemas import Evidence, Forecast, QuestionSpec, RunRecord, SimulationStep, WorldState
from scenario_fixtures import scenario_details


def frozen_state():
    state = json.loads((Path(__file__).parent / 'fixtures' / 'forecast_context_run_0602.json').read_text())['payload']
    # The frozen model input omits this storage alias; the stage schema needs it.
    for item in state['evidence']:
        item['content_hash'] = item['snapshot_hash']
    return state


def report():
    probabilities = {'推进': .45, '局部采用': .35, '受限': .2}
    return Forecast(status='completed', conclusion='在所列条件成立时，可能形成不同采用路径。',
        probabilities=probabilities,
        supporting=[{'text': '若相应研究条件具备，该记录可作为路径的起点。', 'evidence_ids': ['F001']}],
        opposing=[{'text': '现有材料的适用范围仍有限。', 'evidence_ids': ['F002']}],
        scenario_details=scenario_details(probabilities))


def test_validated_finding_reference_finishes_report_in_one_call_and_keeps_raw_audit(tmp_path):
    state = frozen_state()
    before = deepcopy(state)
    class Model:
        calls = 0
        def complete(self, role, payload, schema, instructions):
            assert role == 'forecast'
            self.calls += 1
            return schema.model_validate(report().model_dump())
    model = Model()
    record = RunRecord(run_id='finding_alias_report', question=QuestionSpec.model_validate(state['question']),
                       evidence_mode='import', model='fixture')
    compiled = graph.build_graph(record, [], model, tmp_path, start_at='synthesize')
    result = compiled.invoke(state)
    assert model.calls == 1
    assert result['forecast']['probabilities'] == report().probabilities
    assert result['forecast']['supporting'][0]['evidence_ids'] == ['E001']
    assert result['forecast']['opposing'][0]['evidence_ids'] == ['E002']
    assert record.forecast_attempts[0].candidate.supporting[0].evidence_ids == ['F001']
    assert state == before


@pytest.mark.parametrize('kind', ['unknown', 'unvalidated', 'missing_source', 'duplicate_finding'])
def test_invalid_finding_never_becomes_a_certified_source(kind):
    state = frozen_state()
    candidate = report()
    assessment = state['evidence_assessment']
    if kind == 'unknown':
        candidate.supporting[0].evidence_ids = ['F999']
    elif kind == 'unvalidated':
        assessment['findings_validated'] = False
    elif kind == 'missing_source':
        assessment['findings'][0]['citations'][0]['evidence_id'] = 'E999'
    else:
        assessment['findings'].append(deepcopy(assessment['findings'][0]))
    evidence = [Evidence.model_validate(item) for item in state['evidence']]
    with pytest.raises(ValueError):
        graph.canonicalize_forecast_ids(candidate, evidence, WorldState.model_validate(state['world']),
            [SimulationStep.model_validate(item) for item in state['simulation']], assessment)
        graph.check_ids(candidate.supporting[0].evidence_ids, {item.id for item in evidence}, '报告证据')


def test_multi_source_finding_expands_all_sources_including_scenario_details():
    state = frozen_state()
    assessment = state['evidence_assessment']
    assessment['findings'][0]['citations'].extend(deepcopy(assessment['findings'][1]['citations']))
    candidate = report()
    candidate.scenario_details[0].evidence_ids = ['F001', 'E001']
    graph.canonicalize_forecast_ids(candidate, [Evidence.model_validate(item) for item in state['evidence']],
        WorldState.model_validate(state['world']), [], assessment)
    assert candidate.scenario_details[0].evidence_ids == ['E001', 'E002']
