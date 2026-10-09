from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from app import sources as S
from app.source_fetch import _PageText, extract_page_dates
from app.schemas import QuestionSpec, RetrievalTask, AssessmentCandidate, ImportedEvidence
from app.agents.evidence import validate_findings, validate_causal_hypotheses, make_evidence_context, explicit_dates
NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)


def test_recent_first_and_bounded_expansion(tmp_path, monkeypatch):
    calls=[]
    def search(query, *, days, as_of):
        calls.append(days)
        return [{'url':f'https://example.org/{days}', 'raw_content':f'Actual record {days}', 'score':.8,
                 'published_at': (NOW-timedelta(days=10 if days==90 else 200)).isoformat()}]
    monkeypatch.setattr(S.config,'BRAVE_SEARCH_API_KEY','')
    monkeypatch.setattr(S.config,'TAVILY_API_KEY','test')
    monkeypatch.setattr(S,'utcnow',lambda:NOW)
    monkeypatch.setattr(S,'_search_one',search)
    result=S.retrieve_evidence(QuestionSpec(question='research actions',mode='scenario',as_of=NOW),[RetrievalTask(id='R001',purpose='background',query='research')],tmp_path)
    assert calls == [90,365]
    assert [e.recency_role for e in result.evidence] == ['recent','background']
    assert result.retrieval_log[0].search_windows_days == [90,365]


def test_old_republished_body_is_not_current(tmp_path, monkeypatch):
    monkeypatch.setattr(S.config,'BRAVE_SEARCH_API_KEY','test')
    monkeypatch.setattr(S,'utcnow',lambda:NOW)
    monkeypatch.setattr(S,'_search_one',lambda q, **kw: [{'url':'https://example.org/old','content':'proof research','score':1,'page_age':'2026-10-08'}])
    async def fetch(urls):
        return [('proof old article body', {'final_url':urls[0], 'published_at':'2025-05-12', 'updated_at':'2026-10-08'})]
    monkeypatch.setattr('app.source_fetch.fetch_selected_bodies',fetch)
    result=S.retrieve_evidence(QuestionSpec(question='proof research',mode='scenario',as_of=NOW),[RetrievalTask(id='R001',purpose='background',query='proof')],tmp_path)
    assert not result.evidence
    assert any('超过一年' in x['reason'] for x in result.exclusions)


def test_publication_and_crawl_dates_not_conflated():
    html='<head><meta property="article:published_time" content="2025-05-12"><meta property="article:modified_time" content="2026-10-08"></head>'
    p=_PageText(); p.feed(html)
    assert p.dates == {'published_at':'2025-05-12','updated_at':'2026-10-08'}
    assert extract_page_dates('<script type="application/ld+json">{"@type":"Organization","datePublished":"2026-10-01"}</script>', 'Copyright 2026-10-09') == {}
    assert extract_page_dates('<script type="application/ld+json">{"@graph":[{"@type":"NewsArticle","datePublished":"2026-10-01"}]}</script>', '')['published_at'] == '2026-10-01'
    assert extract_page_dates('', '发布时间：2025-05-12 10:20')['published_at']=='2025-05-12 10:20'


def test_undated_result_stays_unknown_despite_recent_search(tmp_path, monkeypatch):
    monkeypatch.setattr(S.config,'BRAVE_SEARCH_API_KEY','')
    monkeypatch.setattr(S.config,'TAVILY_API_KEY','test')
    monkeypatch.setattr(S,'utcnow',lambda:NOW)
    monkeypatch.setattr(S,'_search_one',lambda q, **kw: [{'url':f'https://example.org/{i}','raw_content':f'proof {i}','page_age':'2026-10-08','score':.9} for i in range(3)])
    result=S.retrieve_evidence(QuestionSpec(question='proof research',mode='scenario',as_of=NOW),[RetrievalTask(id='R001',purpose='background',query='proof')],tmp_path)
    assert result.retrieval_log[0].search_windows_days==[90]
    assert all(e.published_at is None and e.recency_role=='date_unknown' and e.event_at is None for e in result.evidence)


@pytest.mark.parametrize('text,expected',[('On October 8, 2026, the team released it.','2026-10-08'),('8 Oct 2026','2026-10-08'),('2026年10月8日发布','2026-10-08'),('2026-02-30',None),('2026年10月',None)])
def test_event_date_precision(text,expected):
    assert explicit_dates(text)==({expected} if expected else set())


def test_event_time_must_come_from_its_own_valid_quote(tmp_path):
    q=QuestionSpec(question='future research',mode='scenario',as_of=NOW)
    text='2026年10月1日团队发布了工具。'
    e=S.import_evidence([ImportedEvidence(file_id='source',title='test',excerpt=text)],q,tmp_path).evidence[0]
    citation={'evidence_id':e.id,'snapshot_hash':e.snapshot_hash,'paragraph_id':e.passages[0].paragraph_id,'quote':text}
    candidate=AssessmentCandidate(summary='',findings=[{'claim':text,'relation':'background','citations':[citation], 'event_time':{'date':'2026-10-08','date_quote':text,'status':'observed'}}])
    findings,rejected=validate_findings(candidate,None,[e],{e.id:e.passages},NOW)
    assert len(findings)==1 and findings[0].event_time is None and rejected
    candidate.findings[0].event_time.date='2026-10-01'
    findings,rejected=validate_findings(candidate,None,[e],{e.id:e.passages},NOW)
    assert findings[0].event_time.date=='2026-10-01' and not rejected


def test_causal_links_cannot_survive_missing_or_reversed_endpoints():
    candidate=AssessmentCandidate(summary='',causal_hypotheses=[{'from_finding_id':'F001','to_finding_id':'F002','mechanism':'若工具降低成本则促进采用','alternative':'也可能是共同资助','verification':'比较采用前后的独立记录'}])
    findings=[SimpleNamespace(id='F001',event_time=SimpleNamespace(date='2026-10-02',basis='event_quote')),SimpleNamespace(id='F002',event_time=SimpleNamespace(date='2026-10-01',basis='event_quote'))]
    assert not validate_causal_hypotheses(candidate,findings)[0]
    findings[0].event_time.date='2026-09-30'
    assert len(validate_causal_hypotheses(candidate,findings)[0])==1
    assert not validate_causal_hypotheses(candidate,findings[:1])[0]



def test_missing_event_date_uses_article_publication_with_explicit_label(tmp_path):
    q=QuestionSpec(question='future research',mode='scenario',as_of=NOW)
    text='The team released a proof verification tool.'
    e=S.import_evidence([ImportedEvidence(file_id='source',title='test',excerpt=text,published_at=NOW-timedelta(days=3))],q,tmp_path).evidence[0]
    c={'evidence_id':e.id,'snapshot_hash':e.snapshot_hash,'paragraph_id':e.passages[0].paragraph_id,'quote':text}
    candidate=AssessmentCandidate(summary='',findings=[{'claim':text,'relation':'background','citations':[c]}])
    findings,rejected=validate_findings(candidate,None,[e],{e.id:e.passages},NOW)
    assert not rejected
    time=findings[0].event_time
    assert time.date=='2026-10-06' and time.basis=='publication_date' and time.source_evidence_id==e.id
    assert time.status=='unknown' and time.date_quote==''
    e.published_at=None
    assert validate_findings(candidate,None,[e],{e.id:e.passages},NOW)[0][0].event_time is None


def test_report_order_is_not_event_order():
    candidate=AssessmentCandidate(summary='',causal_hypotheses=[{'from_finding_id':'F001','to_finding_id':'F002','mechanism':'conditional mechanism','alternative':'common cause','verification':'find event records'}])
    findings=[SimpleNamespace(id='F001',event_time=SimpleNamespace(date='2026-10-01',basis='event_quote')),SimpleNamespace(id='F002',event_time=SimpleNamespace(date='2026-10-02',basis='publication_date'))]
    kept,rejected=validate_causal_hypotheses(candidate,findings)
    assert kept[0].chronology_basis=='report_order' and not rejected


@pytest.mark.parametrize('text',['优先检索最近90天的原始行动记录，资料不足再扩大到一年','梳理时间线中的因果机制与替代解释','正文无明确事件日期时使用文章发布日期并标注'])
def test_temporal_research_instructions_are_not_factual_premises(text):
    from app.agents.question import research_instruction_premise
    assert research_instruction_premise(SimpleNamespace(content=text,original_span=text))


def test_recency_labels_survive_model_context_compaction():
    from app.model_context import compact_model_payload
    row={'id':'E001','published_at':'2026-10-01','recency_role':'recent','age_days':8}
    assert compact_model_payload({'evidence':[row]})['evidence']==[row]


def test_brave_sends_window_and_preserves_only_ambiguous_page_age(monkeypatch):
    import httpx
    captured=[]
    def handle(request):
        captured.append(dict(request.url.params))
        return httpx.Response(200,json={'web':{'results':[{'url':'https://example.org/a','title':'Research','description':'An action','page_age':'2026-10-08T00:00:00','page_fetched':'2026-10-09T00:00:00'}]}})
    real=httpx.Client
    monkeypatch.setattr(S.config,'BRAVE_SEARCH_API_KEY','test-only')
    monkeypatch.setattr(S.config,'SEARCH_PROXY','')
    monkeypatch.setattr(S.httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(handle),**kw))
    result=S._search_one('proof',days=90,as_of=NOW)
    assert captured[0]['freshness']=='2026-07-11to2026-10-09'
    assert result[0]['page_age']=='2026-10-08T00:00:00'
    assert 'published_at' not in result[0] and 'event_at' not in result[0]


def test_causal_hypotheses_drop_with_omitted_findings(tmp_path):
    from app.schemas import EvidenceAssessment, CausalHypothesis
    q=QuestionSpec(question='future research',mode='scenario',as_of=NOW)
    text='2026年10月1日团队发布了工具。'
    e=S.import_evidence([ImportedEvidence(file_id='source',title='test',excerpt=text)],q,tmp_path).evidence[0]
    c={'evidence_id':e.id,'snapshot_hash':e.snapshot_hash,'paragraph_id':e.passages[0].paragraph_id,'quote':text}
    candidate=AssessmentCandidate(summary='',findings=[{'claim':text,'relation':'background','citations':[c]}])
    findings,_=validate_findings(candidate,None,[e],{e.id:e.passages},NOW)
    assessment=EvidenceAssessment(summary='',findings=findings,findings_validated=True,causal_hypotheses=[CausalHypothesis(from_finding_id='F001',to_finding_id='F002',mechanism='conditional',alternative='other cause',verification='records')])
    context=make_evidence_context([e],assessment,max_chars_per_source=0)
    assert not context['findings'] and not context['assessment'].causal_hypotheses


@pytest.mark.parametrize('text',['2026 年 9 月 18 日，计划公布。','2026-09-18发布计划。','2026/09/18 发布计划。'])
def test_spaced_and_inline_chinese_event_dates(text):
    assert explicit_dates(text)=={'2026-09-18'}
