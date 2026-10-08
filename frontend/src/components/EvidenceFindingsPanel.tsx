import { useState } from 'react'
import type { Evidence, EvidenceAssessment, FindingCitation, QuestionFraming } from '../types'

export function sliceCodepoints(text: string, start: number, end: number): string { return Array.from(text).slice(start, end).join('') }
const relations: Record<string, string> = { supports: '支持', challenges: '挑战', alternative: '替代解释', background: '背景', unclear: '尚不明确' }
export function SourceLimitations({ evidence: e }: { evidence: Evidence }) {
  return <div className="source-limitations">
    {e.date_status === 'synthetic' && <span>教学虚构材料</span>}
    {(e.content_kind === 'snippet' || e.source_type === 'snippet_only') && <span>只有搜索摘要，未取得正文</span>}
    {!e.published_at && <span>发布时间未知</span>}
    {e.content_truncated && <span>正文已截断，不是完整原文</span>}
    {e.availability === 'historical_exercise' && <span>历史回看·非盲测</span>}
    {e.event_status === 'planned' && <span>未来计划，不是已发生的事件</span>}
  </div>
}

type Props = { framing: QuestionFraming | null | undefined; assessment: EvidenceAssessment | null; evidence: Evidence[];
  onInspectCitation: (citation: FindingCitation) => void }

export function EvidenceFindingsPanel({ framing, assessment, evidence, onInspectCitation }: Props) {
  const [premise, setPremise] = useState('')
  const [relation, setRelation] = useState('')
  const [sourceState, setSourceState] = useState('')
  if (!framing) return <section className="panel framing-panel"><div className="framing-body"><p>旧版记录未包含问题理解/逐项发现</p><small>可以继续查看原来的来源列表；系统不会为历史记录补造确认过程。</small></div></section>
  const sources = new Map(evidence.map(e => [e.id, e]))
  const findingById = new Map((assessment?.findings || []).map(f => [f.id, f]))
  const quality = assessment?.quality_profile
  const stateMatches = (id: string) => {
    const e = sources.get(id)
    return !sourceState || !!e && (sourceState === 'snippet' ? e.content_kind === 'snippet' || e.source_type === 'snippet_only' :
      sourceState === 'date_unknown' ? !e.published_at : sourceState === 'truncated' ? !!e.content_truncated : e.content_kind === 'body')
  }
  const visible = (assessment?.findings || []).filter(f => (!premise || f.target_premise_ids.includes(premise)) && (!relation || f.relation === relation) && f.citations.some(c => stateMatches(c.evidence_id)))
  return <section className="panel findings-panel" aria-label="逐项证据发现">
    <div className="panel-title"><h3>前提与证据发现</h3><span className="badge">证据评估</span></div>
    <div className="framing-body">
      <p className="muted">发现是对来源的分析，不是新增的外部证据。引用定位通过校验，也不等于推论已经成立。</p>
      <details><summary>已确认的问题理解 · 版本 {framing.revision}</summary><p>{framing.proposed_spec.question}</p>{framing.premises.map(p =>
        <p key={p.id}><strong>{p.id} {p.content}</strong> · {p.user_review === 'rejected' ? '已否认，不采用' : p.treatment === 'scenario_condition' ? '用户指定情景条件' : '待核查，不是事实'}</p>)}</details>
      {quality && <section className="evidence-quality-profile" aria-label="证据质量概览">
        <div className="evidence-quality-heading"><h4>证据质量概览</h4><small>本次运行 · 由检索记录和校验结果自动统计</small></div>
        <dl className="evidence-quality-grid">
          <div><dt>有效来源</dt><dd>{quality.source_count}</dd></div>
          <div><dt>来源组</dt><dd>{quality.source_group_count}</dd></div>
          <div><dt>取得正文</dt><dd>{quality.body_source_count}</dd></div>
          <div><dt>只有摘要</dt><dd>{quality.snippet_only_count}</dd></div>
          <div><dt>有效发现</dt><dd>{quality.validated_finding_count}</dd></div>
          <div><dt>校验拒绝</dt><dd>{quality.rejected_finding_count}</dd></div>
        </dl>
        <p className="evidence-quality-disclaimer">来源组只是保守的同源线索分组，并不代表已核实的独立来源；质量统计不证明来源真实或引文语义成立。</p>
        <details className="evidence-quality-extra"><summary>其他取证指标</summary>
          <p>标记一手来源 {quality.primary_label_count}（未独立核实） · 发布时间未知 {quality.unknown_publication_count} · 正文截断 {quality.truncated_count} · 疑似同源 {quality.suspected_same_source_count}</p>
          <p>合并别名 {quality.merged_alias_count} · 检索成功 / 空结果 / 失败 {quality.search_success_count} / {quality.search_empty_count} / {quality.search_failure_count} · 被排除候选 {quality.excluded_count} · 未解决冲突 {quality.unresolved_conflict_count}</p>
        </details>
        {quality.warnings.length > 0 && <details className="evidence-quality-warnings"><summary>来源与证据限制（{quality.warnings.length}）</summary>
          <ul>{quality.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul>
        </details>}
      </section>}
      <div className="findings-filters">
        <label className="field"><span>按前提筛选</span><select aria-label="按前提筛选" value={premise} onChange={e => setPremise(e.target.value)}><option value="">所有前提</option>{framing.premises.filter(p => p.user_review !== 'rejected').map(p => <option value={p.id} key={p.id}>{p.id} {p.content}</option>)}</select></label>
        <label className="field"><span>按关系筛选</span><select aria-label="按关系筛选" value={relation} onChange={e => setRelation(e.target.value)}><option value="">所有关系</option>{Object.entries(relations).map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <label className="field"><span>按来源状态筛选</span><select aria-label="按来源状态筛选" value={sourceState} onChange={e => setSourceState(e.target.value)}><option value="">所有来源</option><option value="body">取得正文</option><option value="snippet">只有摘要</option><option value="date_unknown">发布时间未知</option><option value="truncated">正文截断</option></select></label>
      </div>
      <div data-testid="valid-findings" className="finding-list">{visible.length ? visible.map(f => <article className="finding-card" key={f.id}>
        <div className="premise-header"><span className="id-chip">{f.id}</span><span className={`finding-relation relation-${f.relation}`}>{relations[f.relation] || f.relation}</span><small>针对 {f.target_premise_ids.join('、') || '研究问题背景'}</small></div>
        <h4>{f.claim}</h4>{f.citations.map((c,i) => <div className="finding-citation" key={`${c.evidence_id}-${i}`}><blockquote>{c.quote}</blockquote>
          <button className="text-button" onClick={() => onInspectCitation(c)}>查看 {c.evidence_id} 原文</button><small>段落 {c.paragraph_id}</small>
          {sources.get(c.evidence_id) && <SourceLimitations evidence={sources.get(c.evidence_id)!}/>}</div>)}
        {f.limitation && <p className="finding-limitation">不能据此证明：{f.limitation}</p>}
      </article>) : <p className="empty-note">{assessment ? '当前条件下没有可展示的有效发现。未找到反证不等于原观点成立。' : '等待证据阶段完成。'}</p>}</div>
      {!!assessment?.conflict_details?.length && <div className="conflict-details"><h4>冲突与口径</h4>{assessment.conflict_details.map((c,i) => {
        const related = new Map<string, FindingCitation>()
        for (const findingId of c.finding_ids) {
          for (const citation of findingById.get(findingId)?.citations || []) {
            if (!related.has(citation.evidence_id)) related.set(citation.evidence_id, citation)
          }
        }
        return <article key={i}><strong>{c.issue}</strong><span> · {c.status === 'resolved' ? '已解释' : '未解决'}</span>
          <p>{c.scope_comparison}</p><p>{c.explanation}</p><small>关联发现：{c.finding_ids.join('、')}</small>
          {related.size > 0 && <div className="conflict-source-links"><span>关联来源：</span>{Array.from(related.values()).map(citation =>
            <button type="button" className="text-button" key={citation.evidence_id} onClick={() => onInspectCitation(citation)}>
              {citation.evidence_id} · {sources.get(citation.evidence_id)?.title || '查看原文'}
            </button>)}</div>}
        </article>
      })}</div>}
      {!!assessment?.gap_details?.length && <details><summary>缺口及取证限制（{assessment.gap_details.length}）</summary>{assessment.gap_details.map((g,i) => <p key={i}>{g.missing}<small> · {g.attempted_query_ids.join('、')}</small></p>)}</details>}
      {!!assessment?.retrieval_log?.length && <details><summary>查看实际检索记录</summary>{assessment.retrieval_log.map(l => <article className="retrieval-log" key={l.task_id}><strong>{l.task_id} · {l.query}</strong><p>{l.status === 'failed' ? `检索失败：${l.error}` : l.status === 'empty' ? '未返回资料' : `返回 ${l.result_count} 条候选`}</p></article>)}</details>}
      {!!assessment?.rejected_findings?.length && <details className="rejected-findings"><summary><strong>校验未通过</strong>（{assessment.rejected_findings.length}）</summary><p>下列候选未进入有效发现和下游事实依据。</p>{assessment.rejected_findings.map((r,i) => <p key={i}>{r.reason}</p>)}</details>}
    </div>
  </section>
}
