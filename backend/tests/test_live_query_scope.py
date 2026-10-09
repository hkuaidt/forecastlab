from app.agents.question import finalize_framing, _output_only_query
from app.schemas import AnalyzeQuestionRequest, FramingCandidate


def test_actual_live_output_requests_do_not_become_searches():
    question="AI辅助证明工具会怎样影响数学科研中的证明检验和成果采用？关注数学研究团队、AI工具开发者与Lean形式化验证社区的具体行动和相互影响。请给出主要未来情景、条件、主观概率及证据局限。"
    request=AnalyzeQuestionRequest(question={"question":question,"mode":"scenario"})
    spans=["AI辅助证明工具会怎样影响数学科研中的证明检验和成果采用", "关注数学研究团队、AI工具开发者与Lean形式化验证社区的具体行动和相互影响"]
    candidate=FramingCandidate(proposed_spec=request.question,premises=[{"content":s,"original_span":s,"source_input_id":"I001","origin":"user_explicit"} for s in spans],retrieval_plan=[{"query":"AI辅助证明工具 数学科研 证明检验", "purpose":"initial","premise_indexes":[0]}, {"query":"Lean 形式化验证 社区", "purpose":"initial","premise_indexes":[1]}, {"query":"主要未来情景、条件、主观概率及证据局限","purpose":"background","premise_indexes":[]}])
    frame=finalize_framing(candidate,request,None)
    assert frame.premises==[]
    assert len(frame.retrieval_plan)==2
    assert all(not t.target_premise_ids for t in frame.retrieval_plan)
    assert all(not _output_only_query(t.query) for t in frame.retrieval_plan)


def test_subject_queries_are_not_stripped():
    assert not _output_only_query("AI辅助证明工具 未来情景 研究")
    assert not _output_only_query("Lean theorem prover official documentation")
