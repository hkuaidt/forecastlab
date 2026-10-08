import importlib.util
from pathlib import Path
from copy import deepcopy
from fastapi.testclient import TestClient
from app import config
from app.api import create_app


def case(client):
    examples = client.get("/api/examples").json()
    assert "question_evidence_demo" in examples, "fixed classroom case not available"
    return examples["question_evidence_demo"]


def analyze_and_confirm(client, condition=False):
    c = case(client)
    first = client.post("/api/questions/analyze", json={"question": c["question"], "demo_case_id": c["case_id"]})
    assert first.status_code == 200, first.text
    frame = first.json()
    assert frame["status"] == "needs_clarification"
    second = client.post("/api/questions/analyze", json={"question": frame["proposed_spec"], "demo_case_id": c["case_id"],
        "draft_id": frame["draft_id"], "expected_revision": frame["revision"],
        "answers": [{"clarification_id": frame["clarifications"][0]["id"], "answer": c["allowed_answers"][0]}]})
    assert second.status_code == 200, second.text
    frame = second.json()
    assert frame["status"] == "ready_for_confirmation"
    confirm = client.post(f"/api/questions/{frame['draft_id']}/confirm", json={"expected_revision": frame["revision"],
        "decisions": [{"premise_id": p["id"], "user_review": "retained", "treatment": "scenario_condition" if condition else "to_verify"} for p in frame["premises"]]})
    assert confirm.status_code == 200, confirm.text
    return confirm.json()


def test_fixed_case_walks_through_confirm_and_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MODEL_API_KEY", "")
    with TestClient(create_app(tmp_path)) as client:
        confirmed = analyze_and_confirm(client)
        response = client.post("/api/runs", json={"confirmation_id": confirmed["confirmation_id"], "evidence_mode": "demo"})
        assert response.status_code == 202, response.text
        run = client.get(f"/api/runs/{response.json()['run_id']}").json()
        assert run["demo"] is True and run["question_origin"] == "confirmed"
        assert run["status"] == "completed", run["errors"]
        assert len(run["evidence_assessment"]["findings"]) >= 2
        assert run["evidence_assessment"]["rejected_findings"] == []
        quality = run["evidence_assessment"]["quality_profile"]
        assert quality["source_count"] == len(run["evidence"])
        assert quality["validated_finding_count"] == len(run["evidence_assessment"]["findings"])
        assert quality["search_failure_count"] == 0
        assert all(e["date_status"] == "synthetic" for e in run["evidence"])
        assert run["usage"]["calls"] == 0
        assert run["question_framing"]["analysis_record"]["validation_mode"] == "fixture"


def test_demo_rejects_arbitrary_question(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MODEL_API_KEY", "")
    with TestClient(create_app(tmp_path)) as client:
        c = case(client); q = {**c["question"], "question": "这是任意用户自己的问题，不能返回固定答案。"}
        response = client.post("/api/questions/analyze", json={"question": q, "demo_case_id": c["case_id"]})
        assert response.status_code == 422
        assert client.post("/api/questions/analyze", json={"question": q}).status_code == 503


def test_changed_demo_condition_creates_new_run_without_mutating_parent(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        confirmed = analyze_and_confirm(client)
        first_id = client.post("/api/runs", json={"confirmation_id": confirmed["confirmation_id"], "evidence_mode": "demo"}).json()["run_id"]
        before = app.state.store.get(first_id).model_dump()
        frame = confirmed["framing"]
        revised = client.post("/api/questions/analyze", json={"question": frame["proposed_spec"], "demo_case_id": frame["demo_case_id"],
            "draft_id": frame["draft_id"], "expected_revision": frame["revision"]}).json()
        next_confirm = client.post(f"/api/questions/{frame['draft_id']}/confirm", json={"expected_revision": revised["revision"],
            "decisions": [{"premise_id": p["id"], "user_review": "retained", "treatment": "scenario_condition"} for p in revised["premises"]]}).json()
        second = client.post("/api/runs", json={"confirmation_id": next_confirm["confirmation_id"], "evidence_mode": "demo", "parent_run_id": first_id})
        assert second.status_code == 202, second.text
        new = app.state.store.get(second.json()["run_id"])
        assert new.premise_assumption_map
        assert before == app.state.store.get(first_id).model_dump()


def evaluator():
    path = Path(__file__).resolve().parents[2] / "eval/question_evidence.py"
    assert path.exists(), "honest evaluation harness not implemented"
    spec = importlib.util.spec_from_file_location("question_evidence_eval", path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def test_live_mode_is_never_implicit(tmp_path, monkeypatch):
    e = evaluator()
    with TestClient(create_app(tmp_path / "app")) as client:
        c = case(client)
    report = e.run_cases([{"id": "classroom", "question": c["question"], "demo_case_id": c["case_id"]}], data_dir=tmp_path / "eval")
    assert report["validation_mode"] == "fixture" and report["semantic_review"] == "not_performed"
    monkeypatch.setattr(config, "MODEL_API_KEY", "")
    live = e.run_cases([{"id": "live", "question": c["question"]}], mode="live", data_dir=tmp_path / "live")
    assert live["cases"][0]["status"] == "not_run"


def test_eval_keeps_failed_cases(tmp_path):
    e = evaluator()
    with TestClient(create_app(tmp_path / "app")) as client:
        c = case(client)
    cases = [{"id": "known", "question": c["question"], "demo_case_id": c["case_id"]},
             {"id": "unknown", "question": {**c["question"], "question": "一个不属于固定案例的其他研究问题"}, "demo_case_id": c["case_id"]}]
    result = e.run_cases(cases, data_dir=tmp_path / "eval")
    assert len(result["cases"]) == 2
    assert result["cases"][1]["status"] == "failed"
    assert all(row["semantic_review"] == "not_performed" for row in result["cases"])


def test_named_demo_keeps_legacy_requests_replayable(tmp_path):
    from app.schemas import AnalysisRecord
    with TestClient(create_app(tmp_path)) as client:
        examples = client.get('/api/examples').json()
        assert 'question_evidence_demo' in examples
        current = examples['question_evidence_demo']
        assert current['case_id'] == 'question-evidence-demo'
        assert examples['agent12_demo']['case_id'] == 'agent12-release'
        payload = {'question': current['question'], 'demo_case_id': 'agent12-release',
                   'operation_id': 'legacy-demo-operation'}
        first = client.post('/api/questions/analyze', json=payload)
        assert first.status_code == 200, first.text
        assert client.post('/api/questions/analyze', json=payload).json() == first.json()
        frame = first.json()
        revised = client.post('/api/questions/analyze', json={
            'question': frame['proposed_spec'], 'draft_id': frame['draft_id'],
            'expected_revision': frame['revision'], 'demo_case_id': current['case_id'],
            'answers': [{'clarification_id': frame['clarifications'][0]['id'],
                         'answer': current['allowed_answers'][0]}]})
        assert revised.status_code == 200, revised.text
        ready = revised.json()
        confirmed = client.post(f"/api/questions/{ready['draft_id']}/confirm", json={
            'expected_revision': ready['revision'],
            'decisions': [{'premise_id': p['id'], 'user_review': 'retained'} for p in ready['premises']]})
        assert confirmed.status_code == 200, confirmed.text
        started = client.post('/api/runs', json={'confirmation_id': confirmed.json()['confirmation_id'], 'evidence_mode': 'demo'})
        assert started.status_code == 202, started.text
        run = client.get('/api/runs/' + started.json()['run_id']).json()
        assert run['status'] == 'completed', run['errors']
        assert run['usage']['calls'] == 0
    assert AnalysisRecord().prompt_version == 'question-evidence-v1'
    assert AnalysisRecord(prompt_version='agent12-v1').prompt_version == 'agent12-v1'
