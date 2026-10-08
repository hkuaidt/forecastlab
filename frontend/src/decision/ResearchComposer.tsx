import { useEffect, useState } from 'react'
import { useQuestionFraming, type QuestionFields } from '../components/useQuestionFraming'
import type { PremiseDecision } from '../types'
import { api } from '../api'
const today=()=>new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16)
export function ResearchComposer({searchReady,onClose,onCreated}:{searchReady:boolean;onClose:()=>void;onCreated:(id:string)=>void}){
 const [fields,setFields]=useState<QuestionFields>({question:'',asOf:'',resolveBy:'',resolutionRule:'',resolutionSource:'',mode:'scenario',assumptions:''})
 const [error,setError]=useState(''),[starting,setStarting]=useState(false),[choices,setChoices]=useState<Record<string,string>>({}),[answers,setAnswers]=useState<Record<string,string>>({})
 const [specificTime,setSpecificTime]=useState(false)
 const frame=useQuestionFraming(fields,setFields,setError,{scenarioOnly:true,useCurrentTime:!specificTime})
 useEffect(()=>{setChoices(Object.fromEntries((frame.framing?.premises||[]).map(p=>[p.id,p.user_review==='pending'?'':p.user_review==='rejected'?'rejected':p.treatment])));setAnswers({})},[frame.framing?.revision,frame.framing?.draft_id])
 const change=(key:keyof QuestionFields,value:string)=>setFields(f=>({...f,[key]:value}))
 async function start(){if(!frame.confirmationId)return;setStarting(true);setError('');try{const r=await api<{run_id:string}>('/runs',{method:'POST',body:JSON.stringify({confirmation_id:frame.confirmationId,evidence_mode:'online',evidence:[]})});onCreated(r.run_id)}catch(e){setError((e as Error).message)}finally{setStarting(false)}}
 const busy=frame.busy||starting
 const confirmable=!!frame.framing&&!frame.dirty&&frame.framing.status==='ready_for_confirmation'&&frame.framing.premises.every(p=>choices[p.id])&&!frame.confirmationId
 const decisions:PremiseDecision[]=(frame.framing?.premises||[]).map(p=>({premise_id:p.id,user_review:choices[p.id]==='rejected'?'rejected':'retained',treatment:choices[p.id]==='scenario_condition'?'scenario_condition':'to_verify'}))
 return <section className="research-dialog" aria-labelledby="research-title"><header><div><small>NEW RESEARCH</small><h2 id="research-title">定义一个值得推演的问题</h2></div><button aria-label="关闭新建预测" onClick={onClose}>×</button></header>
  <div className="dialog-body"><label className="input-field"><span>研究问题</span><textarea rows={3} value={fields.question} onChange={e=>change('question',e.target.value)} placeholder="如果某项政策改变，未来三个月会如何影响相关主体？" maxLength={4000}/></label>
  <div className="input-field"><div className="section-line"><span id="research-cutoff-label">信息截至</span><button type="button" disabled={busy} onClick={()=>{setSpecificTime(value=>!value);change('asOf',specificTime?'':today())}}>{specificTime?'使用当前时间':'指定历史时间'}</button></div>
   {specificTime?<><input aria-labelledby="research-cutoff-label" type="datetime-local" value={fields.asOf} disabled={busy} onChange={e=>change('asOf',e.target.value)}/><span className="subtle">历史研究请使用截至该时间已可取得的材料。</span></>:<span className="subtle">默认使用提交分析时的当前时间。</span>}
  </div>
  {!searchReady&&<p className="notice warning">Brave 搜索尚未配置密钥。可以先分析并确认问题，搜索接通后再开始联网推演。</p>}
  {error&&<p role="alert" className="notice danger">{error}</p>}
  <button className="primary" disabled={busy||!fields.question.trim()||(specificTime&&!fields.asOf)} onClick={()=>frame.analyze()}>{frame.busy?'模型正在分析…':frame.dirty?'重新分析修改后的问题':'分析问题'}</button>
  {frame.framing&&<div className="framing-review"><div className="section-line"><h3>问题理解与确认</h3><button onClick={()=>frame.reload()} disabled={busy}>重新加载草稿</button></div><p>{frame.framing.proposed_spec.question}</p><p className="subtle">本次分析信息截至：{new Date(frame.framing.proposed_spec.as_of).toLocaleString()}</p>
   {frame.dirty&&<p className="notice warning">内容已修改，请重新分析后确认。</p>}
   {frame.framing.clarifications.filter(c=>c.status==='open').map(c=><label className="input-field" key={c.id}><span>{c.question}</span><textarea rows={2} value={answers[c.id]||''} onChange={e=>setAnswers({...answers,[c.id]:e.target.value})}/></label>)}
   {!!Object.values(answers).some(v=>v.trim())&&<button disabled={busy} onClick={()=>frame.analyze(Object.entries(answers).filter(([,v])=>v.trim()).map(([id,answer])=>({clarification_id:id,answer})))}>提交补充并重新分析</button>}
   <h4>前提处理</h4>{frame.framing.premises.length?frame.framing.premises.map(p=><article className="premise-review" key={p.id}><strong>{p.content}</strong><blockquote>{p.original_span}</blockquote><p>{p.rationale}</p><label className="input-field"><span>{p.id} 前提处理</span><select value={choices[p.id]||''} disabled={busy||!!frame.confirmationId} onChange={e=>setChoices({...choices,[p.id]:e.target.value})}><option value="">请选择如何处理</option><option value="to_verify">保留并核查，不当作事实</option><option value="scenario_condition">作为我指定的情景条件</option><option value="rejected">不是我的意思，不采用</option></select></label></article>):<p className="subtle">未识别到需要确认的前提。</p>}
   {frame.confirmationId?<p className="notice">问题已确认。联网搜索、核查和推演将使用这一版本。</p>:<button className="primary" disabled={busy||!confirmable} onClick={()=>frame.confirm(decisions)}>确认问题</button>}
  </div>}
  </div><footer><span>确认时间边界和前提后，开始联网取证。</span><button className="primary" disabled={busy||!frame.confirmationId||!searchReady} onClick={start}>{starting?'正在启动…':'开始联网推演 →'}</button></footer>
 </section>
}
