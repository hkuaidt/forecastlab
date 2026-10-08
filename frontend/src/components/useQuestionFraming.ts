import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { ClarificationAnswer, PremiseDecision, QuestionConfirmation, QuestionDraft, QuestionDraftView, QuestionFraming } from '../types'

export type QuestionFields = { question: string; asOf: string; resolveBy: string; resolutionRule: string; resolutionSource: string; mode: 'binary' | 'scenario'; assumptions: string }
const localDate = (value: string | null) => {
  if (!value) return ''
  const date = new Date(value)
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
}
export function fieldsFromSpec(q: QuestionDraft): QuestionFields {
  return { question: q.question, asOf: localDate(q.as_of), resolveBy: localDate(q.resolve_by),
    resolutionRule: q.resolution_rule, resolutionSource: q.resolution_source || '', mode: q.mode, assumptions: q.user_assumptions.join('\n') }
}
type FramingOptions = { scenarioOnly?: boolean; useCurrentTime?: boolean }
const signature = (fields: QuestionFields, options: FramingOptions) =>
  JSON.stringify({ fields, scenarioOnly: !!options.scenarioOnly, useCurrentTime: !!options.useCurrentTime })
const KEY = 'forecastlab.agent12.draft_id'
type AnalyzeBody = { question: QuestionDraft; draft_id?: string; expected_revision?: number; operation_id: string; answers: ClarificationAnswer[]; demo_case_id?: string }

export function useQuestionFraming(fields: QuestionFields, applyFields: (next: QuestionFields) => void, onError: (message: string) => void, options: FramingOptions = {}) {
  const [framing, setFraming] = useState<QuestionFraming | null>(null)
  const [confirmationId, setConfirmationId] = useState<string | null>(null)
  const [analyzedSignature, setAnalyzedSignature] = useState<string | null>(null)
  const [forceDirty, setForceDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [demoCaseId, setDemoCaseId] = useState<string | null>(null)
  const currentSignature = signature(fields, options)
  const latestSignature = useRef(currentSignature); latestSignature.current = currentSignature
  const callbacks = useRef({ applyFields, onError }); callbacks.current = { applyFields, onError }
  const pending = useRef<{ key: string; body: AnalyzeBody } | null>(null)
  const dirty = !!framing && (forceDirty || analyzedSignature !== currentSignature)

  function install(frame: QuestionFraming, confirmed: string | null = null) {
    const next = fieldsFromSpec(frame.proposed_spec)
    if (options.scenarioOnly) {
      next.mode = 'scenario'; next.resolveBy = ''; next.resolutionRule = ''
      next.resolutionSource = ''; next.assumptions = ''
    }
    if (options.useCurrentTime) next.asOf = ''
    callbacks.current.applyFields(next)
    setFraming(frame); setConfirmationId(confirmed); setAnalyzedSignature(signature(next, options)); setForceDirty(false)
    setDemoCaseId(frame.demo_case_id)
    try { localStorage.setItem(KEY, frame.draft_id) } catch { /* Storage can be disabled. */ }
  }

  useEffect(() => { if (dirty) setConfirmationId(null) }, [dirty])

  async function analyze(answers: ClarificationAnswer[] = []) {
    callbacks.current.onError(''); setBusy(true)
    const submittedSignature = latestSignature.current
    try {
      const question: QuestionDraft = { question: fields.question.trim(),
        as_of: options.useCurrentTime ? new Date().toISOString() : new Date(fields.asOf).toISOString(),
        resolve_by: !options.scenarioOnly && fields.mode === 'binary' && fields.resolveBy ? new Date(fields.resolveBy).toISOString() : null,
        resolution_rule: options.scenarioOnly ? '' : fields.resolutionRule,
        resolution_source: options.scenarioOnly ? null : fields.resolutionSource || null,
        mode: options.scenarioOnly ? 'scenario' : fields.mode,
        user_assumptions: options.scenarioOnly ? [] : fields.assumptions.split('\n').map(x => x.trim()).filter(Boolean) }
      // A failed request keeps its original operation/time snapshot. A completed
      // analysis starts a fresh revision with the current time on the next click.
      const key = JSON.stringify({ question: { ...question, as_of: options.useCurrentTime ? 'current' : question.as_of },
        draft: framing?.draft_id, revision: framing?.revision, answers, demoCaseId })
      if (!pending.current || pending.current.key !== key) {
        pending.current = { key, body: { question, answers, operation_id: crypto.randomUUID(),
          ...(framing ? { draft_id: framing.draft_id, expected_revision: framing.revision } : {}),
          ...(demoCaseId ? { demo_case_id: demoCaseId } : {}) } }
      }
      const next = await api<QuestionFraming>('/questions/analyze', { method: 'POST', body: JSON.stringify(pending.current.body) })
      pending.current = null
      if (latestSignature.current !== submittedSignature) {
        callbacks.current.onError('分析期间输入已变化；已保存旧输入结果，请重新加载后分析当前内容。')
        return
      }
      install(next)
    } catch (error) { callbacks.current.onError((error as Error).message) } finally { setBusy(false) }
  }

  async function confirm(decisions: PremiseDecision[]) {
    if (!framing || dirty) return
    callbacks.current.onError(''); setBusy(true)
    const submittedSignature = latestSignature.current
    try {
      const result = await api<QuestionConfirmation>(`/questions/${framing.draft_id}/confirm`, {
        method: 'POST', body: JSON.stringify({ expected_revision: framing.revision, decisions }) })
      if (latestSignature.current === submittedSignature) install(result.framing, result.confirmation_id)
    } catch (error) { callbacks.current.onError((error as Error).message) } finally { setBusy(false) }
  }

  async function reload() {
    if (!framing) return
    setBusy(true)
    try {
      const view = await api<QuestionDraftView>(`/questions/${framing.draft_id}`)
      install(view.confirmation?.framing || view.framing, view.confirmation?.confirmation_id || null)
    } catch (error) { callbacks.current.onError((error as Error).message) } finally { setBusy(false) }
  }

  function newDraft() {
    setFraming(null); setConfirmationId(null); setAnalyzedSignature(null); setDemoCaseId(null); setForceDirty(false); pending.current = null
    try { localStorage.removeItem(KEY) } catch { /* optional storage */ }
  }
  function editDecisions() { setConfirmationId(null); setForceDirty(true) }
  return { framing, confirmationId: dirty ? null : confirmationId, dirty, busy, analyze, confirm, reload, newDraft, editDecisions, install, demoCaseId, setDemoCaseId }
}
