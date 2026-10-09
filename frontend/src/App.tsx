import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api } from './api'
import { reportFailed, recordedSeconds, requestSeconds } from './researchStatus'
import type { Evidence, FindingCitation, Health, Run, RunSummary } from './types'
import { ExecutionMap, phases, statusNames, type MapNode } from './decision/ExecutionMap'
import { ResearchComposer } from './decision/ResearchComposer'
import { DetailPanel, SourceReader, type DetailTab } from './decision/DetailPanel'

type ActiveRun = Run & { cancel_requested?: boolean }
const PAGE_SIZE = 20
const summaryOf = (value: RunSummary | Run): RunSummary => ({run_id: value.run_id, question: value.question, status: value.status, stage: value.stage, started_at: value.started_at, finished_at: value.finished_at, parent_run_id: value.parent_run_id, model: value.model, demo: value.demo, is_demo: 'is_demo' in value ? value.is_demo : value.demo, evidence_mode: value.evidence_mode, report_failed: 'report_failed' in value ? value.report_failed : reportFailed(value as Run)})
const summaryStatus = (value: RunSummary) => value.report_failed ? '报告待修复' : statusNames[value.status] || value.status

type WorkspacePage = 'canvas' | 'evidence' | 'actors' | 'report'
type WorkspaceRoute = { page: WorkspacePage; runId: string | null }
const pageTitles: Record<WorkspacePage, string> = {
  canvas: '推演画布', evidence: '问题与证据', actors: '主体与行动', report: '研究报告',
}
function readRoute(): WorkspaceRoute {
  const match = window.location.hash.match(/^#\/(?:research\/([^/]+)\/)?(canvas|evidence|actors|report)\/?$/)
  if (!match) return { page: 'canvas', runId: null }
  try { return { page: match[2] as WorkspacePage, runId: match[1] ? decodeURIComponent(match[1]) : null } }
  catch { return { page: 'canvas', runId: null } }
}
function pageHref(page: WorkspacePage, runId?: string | null) {
  return runId ? `#/research/${encodeURIComponent(runId)}/${page}` : `#/${page}`
}
function initialDirectoryState() {
  try {
    const saved = localStorage.getItem('forecastlab.directoryCollapsed')
    if (saved !== null) return saved === 'true'
  } catch { /* Storage is optional in private browsing. */ }
  return window.innerWidth <= 760
}

function Modal({ children, onClose, label }: { children: ReactNode; onClose: () => void; label: string }) {
  const ref = useRef<HTMLDivElement>(null), close = useRef(onClose)
  close.current = onClose
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null, el = ref.current!
    const first = el.querySelector<HTMLElement>('textarea[autofocus],input[autofocus],textarea,input') || el.querySelector<HTMLElement>('button,a')
    first?.focus()
    const listener = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); close.current() }
      if (e.key === 'Tab') {
        const controls = Array.from(el.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),summary')).filter(v => v.getClientRects().length)
        const head = controls[0], last = controls.at(-1)
        if (e.shiftKey && document.activeElement === head) { e.preventDefault(); last?.focus() }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); head?.focus() }
      }
    }
    el.addEventListener('keydown', listener)
    return () => { el.removeEventListener('keydown', listener); previous?.focus() }
  }, [])
  return <div className="dialog-backdrop"><div ref={ref} className="modal-shell" role="dialog" aria-modal="true" aria-label={label}>{children}</div></div>
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [history, setHistory] = useState<RunSummary[]>([]), [run, setRun] = useState<ActiveRun | null>(null)
  const [booted, setBooted] = useState(false), [historyMore, setHistoryMore] = useState(false), [historyLoading, setHistoryLoading] = useState(false)
  const historyOffset = useRef(0), navigationEpoch = useRef(0), currentRun = useRef<ActiveRun | null>(null)
  currentRun.current = run
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set())
  const [composeParent, setComposeParent] = useState<Run | null>(null)
  const [route, setRoute] = useState<WorkspaceRoute>(readRoute)
  const [directoryCollapsed, setDirectoryCollapsed] = useState(initialDirectoryState)
  const [loading, setLoading] = useState(true)
  const [compose, setCompose] = useState(false), [historyOpen, setHistoryOpen] = useState(false)
  const [selected, setSelected] = useState<MapNode | null>(null)
  const [source, setSource] = useState<Evidence | null>(null), [citation, setCitation] = useState<FindingCitation | null>(null)
  const [tab, setTab] = useState<DetailTab>('event'), [panelVisible, setPanelVisible] = useState(false), [readerExpanded, setReaderExpanded] = useState(false)
  const [error, setError] = useState(''), [filter, setFilter] = useState('')
  const futureRun = !!run && (!run.question.resolve_by || new Date(run.question.resolve_by).getTime() > Date.now()) && !run.evidence.some(e => e.source_type === 'exercise')
  const resuming = !!run && busyIds.has(run.run_id)
  function busy(id: string, value: boolean) { setBusyIds(old => { const next = new Set(old); value ? next.add(id) : next.delete(id); return next }) }
  function remember(value: ActiveRun, preserveTerminal = false) { setHistory(rows => rows.some(r => r.run_id === value.run_id) ? rows.map(r => r.run_id === value.run_id && !(preserveTerminal && !['queued', 'running'].includes(r.status)) ? summaryOf(value) : r) : [summaryOf(value), ...rows]) }
  function composeResearch(parent: Run | null = null) { setComposeParent(parent); setCompose(true) }

  useEffect(() => {
    const changed = () => { navigationEpoch.current++; setRoute(readRoute()); setSelected(null); setPanelVisible(false); setSource(null) }
    window.addEventListener('hashchange', changed)
    return () => window.removeEventListener('hashchange', changed)
  }, [])
  useEffect(() => {
    try { localStorage.setItem('forecastlab.directoryCollapsed', String(directoryCollapsed)) } catch { /* Optional preference. */ }
  }, [directoryCollapsed])
  useEffect(() => {
    let live = true
    Promise.all([api<Health>('/health'), api<RunSummary[]>(`/runs?summary=true&limit=${PAGE_SIZE}&offset=0`)]).then(([h, rows]) => {
      if (!live) return
      setHealth(h); setHistory(rows.map(summaryOf)); historyOffset.current = rows.length; setHistoryMore(rows.length === PAGE_SIZE)
      if (!readRoute().runId && rows[0]) {
        const next = {page: readRoute().page, runId: rows[0].run_id}
        window.history.replaceState(null, '', pageHref(next.page, next.runId)); setRoute(next)
      }
    }).catch(e => { if (live) setError(e.message) }).finally(() => { if (live) setBooted(true) })
    const timer = setInterval(() => api<Health>('/health').then(h => { if (live) setHealth(h) }).catch(() => {}), 15000)
    return () => { live = false; clearInterval(timer) }
  }, [])
  useEffect(() => {
    if (!booted) return
    if (!route.runId) { setRun(null); setLoading(false); return }
    let live = true
    setLoading(true); setRun(null); setError('')
    api<ActiveRun>(`/runs/${encodeURIComponent(route.runId)}`).then(value => {
      if (live) { setRun(value); remember(value) }
    }).catch(e => { if (live) setError(e.message) }).finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [route.runId, booted])
  async function loadMoreHistory() {
    if (historyLoading || !historyMore) return
    setHistoryLoading(true)
    try {
      const rows = await api<RunSummary[]>(`/runs?summary=true&limit=${PAGE_SIZE}&offset=${historyOffset.current}`)
      historyOffset.current += rows.length; setHistoryMore(rows.length === PAGE_SIZE)
      setHistory(old => { const known = new Set(old.map(r => r.run_id)); return [...old, ...rows.filter(r => !known.has(r.run_id)).map(summaryOf)] })
    } catch (e) { setError((e as Error).message) }
    finally { setHistoryLoading(false) }
  }
  useEffect(() => { setSelected(null); setSource(null); setPanelVisible(false); setTab(run?.forecast ? 'report' : 'event') }, [run?.run_id])
  useEffect(() => {
    if (!run || !['queued', 'running'].includes(run.status)) return
    let live = true, pending = false
    const id = run.run_id, timer = setInterval(() => {
      if (pending) return
      pending = true
      api<ActiveRun>(`/runs/${encodeURIComponent(id)}`).then(r => {
        if (!live || r.run_id !== id) return
        setRun(current => current?.run_id === id ? r : current)
        remember(r)
      }).catch(e => { if (live) setError(e.message) }).finally(() => { pending = false })
    }, 1800)
    return () => { live = false; clearInterval(timer) }
  }, [run?.run_id, run?.status])

  function navigate(page: WorkspacePage, runId = run?.run_id) {
    navigationEpoch.current++
    setSelected(null); setPanelVisible(false); setSource(null)
    window.location.hash = pageHref(page, runId)
    setRoute({ page, runId: runId || null })
  }
  async function created(id: string) {
    setCompose(false); navigate('canvas', id)
  }
  async function resume() {
    if (!run) return
    const id = run.run_id, epoch = navigationEpoch.current
    busy(id, true); setError('')
    try {
      await api(`/runs/${encodeURIComponent(id)}/resume`, { method: 'POST' })
      const value = await api<ActiveRun>(`/runs/${encodeURIComponent(id)}`)
      remember(value, navigationEpoch.current !== epoch); setRun(current => navigationEpoch.current === epoch && current?.run_id === id ? value : current)
    } catch (e) { if (navigationEpoch.current === epoch && currentRun.current?.run_id === id) setError((e as Error).message) }
    finally { busy(id, false) }
  }
  async function repairReport() {
    if (!run || !run.report_repair?.available) return
    const id = run.run_id, epoch = navigationEpoch.current
    busy(id, true); setError('')
    try {
      const result = await api<{run_id: string}>(`/runs/${encodeURIComponent(id)}/repair-report`, {method: 'POST'})
      const child = await api<ActiveRun>(`/runs/${encodeURIComponent(result.run_id)}`)
      remember(child, true)
      if (navigationEpoch.current === epoch && currentRun.current?.run_id === id) navigate('report', result.run_id)
    } catch (e) { if (navigationEpoch.current === epoch && currentRun.current?.run_id === id) setError((e as Error).message) }
    finally { busy(id, false) }
  }
  async function cancelRun() {
    if (!run || run.cancel_requested) return
    const id = run.run_id, epoch = navigationEpoch.current
    busy(id, true); setError('')
    try {
      const value = await api<ActiveRun>(`/runs/${encodeURIComponent(id)}/cancel`, {method: 'POST'})
      remember(value, true); setRun(current => navigationEpoch.current === epoch && current?.run_id === id && ['queued', 'running'].includes(current.status) ? value : current)
    } catch (e) { if (navigationEpoch.current === epoch && currentRun.current?.run_id === id) setError((e as Error).message) }
    finally { busy(id, false) }
  }
  function chooseRun(value: RunSummary) { setHistoryOpen(false); navigate(route.page, value.run_id) }
  function inspect(node: MapNode) {
    setSelected(node); setPanelVisible(true); setReaderExpanded(false)
    setTab(node.phase === 'forecast' || node.phase === 'review' ? 'report' : node.phase === 'world' || node.phase === 'simulation' ? 'actors' : 'event')
  }
  function inspectSource(value: Evidence) { setCitation(null); setSource(value) }
  function inspectCitation(value: FindingCitation) {
    const evidence = run?.evidence.find(e => e.id === value.evidence_id)
    if (evidence) { setCitation(value); setSource(evidence) }
  }
  const rounds = [...new Set((run?.simulation || []).map(s => s.round))]
  const failedReport = reportFailed(run)
  const stageComplete = (key: string) => !!run?.stage_outputs[key] && !(key === 'forecast' && failedReport)
  const stageCount = phases.filter(([key]) => stageComplete(key)).length
  const elapsed = recordedSeconds(run)
  const calls = [...new Map([...(run?.preparation_records || []), ...(run?.model_calls || [])].map(c => [c.request_id, c])).values()].filter(c => c.usage_known && c.elapsed_seconds > 0)
  const hasRequestTiming = calls.length > 0 && calls.every(c => Number.isFinite(Date.parse(c.started_at || '')))
  const measuredSeconds = hasRequestTiming ? requestSeconds(calls) : calls.reduce((s, c) => s + c.elapsed_seconds, 0)
  const rate = measuredSeconds ? calls.reduce((s, c) => s + c.completion_tokens, 0) / measuredSeconds
    : elapsed > 0 && run?.usage.completion_tokens ? run.usage.completion_tokens / elapsed : null
  const rateLabel = hasRequestTiming ? '输出吞吐' : measuredSeconds ? '平均' : '运行均速'
  const tokenTotal = (run?.usage.prompt_tokens || 0) + (run?.usage.completion_tokens || 0)
  const pageTab: DetailTab = route.page === 'evidence' ? 'event' : route.page === 'actors' ? 'actors' : 'report'
  const isCanvas = route.page === 'canvas'
  const navItems: { page: WorkspacePage; number: string; detail: string }[] = [
    { page: 'canvas', number: '01', detail: '探索路径' },
    { page: 'evidence', number: '02', detail: `${run?.evidence.length || 0} 个来源` },
    { page: 'actors', number: '03', detail: `${run?.world?.actors.length || 0} 个主体` },
    { page: 'report', number: '04', detail: '判断与边界' },
  ]

  return <div className={`forecast-app research-desk ${directoryCollapsed ? 'directory-collapsed' : ''}`}>
    <aside id="research-directory" className="research-directory" hidden={directoryCollapsed}>
      <div className="desk-brand"><span>F/</span><div>ForecastLab<small>预测研究工作台</small></div></div>
      <button className="directory-create" onClick={() => composeResearch()}>＋ 新建研究</button>
      <nav className="directory-nav" aria-label="研究导航">
        {navItems.map(item => <a key={item.page} href={pageHref(item.page, run?.run_id)} className={route.page === item.page ? 'active' : ''} aria-current={route.page === item.page ? 'page' : undefined}><span>{item.number}</span>{pageTitles[item.page]}<small>{item.detail}</small></a>)}
      </nav>
      <div className="directory-history">
        <div><small>最近研究</small><button onClick={() => setHistoryOpen(true)} aria-label="研究记录">全部 ↗</button></div>
        {history.slice(0, 3).map(r => <button key={r.run_id} className={r.run_id === run?.run_id ? 'current' : ''} onClick={() => chooseRun(r)}><strong>{r.question.question}</strong><small>{new Date(r.started_at).toLocaleDateString('zh-CN')} · {summaryStatus(r)}</small></button>)}
      </div>
      <div className="directory-services">
        <div title={health?.readiness?.model.detail}><span className={health?.model_ready === true ? 'connection-dot' : 'connection-dot off'} />{!health?.model_configured ? '模型未配置' : health.model_ready === true ? `${health.model} · 可用` : health.model_ready === false ? '模型暂不可用' : `${health.model} · 就绪状态未知`}</div>
        <div title={health?.readiness?.search.detail}><span className={health?.search_ready === true ? 'connection-dot' : 'connection-dot off'} />{!health?.search_configured ? '搜索待配置' : health.search_ready === true ? '联网搜索可用' : health.search_ready === false ? '搜索暂不可用' : '搜索已配置 · 待请求验证'}</div>
        <small title={hasRequestTiming ? "已结束请求的输出总量除以活动时间，并发时不重复累计；包含输入处理，不是实时解码速度" : measuredSeconds ? "已完成请求的平均输出速度，包含输入处理与生成时间；不是纯生成速度" : "输出 token 除以已记录阶段的总耗时，包含检索、输入处理和生成"}>{rate === null ? '等待首个调用' : `${rateLabel} ${rate.toFixed(1)} token/s（含输入处理耗时）`}</small>
      </div>
    </aside>
    <div className="desk-main">
      <header className="work-header">
        <div className="work-breadcrumb">
          <div className="workspace-location"><button className="directory-toggle" onClick={() => setDirectoryCollapsed(v => !v)} aria-expanded={!directoryCollapsed} aria-controls="research-directory" aria-label={directoryCollapsed ? '展开目录' : '隐藏目录'}><svg viewBox="0 0 20 20" aria-hidden="true"><rect x="2.5" y="3" width="15" height="14" rx="1.5" /><path d="M7 3v14" /></svg><span>{directoryCollapsed ? '展开目录' : '隐藏目录'}</span></button><span>研究空间 / {pageTitles[route.page]}</span></div>
          <div className="workspace-shortcuts"><button onClick={() => composeResearch()}>新建研究 ＋</button><button onClick={() => setHistoryOpen(true)}>切换研究 ↗</button></div>
        </div>
        <h1>{run?.question.question || '把问题展开为可检查的路径'}</h1>
        <div className="work-status">
          <span className={`status-label ${run?.status || ''}`}>{run ? failedReport ? '报告待修复' : statusNames[run.status] || run.status : loading ? '正在载入' : '准备开始'}</span><span>{stageCount} / 6 阶段</span>
          <div className="stage-progress" aria-label={`${stageCount}个阶段已完成`}>{phases.map(([id, label]) => <i title={label} key={id} className={stageComplete(id) ? 'done' : ''} />)}</div>
          <span>{rounds.length} 轮演化</span><span title="累计记录的运行时间，不计服务停止和等待用户期间">累计运行 {Math.floor(Math.max(0, elapsed) / 60)} 分 {Math.floor(Math.max(0, elapsed) % 60)} 秒</span><span>{tokenTotal.toLocaleString()} tokens</span>
          {run && ['queued', 'running'].includes(run.status) && <button className="run-stop" disabled={resuming || run.cancel_requested} onClick={cancelRun}>{run.cancel_requested ? '正在停止…' : '停止运行'}</button>}
          {run?.status === 'cancelled' && <span className="run-status-note">已保留停止前的阶段记录。</span>}
          {isCanvas && panelVisible && <button className="reading-toggle" onClick={() => setPanelVisible(false)} aria-label="收起详情面板">收起节点详情 ↓</button>}
        </div>
      </header>
      {isCanvas && failedReport && <div className="outcome-brief warning"><span>报告待修复</span><strong>报告未通过内容与来源检查，原始记录已保留。</strong><a href={pageHref('report', run?.run_id)}>查看原因与修复 ↗</a></div>}
      {isCanvas && run?.forecast && !failedReport && <div className="outcome-brief"><span>当前判断</span><strong>{run.forecast.conclusion}</strong><a href={pageHref('report', run.run_id)}>依据与限制 ↗</a></div>}
      {error && <div role="alert" className="app-alert"><span>{error}</span><button aria-label="关闭错误提示" onClick={() => setError('')}>×</button></div>}
      <main className={`research-space ${isCanvas && panelVisible ? 'panel-open' : ''}`} data-page={route.page} aria-busy={loading}>
        <section className="canvas-workspace" aria-label="推演画布" hidden={!isCanvas}>
          <div className="canvas-area"><ExecutionMap run={run} onInspect={inspect} selectedId={selected?.id} />{!run && !loading && <div className="first-research"><small>开始一项研究</small><h2>提出问题，检查依据，探索可能的未来。</h2><p>每个节点连接一次真实的分析、取证或推演记录。</p><button className="primary" onClick={() => composeResearch()}>新建事件研究 →</button></div>}</div>
          {panelVisible && <DetailPanel presentation={readerExpanded ? 'page' : 'panel'} run={run} tab={tab} node={selected} onClose={() => setPanelVisible(false)} onExpand={() => setReaderExpanded(value => !value)} onClear={() => setSelected(null)} onSource={inspectSource} onCitation={inspectCitation} onNew={() => composeResearch(run)} onRepair={repairReport} repairing={resuming} />}
        </section>
        {!isCanvas && <DetailPanel key={`${run?.run_id}-${route.page}`} presentation="page" run={run} tab={pageTab} node={null} onClose={() => navigate('canvas')} onClear={() => setSelected(null)} onSource={inspectSource} onCitation={inspectCitation} onNew={() => composeResearch(run)} onRepair={repairReport} repairing={resuming} />}
      </main>
      {run && futureRun && !failedReport && !run.stage_outputs.forecast && ['failed', 'interrupted', 'partial', 'cancelled'].includes(run.status) && <div className="resume-line"><span>{run.status === 'cancelled' ? '运行已停止，阶段记录已保留。' : `运行在 ${run.failed_stage || run.stage} 阶段中断`}</span><button disabled={resuming} onClick={resume}>{run.status === 'cancelled' ? '从已保存阶段继续 ↗' : '从失败阶段继续 ↗'}</button></div>}
    </div>
    {compose && <Modal label="新建事件研究" onClose={() => setCompose(false)}><ResearchComposer parent={composeParent} unavailableReason={!health?.model_configured?'模型尚未配置，请配置后开始推演。':health.model_ready===false?'模型暂不可用，请等待服务恢复后开始推演。':health.search_ready===false?'搜索服务暂不可用，请稍后重试。':undefined} searchReady={!!health?.model_configured && !!health?.search_configured && health.search_ready !== false && health.model_ready !== false} onClose={() => setCompose(false)} onCreated={created} /></Modal>}
    {source && run && <Modal label="来源原文" onClose={() => setSource(null)}><SourceReader run={run} evidence={source} citation={citation} onClose={() => setSource(null)} /></Modal>}
    {historyOpen && <Modal label="研究记录" onClose={() => setHistoryOpen(false)}><section className="history-dialog"><header><div><small>RESEARCH HISTORY</small><h2>每一次判断，都有一条路径</h2></div><button aria-label="关闭研究记录" onClick={() => setHistoryOpen(false)}>×</button></header><div className="dialog-body"><label className="input-field"><span>筛选研究记录</span><input placeholder="搜索事件或研究问题" value={filter} onChange={e => setFilter(e.target.value)} /></label>{history.filter(r => r.question.question.includes(filter)).map(r => <button className="history-row" key={r.run_id} onClick={() => chooseRun(r)}><strong>{r.question.question}</strong><div><span>{summaryStatus(r)}</span><small>{new Date(r.started_at).toLocaleString('zh-CN')} · {r.model} · {(r.demo || r.is_demo) ? '固定教学' : r.evidence_mode === 'online' ? '联网取证' : r.evidence_mode ? '历史测试' : '研究记录'}</small></div></button>)}{!history.length && <p className="subtle">尚无研究记录。新建一个事件，开始第一条路径。</p>}<div className="history-footer"><small>筛选当前已加载的 {history.length} 条研究。{historyMore ? '可继续加载更早记录。' : '已显示全部记录。'}</small>{historyMore && <button disabled={historyLoading} onClick={loadMoreHistory}>{historyLoading ? '正在加载…' : '加载更早记录'}</button>}</div></div></section></Modal>}
  </div>
}
