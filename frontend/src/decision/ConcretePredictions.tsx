import type { Evidence, Run } from '../types'
import { RecordReferences } from './RecordReferences'

export function ConcretePredictions({run,onSource,scenarioName}:{run:Run;onSource:(e:Evidence)=>void;scenarioName?:string}) {
  const items=(run.forecast?.predictions || []).filter(p=>!scenarioName || p.scenario_names.includes(scenarioName))
  if (!items.length) return null
  return <section className="scenario-comparison" aria-label="具体预测与验证节点"><h3>具体预测与验证节点</h3>
    <p className="subtle">以下是条件化预测，日期为观察截止点；判据中的数量或阈值由模型设定，不代表来源已有承诺或实测结果。</p>
    {[...items].sort((a,b)=>a.by_date.localeCompare(b.by_date)).map(p=><article key={p.id} className="scenario-detail">
      <div className="section-line"><h4>{p.actor}：{p.action}</h4><strong>{p.by_date} 前观察</strong></div>
      <p className="subtle">{p.id} · 对应情景：{p.scenario_names.join(' / ')}</p>
      <h5>预期看到的具体变化</h5><p>{p.observable_result}</p>
      <h5>为什么可能发生</h5><p>{p.mechanism}</p>
      <h5>用什么材料核对</h5><p>{p.verification}</p>
      <h5>什么会推翻这条预测</h5><p>{p.falsifier}</p>
      <RecordReferences run={run} evidenceIds={p.evidence_ids} assumptionIds={p.assumption_ids} simulationIds={p.simulation_ids} onSource={onSource}/>
    </article>)}
  </section>
}
