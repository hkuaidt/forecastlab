from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
from app import config
from app.api import create_app
from app.future_only import current_question
from app.schemas import QuestionDraft, QuestionSpec, RunRecord, utcnow
from app.question_service import QuestionService
from app.storage import RunStore


def spec(**changes):
    now = utcnow()
    return dict(question='未来一年开源工具将怎样改变软件开发？', as_of=now.isoformat(),
                mode='scenario', **changes)


def test_server_owns_start_time():
    q = QuestionDraft(**spec())
    result = current_question(q)
    assert result.as_of >= q.as_of
    assert result.question == q.question


@pytest.mark.parametrize('endpoint', ['/api/questions/parse', '/api/runs', '/api/questions/analyze'])
def test_historical_cutoff_rejected_before_inference(tmp_path, monkeypatch, endpoint):
    monkeypatch.setattr(config, 'MODEL_API_KEY', 'test')
    app = create_app(tmp_path)
    q = spec(); q['as_of'] = (utcnow() - timedelta(days=3)).isoformat()
    body = q if endpoint.endswith('parse') else {'question':q}
    if endpoint.endswith('runs'): body.update(evidence_mode='import', evidence=[])
    with TestClient(app) as client:
        response = client.post(endpoint,json=body)
    assert response.status_code == 422, response.text
    assert '历史' in response.text


def test_expired_target_rejected_with_current_start(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        response = client.post('/api/runs', json={'question':spec(resolve_by=(utcnow()-timedelta(days=1)).isoformat()), 'evidence_mode':'online'})
    assert response.status_code == 422
    assert '未来' in response.text


def test_future_import_still_works(tmp_path, monkeypatch):
    import app.api as api_module
    monkeypatch.setattr(config, 'MODEL_API_KEY', 'test')
    monkeypatch.setattr(api_module, 'execute', lambda *a, **kw: None)
    with TestClient(create_app(tmp_path)) as client:
        response = client.post('/api/runs',json={'question':spec(), 'evidence_mode':'import','evidence':[]})
        assert response.status_code == 202, response.text


def test_past_run_readable_but_not_resumable(tmp_path):
    app = create_app(tmp_path)
    now=utcnow()
    q=QuestionSpec(question='过去一年开源工具会怎样影响开发？',mode='scenario',as_of=now-timedelta(days=3),resolve_by=now-timedelta(days=1))
    app.state.store.save(RunRecord(run_id='past',question=q,evidence_mode='import',status='cancelled',model='fixture'))
    with TestClient(app) as client:
        assert client.get('/api/runs/past').status_code == 200
        assert client.post('/api/runs/past/resume').status_code == 422


def test_current_analysis_retry_remains_idempotent(tmp_path):
    from app.schemas import AnalyzeQuestionRequest
    class Model:
        calls=0
        def complete(self, role, payload, schema, instructions, **kw):
            self.calls+=1
            return schema.model_validate({'proposed_spec':payload['question'],'premises':[], 'retrieval_plan':[]})
    model=Model()
    service=QuestionService(RunStore(tmp_path),model_factory=lambda **kw:model)
    request=AnalyzeQuestionRequest(question=spec(),operation_id='same-request')
    a=service.analyze(request); b=service.analyze(request)
    assert a==b and model.calls==1
