import type { Evidence, Run } from '../types'

export function RecordReferences({run, evidenceIds = [], assumptionIds = [], simulationIds = [], onSource}: {
  run: Run; evidenceIds?: string[]; assumptionIds?: string[]; simulationIds?: string[]; onSource: (e: Evidence) => void
}) {
  if (!evidenceIds.length && !assumptionIds.length && !simulationIds.length) return null
  return <div className="record-references" aria-label="关联依据">
    {!!evidenceIds.length && <div><span>来源与证据发现</span>{evidenceIds.map(id => {
      const source = run.evidence.find(e => e.id === id)
      if (source) return <button key={id} onClick={() => onSource(source)}>{id} · {source.title} ↗</button>
      const finding = run.evidence_assessment?.findings?.find(item => item.id === id)
      if (finding) return <details className="finding-reference" key={id}>
        <summary>证据发现 {id} · {finding.claim}</summary>
        <p>{finding.claim}</p>
        <p><strong>判断边界</strong> · {finding.limitation || '未记录额外限制。'}</p>
        {finding.citations.map((citation, i) => {
          const citedSource = run.evidence.find(item => item.id === citation.evidence_id)
          return <div key={`${citation.evidence_id}:${citation.paragraph_id}:${i}`}><blockquote>{citation.quote}</blockquote>
            {citedSource ? <button onClick={() => onSource(citedSource)}>{citation.evidence_id} · {citedSource.title} ↗</button> : <small>{citation.evidence_id} · 来源记录缺失</small>}
          </div>
        })}
      </details>
      return <small key={id}>{id} · 来源记录缺失</small>
    })}</div>}
    {assumptionIds.map(id => { const item = run.world?.assumptions.find(a => a.id === id)
      return <details key={id}><summary>模型假设 {id}{item ? ` · ${item.content}` : ' · 记录缺失'}</summary>{item && <><p>{item.content}</p><p>{item.rationale}</p><small>关联记录 {item.parent_ids.join(' · ') || '无'}</small></>}</details>
    })}
    {simulationIds.map(id => { const item = run.simulation.find(s => s.id === id)
      return <details key={id}><summary>模拟结果 {id}{item ? ` · 第 ${item.round} 轮` : ' · 记录缺失'}</summary>{item && <><p>{item.summary}</p>{Object.entries(item.state_changes).map(([key, value]) => <p key={key}><strong>{key}</strong> · {value}</p>)}</>}</details>
    })}
  </div>
}
