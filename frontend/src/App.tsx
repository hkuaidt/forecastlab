import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api } from './api'
import { reportFailed, recordedSeconds, requestSeconds } from './researchStatus'
import type { Evidence, FindingCitation, Health, Run } from './types'
import { ExecutionMap, phases, statusNames, type MapNode } from './decision/ExecutionMap'
import { ResearchComposer } from './decision/ResearchComposer'
import { DetailPanel, SourceReader, type DetailTab } from './decision/DetailPanel'

type WorkspacePage = 'canvas' | 'evidence' | 'actors' | 'report'
type WorkspaceRoute = { page: WorkspacePage; runId: string | null }
const pageTitles: Record<WorkspacePage, string> = {
  canvas: '推演画布', evidence: '问题与证据', actors: '主体与行动', report: '研究报告',
}
const pageForTab: Record<DetailTab, WorkspacePage> = { event: 'evidence', actors: 'actors', report: 'report' }
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
  const [history, setHistory] = useState<Run[]>([]), [run, setRun] = useState<Run | null>(null)
  const [route, setRoute] = useState<WorkspaceRoute>(readRoute)
  const [directoryCollapsed, setDirectoryCollapsed] = useState(initialDirectoryState)
  const [loading, setLoading] = useState(true)
  const [compose, setCompose] = useState(false), [historyOpen, setHistoryOpen] = useState(false)
  const [selected, setSelected] = useState<MapNode | null>(null)
  const [source, setSource] = useState<Evidence | null>(null), [citation, setCitation] = useState<FindingCitation | null>(null)
  const [tab, setTab] = useState<DetailTab>('event'), [panelVisible, setPanelVisible] = useState(false)
  const [error, setError] = useState(''), [filter, setFilter] = useState(''), [resuming, setResuming] = useState(false)

  useEffect(() => {
    const changed = () => { setRoute(readRoute()); setSelected(null); setPanelVisible(false); setSource(null) }
    window.addEventListener('hashchange', changed)
    return () => window.removeEventListener('hashchange', changed)
  }, [])
  useEffect(() => {
    try { localStorage.setItem('forecastlab.directoryCollapsed', String(directoryCollapsed)) } catch { /* Optional preference. */ }
  }, [directoryCollapsed])
  useEffect(() => {
    let live = true
    Promise.all([api<Health>('/health'), api<Run[]>('/runs')]).then(async ([h, rows]) => {
      const requested = readRoute().runId
      const initial = requested ? rows.find(r => r.run_id === requested) || await api<Run>(`/runs/${encodeURIComponent(requested)}`) : rows[0] || null
      if (live) { setHealth(h); setHistory(rows); setRun(initial); setLoading(false) }
    }).catch(e => { if (live) { setError(e.message); setLoading(false) } })
    const timer = setInterval(() => api<Health>('/health').then(h => { if (live) setHealth(h) }).catch(() => {}), 15000)
    return () => { live = false; clearInterval(timer) }
  }, [])
  useEffect(() => {
    if (loading || !route.runId || route.runId === run?.run_id) return
    let live = true
    const known = history.find(r => r.run_id === route.runId)
    if (known) { setRun(known); return }
    setRun(null)
    api<Run>(`/runs/${encodeURIComponent(route.runId)}`).then(value => { if (live) setRun(value) }).catch(e => { if (live) setError(e.message) })
    return () => { live = false }
  }, [route.runId, loading])
  useEffect(() => { setSelected(null); setSource(null); setPanelVisible(false); setTab(run?.forecast ? 'report' : 'event') }, [run?.run_id])
  useEffect(() => {
    if (!run || !['queued', 'running'].includes(run.status)) return
    let live = true
    const id = run.run_id, timer = setInterval(() => {
      api<Run>(`/runs/${id}`).then(r => {
        if (!live) return
        setRun(r)
        if (!['queued', 'running'].includes(r.status)) api<Run[]>('/runs').then(rows => { if (live) setHistory(rows) })
      }).catch(e => { if (live) setError(e.message) })
    }, 1800)
    return () => { live = false; clearInterval(timer) }
  }, [run?.run_id, run?.status])

  function navigate(page: WorkspacePage, runId = run?.run_id) {
    setSelected(null); setPanelVisible(false); setSource(null)
    window.location.hash = pageHref(page, runId)
    setRoute({ page, runId: runId || null })
  }
  async function created(id: string) {
    try {
      setRun(await api<Run>(`/runs/${id}`)); setHistory(await api<Run[]>('/runs'))
      setCompose(false); navigate('canvas', id)
    } catch (e) { setError((e as Error).message) }
  }
  async function resume() {
    if (!run) return
    setResuming(true); setError('')
    try { await api(`/runs/${run.run_id}/resume`, { method: 'POST' }); setRun(await api<Run>(`/runs/${run.run_id}`)) }
    catch (e) { setError((e as Error).message) }
    finally { setResuming(false) }
  }
  async function repairReport() {
    if (!run || !run.report_repair?.available) return
    setResuming(true); setError('')
    try {
      const result = await api<{ run_id: string }>(`/runs/${run.run_id}/repair-report`, { method: 'POST' })
      setRun(await api<Run>(`/runs/${result.run_id}`)); setHistory(await api<Run[]>('/runs'))
      navigate('report', result.run_id)
    } catch (e) { setError((e as Error).message) }
    finally { setResuming(false) }
  }
  function chooseRun(value: Run) { setRun(value); setHistoryOpen(false); navigate(route.page, value.run_id) }
  function inspect(node: MapNode) {
    setSelected(node); setPanelVisible(true)
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
      <button className="directory-create" onClick={() => setCompose(true)}>＋ 新建研究</button>
      <nav className="directory-nav" aria-label="研究导航">
        {navItems.map(item => <a key={item.page} href={pageHref(item.page, run?.run_id)} className={route.page === item.page ? 'active' : ''} aria-current={route.page === item.page ? 'page' : undefined}><span>{item.number}</span>{pageTitles[item.page]}<small>{item.detail}</small></a>)}
      </nav>
      <div className="directory-history">
        <div><small>最近研究</small><button onClick={() => setHistoryOpen(true)} aria-label="研究记录">全部 ↗</button></div>
        {history.slice(0, 3).map(r => <button key={r.run_id} className={r.run_id === run?.run_id ? 'current' : ''} onClick={() => chooseRun(r)}><strong>{r.question.question}</strong><small>{new Date(r.started_at).toLocaleDateString('zh-CN')} · {reportFailed(r) ? '报告待修复' : statusNames[r.status] || r.status}</small></button>)}
      </div>
      <div className="directory-services">
        <div><span className={health?.model_configured ? 'connection-dot' : 'connection-dot off'} />{health?.model_configured ? health.model : '模型未连接'}</div>
        <div><span className={health?.search_configured ? 'connection-dot' : 'connection-dot off'} />{health?.search_configured ? '联网搜索可用' : '搜索待配置'}</div>
        <small title={hasRequestTiming ? "已结束请求的输出总量除以活动时间，并发时不重复累计；包含输入处理，不是实时解码速度" : measuredSeconds ? "已完成请求的平均输出速度，包含输入处理与生成时间；不是纯生成速度" : "输出 token 除以已记录阶段的总耗时，包含检索、输入处理和生成"}>{rate === null ? '等待首个调用' : `${rateLabel} ${rate.toFixed(1)} token/s（含输入）`}</small>
      </div>
    </aside>
    <div className="desk-main">
      <header className="work-header">
        <div className="work-breadcrumb">
          <div className="workspace-location"><button className="directory-toggle" onClick={() => setDirectoryCollapsed(v => !v)} aria-expanded={!directoryCollapsed} aria-controls="research-directory" aria-label={directoryCollapsed ? '展开目录' : '隐藏目录'}><svg viewBox="0 0 20 20" aria-hidden="true"><rect x="2.5" y="3" width="15" height="14" rx="1.5" /><path d="M7 3v14" /></svg><span>{directoryCollapsed ? '展开目录' : '隐藏目录'}</span></button><span>研究空间 / {pageTitles[route.page]}</span></div>
          <div className="workspace-shortcuts"><button onClick={() => setCompose(true)}>新建研究 ＋</button><button onClick={() => setHistoryOpen(true)}>切换研究 ↗</button></div>
        </div>
        <h1>{run?.question.question || '把问题展开为可检查的路径'}</h1>
        <div className="work-status">
          <span className={`status-label ${run?.status || ''}`}>{run ? failedReport ? '报告待修复' : statusNames[run.status] || run.status : loading ? '正在载入' : '准备开始'}</span><span>{stageCount} / 6 阶段</span>
          <div className="stage-progress" aria-label={`${stageCount}个阶段已完成`}>{phases.map(([id, label]) => <i title={label} key={id} className={stageComplete(id) ? 'done' : ''} />)}</div>
          <span>{rounds.length} 轮演化</span><span title="累计记录的运行时间，不计服务停止和等待用户期间">累计运行 {Math.floor(Math.max(0, elapsed) / 60)} 分 {Math.floor(Math.max(0, elapsed) % 60)} 秒</span><span>{tokenTotal.toLocaleString()} tokens</span>
          {isCanvas && panelVisible && <button className="reading-toggle" onClick={() => setPanelVisible(false)} aria-label="收起详情面板">收起节点详情 ↓</button>}
        </div>
      </header>
      {isCanvas && failedReport && <div className="outcome-brief warning"><span>报告待修复</span><strong>报告未通过内容与来源检查，原始记录已保留。</strong><a href={pageHref('report', run?.run_id)}>查看原因与修复 ↗</a></div>}
      {isCanvas && run?.forecast && !failedReport && <div className="outcome-brief"><span>当前判断</span><strong>{run.forecast.conclusion}</strong><a href={pageHref('report', run.run_id)}>依据与限制 ↗</a></div>}
      {error && <div role="alert" className="app-alert"><span>{error}</span><button aria-label="关闭错误提示" onClick={() => setError('')}>×</button></div>}
      <main className={`research-space ${isCanvas && panelVisible ? 'panel-open' : ''}`} data-page={route.page} aria-busy={loading}>
        <section className="canvas-workspace" aria-label="推演画布" hidden={!isCanvas}>
          <div className="canvas-area"><ExecutionMap run={run} onInspect={inspect} selectedId={selected?.id} />{!run && !loading && <div className="first-research"><small>开始一项研究</small><h2>提出问题，检查依据，探索可能的未来。</h2><p>每个节点连接一次真实的分析、取证或推演记录。</p><button className="primary" onClick={() => setCompose(true)}>新建事件研究 →</button></div>}</div>
          {panelVisible && <DetailPanel run={run} tab={tab} node={selected} onClose={() => setPanelVisible(false)} onExpand={() => navigate(pageForTab[tab])} onClear={() => setSelected(null)} onSource={inspectSource} onCitation={inspectCitation} onNew={() => setCompose(true)} onRepair={repairReport} repairing={resuming} />}
        </section>
        {!isCanvas && <DetailPanel key={`${run?.run_id}-${route.page}`} presentation="page" run={run} tab={pageTab} node={null} onClose={() => navigate('canvas')} onClear={() => setSelected(null)} onSource={inspectSource} onCitation={inspectCitation} onNew={() => setCompose(true)} onRepair={repairReport} repairing={resuming} />}
      </main>
      {run && !failedReport && ['failed', 'interrupted', 'partial'].includes(run.status) && <div className="resume-line"><span>运行在 {run.failed_stage || run.stage} 阶段中断</span><button disabled={resuming} onClick={resume}>从失败阶段继续 ↗</button></div>}
    </div>
    {compose && <Modal label="新建事件研究" onClose={() => setCompose(false)}><ResearchComposer searchReady={!!health?.search_configured} onClose={() => setCompose(false)} onCreated={created} /></Modal>}
    {source && run && <Modal label="来源原文" onClose={() => setSource(null)}><SourceReader run={run} evidence={source} citation={citation} onClose={() => setSource(null)} /></Modal>}
    {historyOpen && <Modal label="研究记录" onClose={() => setHistoryOpen(false)}><section className="history-dialog"><header><div><small>RESEARCH HISTORY</small><h2>每一次判断，都有一条路径</h2></div><button aria-label="关闭研究记录" onClick={() => setHistoryOpen(false)}>×</button></header><div className="dialog-body"><label className="input-field"><span>筛选研究记录</span><input placeholder="搜索事件或研究问题" value={filter} onChange={e => setFilter(e.target.value)} /></label>{history.filter(r => r.question.question.includes(filter)).map(r => <button className="history-row" key={r.run_id} onClick={() => chooseRun(r)}><strong>{r.question.question}</strong><div><span>{reportFailed(r) ? '报告待修复' : statusNames[r.status] || r.status}</span><small>{new Date(r.started_at).toLocaleString('zh-CN')} · {r.model} · {r.demo ? '固定教学' : r.evidence_mode === 'online' ? '联网取证' : '历史测试'}</small></div></button>)}{!history.length && <p className="subtle">尚无研究记录。新建一个事件，开始第一条路径。</p>}</div></section></Modal>}
  </div>
}
