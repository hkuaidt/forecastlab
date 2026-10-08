import { useEffect, useState } from 'react'
import type { ClarificationAnswer, PremiseDecision, QuestionFraming } from '../types'

type Choice = '' | 'to_verify' | 'scenario_condition' | 'rejected'
type Props = { demoQuestion?: string; framing: QuestionFraming | null; confirmationId: string | null; busy: boolean; dirty: boolean;
  onAnalyze: () => void; onAnswer: (answers: ClarificationAnswer[]) => void; onConfirm: (decisions: PremiseDecision[]) => void;
  onEdit: () => void; onReload: () => void; onStart: () => void }

export function QuestionConfirmationPanel({ demoQuestion, framing, confirmationId, busy, dirty, onAnalyze, onAnswer, onConfirm, onEdit, onReload, onStart }: Props) {
  const [choices, setChoices] = useState<Record<string, Choice>>({})
  const [answers, setAnswers] = useState<Record<string, string>>({})
  useEffect(() => {
    setChoices(Object.fromEntries((framing?.premises || []).map(p => [p.id,
      p.user_review === 'pending' ? '' : p.user_review === 'rejected' ? 'rejected' : p.treatment])))
    setAnswers({})
  }, [framing?.draft_id, framing?.revision, confirmationId])
  const canConfirm = !!framing && !dirty && !busy && !confirmationId && framing.status === 'ready_for_confirmation'
    && !framing.clarifications.some(c => c.blocking && c.status === 'open') && framing.premises.every(p => !!choices[p.id])
  const decisions = (): PremiseDecision[] => (framing?.premises || []).map(p => ({ premise_id: p.id,
    user_review: choices[p.id] === 'rejected' ? 'rejected' : 'retained', treatment: choices[p.id] === 'scenario_condition' ? 'scenario_condition' : 'to_verify' }))
  return <section className="panel framing-panel" aria-label="问题分析与确认">
    <div className="panel-title"><h3>先确认研究问题</h3><span className="badge">问题分析</span></div>
    <div className="framing-body">
      {demoQuestion && !framing && <div role="status" className="framing-warning">
        <strong>已载入教学问题</strong><p>{demoQuestion}</p>
        <span>请点击“分析问题”开始；不会自动启动预测。</span>
      </div>}
      <p className="muted">把问题、解释与待核查前提分开。分析不会启动后续推演。</p>
      <button id="analyze-question" className="button button-primary" onClick={onAnalyze} disabled={busy}>{busy ? '正在分析或保存…' : '分析问题'}</button>
      {dirty && <p className="framing-warning" role="status">内容已修改，请重新分析后确认</p>}
      {framing && <>
        <div className="framing-status"><strong>{confirmationId ? '问题已确认' : framing.status === 'needs_clarification' ? '需要补充信息' : '请核对系统理解'}</strong><span>草稿版本 {framing.revision}</span><button className="text-button" onClick={onReload} disabled={busy}>重新加载草稿</button></div>
        {framing.demo_case_id && <p className="framing-warning">固定教学案例 · 虚构资料与固定输出，不代表模型效果</p>}
        <details><summary>查看用户原话</summary><p>{framing.raw_question}</p></details>
        <h4>规范化问题候选</h4><p className="normalized-question">{framing.proposed_spec.question}</p>
        {framing.clarifications.length > 0 && <div className="clarification-list"><h4>需要澄清</h4>{framing.clarifications.filter(c => c.status === 'open').map(c =>
          <label className="field" key={c.id}><span>{c.question}{c.blocking && '（需补充）'}</span><textarea rows={2} value={answers[c.id] || ''} onChange={e => setAnswers({ ...answers, [c.id]: e.target.value })} disabled={busy} maxLength={2000}/></label>)}
          <button className="button button-outline" disabled={busy || !Object.values(answers).some(v => v.trim())} onClick={() => onAnswer(Object.entries(answers).filter(([,v]) => v.trim()).map(([id, answer]) => ({ clarification_id: id, answer })))}>提交补充并重新分析</button></div>}
        <h4>前提处理</h4><p className="muted">待核查前提，不是已证实事实</p>
        {framing.premises.length ? framing.premises.map(p => <article className="premise-card" key={p.id}>
          <div className="premise-header"><span className="id-chip">{p.id}</span><strong>{p.content}</strong><small>{p.origin === 'user_explicit' ? '用户明确提出' : '模型从措辞中识别'}</small></div>
          <blockquote>{p.original_span}</blockquote><p>{p.rationale}</p>{p.replaces_id && <small>修订自 {p.replaces_id}，不沿用旧前提编号</small>}
          <label className="field"><span>{p.id} 前提处理</span><select aria-label={`${p.id} 前提处理`} value={choices[p.id] || ''} disabled={busy || !!confirmationId} onChange={e => setChoices({ ...choices, [p.id]: e.target.value as Choice })}>
            <option value="">请选择如何处理</option><option value="to_verify">保留并核查，不当作事实</option><option value="scenario_condition">作为我指定的情景条件</option><option value="rejected">不是我的意思，不采用</option>
          </select></label></article>) : <p>未识别出需要用户确认的前提；不会为凑数量而补出观点。</p>}
        {framing.alternative_directions.length > 0 && <details><summary>其他值得核查的方向（模型提出）</summary>{framing.alternative_directions.map((x,i) => <p key={i}>{x}</p>)}</details>}
        <div className="framing-actions"><button className="button button-primary" disabled={!canConfirm} onClick={() => onConfirm(decisions())}>确认并继续</button>{confirmationId && <button className="button button-outline" onClick={onEdit} disabled={busy}>修改前提决定</button>}</div>
        {confirmationId && <div className="confirmed-next" role="status"><strong>问题确认完成</strong><p>核对上方选择的证据入口后，开始运行这个版本。</p><button className="button button-primary" onClick={onStart} disabled={busy}>运行已确认的问题 →</button></div>}<small>确认只保存本版本；之后再选择证据并开始运行。修改决定需重新分析生成新版本。</small>
      </>}
    </div>
  </section>
}
