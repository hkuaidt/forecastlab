from copy import deepcopy
from datetime import datetime, timezone
import pytest
from app.forecast_wire import DefinitionFirstForecast,to_public_forecast
from app.forecast_specificity import validate_specificity
from app.schemas import QuestionSpec,Forecast
from app import graph
from test_definition_first_forecast import response
from test_scenario_end_states import inputs


def question():
    return QuestionSpec(question='到2027年12月31日，数学研究的证明检验会有什么具体变化？',mode='scenario',as_of=datetime(2026,10,9,tzinfo=timezone.utc))


def test_specific_predictions_keep_probabilities_and_all_details():
    body=response();report=to_public_forecast(DefinitionFirstForecast.model_validate(body))
    validate_specificity(report,question())
    assert [p.model_dump() for p in report.predictions]==[p.model_dump() for p in DefinitionFirstForecast.model_validate(body).predictions]
    assert list(report.probabilities.values())==[.43,.34,.23]


@pytest.mark.parametrize('defect',['missing','generic_names','past','after_horizon','duplicate_id','duplicate_action','unknown_scenario','empty_refs','missing_coverage'])
def test_generic_or_unverifiable_predictions_are_rejected(defect):
    report=to_public_forecast(DefinitionFirstForecast.model_validate(response()))
    if defect=='missing':report.predictions=[]
    elif defect=='generic_names':report.probabilities={'显著影响':.5,'部分影响':.3,'无影响':.2}
    elif defect=='past':report.predictions[0].by_date='2025-01-01'
    elif defect=='after_horizon':report.predictions[0].by_date='2028-01-01'
    elif defect=='duplicate_id':report.predictions[1].id=report.predictions[0].id
    elif defect=='duplicate_action':report.predictions[1].action=report.predictions[0].action
    elif defect=='unknown_scenario':report.predictions[0].scenario_names=['不存在的结果']
    elif defect=='empty_refs':report.predictions[0].evidence_ids=[]
    elif defect=='missing_coverage':report.predictions[0].scenario_names=report.predictions[1].scenario_names
    with pytest.raises(ValueError):validate_specificity(report,question())


def test_prediction_references_cannot_invent_external_evidence():
    report=to_public_forecast(DefinitionFirstForecast.model_validate(response()))
    report.predictions[0].evidence_ids=['E999']
    _,q,e,w,s,r=inputs()
    with pytest.raises(ValueError,match='E999'):
        graph.validate_forecast(report,q,e,w,s,r)


def test_old_public_reports_still_load_without_invented_detail():
    report=Forecast(status='completed',conclusion='旧结论',probabilities={'是':.5,'否':.5})
    assert report.predictions==[]


def test_completed_report_refinement_preserves_parent_and_upstream(tmp_path):
    from test_report_failure_recovery import saved_report,GOOD
    from app.report_repair import prepare_report_repair,report_repair_info
    parent=saved_report(tmp_path)
    parent.status='completed';parent.stage='done';parent.forecast=Forecast.model_validate(GOOD)
    parent.stage_outputs['forecast']={'forecast':parent.forecast.model_dump(mode='json')}
    before=parent.model_dump_json()
    assert not report_repair_info(parent)['available']
    child=prepare_report_repair(parent,tmp_path,refine=True)
    assert parent.model_dump_json()==before
    assert child.parent_run_id==parent.run_id and child.forecast is None
    assert child.report_repair_audit['scope']=='forecast_refinement'
    assert child.stage=='forecast' and set(child.stage_outputs)=={'question','evidence','world','simulation','review'}
    assert child.evidence_assessment.findings==parent.evidence_assessment.findings
    assert child.usage['calls']==0


def test_refinement_revalidates_sources_before_scheduling(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.api import create_app
    from app.storage import RunStore
    from app import config
    from test_report_failure_recovery import saved_report,GOOD
    parent=saved_report(tmp_path)
    parent.status='completed';parent.forecast=Forecast.model_validate(GOOD)
    parent.stage_outputs['evidence']['evidence_assessment']['findings'][0]['citations'][0]['quote']='forged quote'
    store=RunStore(tmp_path);store.save(parent)
    monkeypatch.setattr(config,'MODEL_API_KEY','fixture')
    monkeypatch.setattr('app.api.execute',lambda *args,**kw: pytest.fail('Must reject before model'))
    with TestClient(create_app(tmp_path)) as client:
        result=client.post(f'/api/runs/{parent.run_id}/refine-report')
        assert result.status_code==422
        assert len(client.get('/api/runs').json())==1


def test_export_keeps_specific_predictions_and_escapes_content(tmp_path):
    from app.reporting import report_html
    from app.schemas import RunRecord
    report=to_public_forecast(DefinitionFirstForecast.model_validate(response()))
    report.predictions[0].action='<script>not executable</script>'
    record=RunRecord(run_id='specific-export',evidence_mode='import',question=question(),model='fixture',forecast=report)
    html=report_html(record)
    assert '具体预测与验证节点' in html and '什么会推翻预测' in html
    assert '&lt;script&gt;not executable&lt;/script&gt;' in html
    assert '<script>not executable</script>' not in html



def test_retry_reports_all_specificity_defects_together():
    report=to_public_forecast(DefinitionFirstForecast.model_validate(response()))
    report.probabilities={'显著影响':.5,'无影响':.5}
    report.predictions[0].evidence_ids=[]
    report.predictions[1].action=report.predictions[0].action
    with pytest.raises(ValueError) as err:validate_specificity(report,question())
    assert all(text in str(err.value) for text in ['情景名称过于笼统','缺少可追溯','重复同一主体行动','不存在的情景'])


def test_private_slot_references_resolve_without_changing_raw_candidate():
    body=response()
    body['predictions'][0]['scenario_names']=['outcome_1']
    wire=DefinitionFirstForecast.model_validate(body)
    before=wire.model_dump()
    report=to_public_forecast(wire)
    assert report.predictions[0].scenario_names==[body['terminal_definitions'][0]['name']]
    assert wire.model_dump()==before
    validate_specificity(report,question())


@pytest.mark.parametrize('name',['显著提高证明检验效率','部分改善成果采用','AI工具未显著提高科研效率'])
def test_degree_synonyms_do_not_replace_observable_scenarios(name):
    report=to_public_forecast(DefinitionFirstForecast.model_validate(response()))
    old=next(iter(report.probabilities))
    report.probabilities[name]=report.probabilities.pop(old)
    report.predictions[0].scenario_names=[name]
    with pytest.raises(ValueError,match='情景名称过于笼统'):validate_specificity(report,question())


def test_concise_chinese_checks_do_not_force_another_model_call():
    body=response()
    body['predictions'][0]['verification']='核对版本与复核记录'
    body['predictions'][0]['falsifier']='已出现正式采用流程'
    report=to_public_forecast(DefinitionFirstForecast.model_validate(body))
    validate_specificity(report,question())
