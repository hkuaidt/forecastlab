import { reportFailed } from '../researchStatus'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import type { Run } from '../types'

export const phases = [['question','问题理解'],['evidence','证据核查'],['world','世界建模'],['simulation','主体推演'],['review','依据审查'],['forecast','预测报告']] as const
export const statusNames:Record<string,string>={queued:'等待执行',running:'推演中',completed:'已完成',scenario_only:'情景分析',insufficient_evidence:'证据不足',failed:'执行失败',partial:'部分完成',interrupted:'已中断',cancelled:'已停止'}
export type MapNode={id:string;parent?:string;phase:string;kind:string;title:string;text:string;x:number;y:number;status:string;refs?:string[];tone?:string}
export function buildMap(run:Run|null,closed:Set<string>){
 const nodes:MapNode[]=[],columns:{id:string;label:string;x:number}[]=[]
 const rounds=[...new Set((run?.simulation||[]).map(s=>s.round))].sort((a,b)=>a-b)
 const order=['question','evidence','world','simulation',...rounds.map(r=>`round:${r}`),'review','forecast']
 const summaries:Record<string,string>={question:run?.question_analysis?.normalized_question||run?.question.question||'确认研究问题与信息截至时间',evidence:run?.evidence_assessment?.summary||'检索公开来源，核对支持、反证与信息缺口',world:run?.world?.summary||'建立初始状态，识别主体、资源与约束',simulation:run?.world?.simulation_branch_reason||`${run?.world?.actors.length||0} 个主体 · ${run?.actions.length||0} 条已记录行动`,review:run?.review?`${run.review.issues.length} 条审查意见 · ${run.review.missing_evidence.length} 项缺口`:'检查引用、假设与论据',forecast:reportFailed(run)?'报告未通过检查，打开查看原因及可恢复操作':run?.forecast?.conclusion||'在审查完成后形成报告；不足时保留判断'}
 order.forEach((id,i)=>{
  const x=24+i*420,isRound=id.startsWith('round:'),round=Number(id.split(':')[1]),phase=isRound?'simulation':id,title=isRound?`第 ${round} 轮演化`:phases.find(([k])=>k===id)![1]
  const step=run?.simulation.find(s=>s.round===round)
  columns.push({id,label:isRound?`ROUND ${round}`:`STAGE ${phases.findIndex(([k])=>k===id)+1}`,x})
  nodes.push({id,phase,kind:isRound?'轮次':'阶段',title,text:isRound?step?.summary||'等待下一状态':summaries[id],x,y:140,parent:i?order[i-1]:undefined,status:isRound?'saved':(id==='forecast'&&reportFailed(run))||(run?.failed_stage===id&&['failed','partial','interrupted'].includes(run.status))?'failed':run?.stage_outputs[id]?'saved':run?.stage===id?(run.status==='running'?'active':'failed'):'waiting',tone:id==='forecast'&&run?.forecast&&!run.forecast.probabilities?'warning':undefined})
  if(!run||closed.has(id))return
  const branches:Omit<MapNode,'x'|'y'|'phase'|'status'>[]=[]
  if(id==='question')run.question_framing?.premises.filter(p=>p.user_review!=='rejected').forEach(p=>branches.push({id:`premise:${p.id}`,kind:'前提',title:p.content,text:p.treatment==='scenario_condition'?'用户指定情景条件':'待核查，尚非事实',parent:id}))
  if(id==='evidence')run.evidence.forEach(e=>branches.push({id:`source:${e.id}`,kind:'来源',title:e.title,text:e.claim||e.excerpt||e.publisher||'发布方待核查',parent:id,refs:[e.id],tone:e.content_kind==='snippet'?'warning':undefined}))
  if(id==='world')run.world?.actors.forEach(a=>branches.push({id:`actor:${a.id}`,kind:'角色',title:a.name,text:a.goal,parent:id,refs:a.visible_evidence_ids,tone:'green'}))
  if(isRound)run.actions.filter(a=>a.round===round).forEach(a=>branches.push({id:`action:${a.id}`,kind:'主体行动',title:run.world?.actors.find(v=>v.id===a.actor_id)?.name||a.actor_id,text:`${a.action}。${a.rationale_summary}`,parent:id,refs:[...a.evidence_ids,...a.assumption_ids],tone:'green'}))
  if(id==='review')run.review?.issues.forEach((v,j)=>branches.push({id:`issue:${j}`,kind:'审查意见',title:v.claim,text:v.explanation,parent:id,refs:v.affected_ids,tone:v.severity==='high'?'danger':'warning'}))
  if(id==='forecast'&&run.forecast?.probabilities)Object.entries(run.forecast.probabilities).forEach(([key,v])=>branches.push({id:`outcome:${key}`,kind:'结果分支',title:key,text:`${(v*100).toFixed(1)}% · 模型主观概率，未校准`,parent:id,tone:'green'}))
  branches.forEach((b,j)=>nodes.push({...b,x,y:500+j*340,phase,status:'saved'}))
 })
 return {nodes,columns,width:order.length*420+80,height:Math.max(1200,...nodes.map(n=>n.y+360)),rounds}
}
export function ExecutionMap({run,onInspect,selectedId}:{run:Run|null;onInspect:(node:MapNode)=>void;selectedId?:string}){
 const [zoom,setZoom]=useState(1),[closed,setClosed]=useState<Set<string>>(new Set()),[roundFilter,setRoundFilter]=useState<number|null>(null)
 const viewport=useRef<HTMLDivElement>(null),drag=useRef<{pointerId:number;x:number;y:number;left:number;top:number}|null>(null)
 const zoomRef=useRef(zoom),pendingScroll=useRef<{left:number;top:number}|null>(null)
 const [panning,setPanning]=useState(false)
 const [view,setView]=useState({left:0,top:0,width:1,height:1})
 const map=useMemo(()=>buildMap(run,closed),[run,closed]),{nodes,columns,width,height,rounds}=map
 const fit=()=>{const el=viewport.current;if(el){zoomRef.current=Math.max(.28,Math.min(1,(el.clientWidth-30)/width));pendingScroll.current=null;setZoom(zoomRef.current);el.scrollTo({left:0,top:0})}}
 useEffect(()=>{setClosed(new Set());setRoundFilter(null);const el=viewport.current;if(el){zoomRef.current=el.clientWidth<600?.9:1;pendingScroll.current=null;setZoom(zoomRef.current);el.scrollTo({left:0,top:0})}},[run?.run_id])
 useEffect(()=>{const el=viewport.current;if(!el)return;const update=()=>{if(el.clientWidth&&el.clientHeight)setView({left:el.scrollLeft,top:el.scrollTop,width:el.clientWidth,height:el.clientHeight})};const observer=new ResizeObserver(update);observer.observe(el);el.addEventListener('scroll',update);update();return()=>{observer.disconnect();el.removeEventListener('scroll',update)}},[])
 useLayoutEffect(()=>{const el=viewport.current,next=pendingScroll.current;if(el&&next){const before={left:el.scrollLeft,top:el.scrollTop};el.scrollTo(next);pendingScroll.current=null;if(drag.current){drag.current.left+=el.scrollLeft-before.left;drag.current.top+=el.scrollTop-before.top}}},[zoom])
 function changeZoom(next:number,anchor?:{x:number;y:number}){
  const el=viewport.current;if(!el)return
  next=Math.max(.28,Math.min(1.5,next));if(next===zoomRef.current)return
  const x=anchor?.x??el.clientWidth/2,y=anchor?.y??el.clientHeight/2,style=getComputedStyle(el)
  const paddingLeft=parseFloat(style.paddingLeft)||0,paddingTop=parseFloat(style.paddingTop)||0
  const previous=pendingScroll.current??{left:el.scrollLeft,top:el.scrollTop}
  const left=(previous.left+x-paddingLeft)/zoomRef.current,top=(previous.top+y-paddingTop)/zoomRef.current
  pendingScroll.current={left:left*next-x+paddingLeft,top:top*next-y+paddingTop}
  zoomRef.current=next;setZoom(next)
 }
 useEffect(()=>{
  const el=viewport.current;if(!el)return
  const wheel=(e:WheelEvent)=>{e.preventDefault();const rect=el.getBoundingClientRect(),unit=e.deltaMode===1?16:e.deltaMode===2?el.clientHeight:1;changeZoom(zoomRef.current*Math.exp(-e.deltaY*unit*.002),{x:e.clientX-rect.left-el.clientLeft,y:e.clientY-rect.top-el.clientTop})}
  el.addEventListener('wheel',wheel,{passive:false});return()=>el.removeEventListener('wheel',wheel)
 },[])
 function finishPan(e:ReactPointerEvent<HTMLDivElement>){
  if(drag.current?.pointerId!==e.pointerId)return
  drag.current=null;setPanning(false)
  if(e.currentTarget.hasPointerCapture(e.pointerId))e.currentTarget.releasePointerCapture(e.pointerId)
 }
 function beginPan(e:ReactPointerEvent<HTMLDivElement>){
  if(e.pointerType==='touch'||e.button!==2||drag.current)return
  e.preventDefault();const el=e.currentTarget
  drag.current={pointerId:e.pointerId,x:e.clientX,y:e.clientY,left:el.scrollLeft,top:el.scrollTop};setPanning(true)
  el.setPointerCapture(e.pointerId)
 }
 function movePan(e:ReactPointerEvent<HTMLDivElement>){
  const active=drag.current;if(!active||active.pointerId!==e.pointerId)return
  if(!(e.buttons&2)){finishPan(e);return}
  e.preventDefault();e.currentTarget.scrollLeft=active.left-(e.clientX-active.x);e.currentTarget.scrollTop=active.top-(e.clientY-active.y)
 }
 function toggle(id:string){setClosed(old=>{const next=new Set(old);if(next.has(id))next.delete(id);else next.add(id);return next})}
 function visitRound(round:number){setRoundFilter(round);const c=columns.find(c=>c.id===`round:${round}`);if(c)viewport.current?.scrollTo({left:Math.max(0,c.x*zoom-80),top:80,behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'instant':'smooth'})}
 const displayed=nodes.filter(n=>roundFilter===null||n.kind!=='主体行动'||run?.actions.find(a=>`action:${a.id}`===n.id)?.round===roundFilter)
 return <section className="execution-map" aria-label="事件预测思维导图" onContextMenu={e=>e.preventDefault()}><div className={`map-viewport${panning?' is-panning':''}`} ref={viewport} tabIndex={0} aria-label="可拖动推演画布" onKeyDown={e=>{if(e.target!==e.currentTarget)return;if(e.key==='+'||e.key==='='){e.preventDefault();changeZoom(zoomRef.current+.1)}if(e.key==='-'){e.preventDefault();changeZoom(zoomRef.current-.1)}if(e.key==='0'){e.preventDefault();fit()}}} onPointerDown={beginPan} onPointerMove={movePan} onPointerUp={finishPan} onPointerCancel={finishPan} onLostPointerCapture={finishPan}>
  <div style={{width:width*zoom,height:height*zoom}}><div className="map-plane" style={{width,height,transform:`scale(${zoom})`}}><div className="stage-ruler" style={{width}}>{columns.map(c=><div className="ruler-column" style={{left:c.x,width:360}} key={c.id}><span>{c.label}</span><i/><small>{c.id.startsWith('round:')?'状态演化':phases.find(([id])=>id===c.id)![1]}</small></div>)}</div>
   <svg className="map-edges" width={width} height={height} aria-hidden="true"><defs><marker id="tree-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0L8 4L0 8" fill="#ada2d0"/></marker></defs>{columns.map(c=><path className="column-guide" key={c.id} d={`M${c.x+180} 95V${height}`}/>)}{displayed.filter(n=>n.parent).map(n=>{const p=nodes.find(v=>v.id===n.parent)!;const spine=n.kind==='阶段'||n.kind==='轮次';return <path className={`map-link ${n.tone||''}`} key={n.id} d={spine?`M${p.x+360} ${p.y+140}C${p.x+385} ${p.y+140} ${n.x-24} ${n.y+140} ${n.x} ${n.y+140}`:`M${p.x+18} ${p.y+300}V${p.y+320}H${p.x-20}V${n.y+145}H${n.x}`} markerEnd="url(#tree-arrow)"/>})}</svg>
   {displayed.map(n=><article key={n.id} className={`tree-node ${n.status} ${n.tone||''} ${selectedId===n.id?'selected':''}`} style={{left:n.x,top:n.y}}><div className="node-bar"><span>{n.kind}</span><small>{n.status==='active'?'运行中':n.status==='waiting'?'等待':n.status==='failed'?'中断':'已记录'}</small>{(n.kind==='阶段'||n.kind==='轮次')&&<button onClick={()=>toggle(n.id)} aria-label={`${closed.has(n.id)?'展开':'折叠'}${n.title}分支`}>{closed.has(n.id)?'＋':'−'}</button>}</div><button className="node-content" aria-label={`${n.kind}：${n.title}`} aria-pressed={selectedId===n.id} onClick={()=>onInspect(n)}><h3>{n.title}</h3><p>{n.text}</p>{n.refs?.length?<span>{n.refs.join(' · ')} · 检查依据 ↗</span>:<span>查看记录 ↗</span>}</button></article>)}
  </div></div>
 </div><nav className="map-minimap" aria-label="推演全图导航"><div className="minimap-heading"><span>全图导航</span><small>{displayed.length} 个节点</small></div><svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-label="点击定位画布" onClick={e=>{const rect=e.currentTarget.getBoundingClientRect();viewport.current?.scrollTo({left:Math.max(0,(e.clientX-rect.left)/rect.width*width*zoom-view.width/2),top:Math.max(0,(e.clientY-rect.top)/rect.height*height*zoom-view.height/2)})}}>{displayed.map(n=><rect key={n.id} x={n.x} y={n.y} width={360} height={300} className={`mini-node ${n.tone||''} ${selectedId===n.id?'selected':''}`}/>)}<rect className="mini-viewport" x={view.left/zoom} y={view.top/zoom} width={Math.min(width,view.width/zoom)} height={Math.min(height,view.height/zoom)}/></svg><div className="minimap-jumps">{columns.map(c=><button key={c.id} aria-label={`定位${c.label}`} onClick={()=>viewport.current?.scrollTo({left:Math.max(0,c.x*zoom-view.width/3),top:Math.max(0,90*zoom)})}>{c.id.startsWith('round:')?`R${c.id.split(':')[1]}`:`S${phases.findIndex(([id])=>id===c.id)+1}`}</button>)}</div></nav><div className="map-toolbar"><div className="canvas-caption"><strong>推演结构</strong><span>阶段 → 分支 → 记录</span></div><label className="round-select">查看轮次<select aria-label="查看轮次" value={roundFilter??'all'} onChange={e=>e.target.value==='all'?setRoundFilter(null):visitRound(Number(e.target.value))}><option value="all">全部路径</option>{rounds.map(r=><option value={r} key={r}>第 {r} 轮</option>)}</select></label><div className="zoom-controls"><button onClick={()=>changeZoom(zoomRef.current-.1)} aria-label="缩小导图">−</button><output>{Math.round(zoom*100)}%</output><button onClick={()=>changeZoom(zoomRef.current+.1)} aria-label="放大导图">＋</button><button onClick={fit} aria-label="适应画布">适应</button></div></div><div className="map-explainer">右键拖动平移 · 滚轮缩放 · 左键查看节点 · 触屏可拖动与点按</div></section>
}
