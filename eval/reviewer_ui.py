"""Local-only blind-review UI for 证据评估 finding audits."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn

LABELS = ["supported", "partially_supported", "unsupported", "unclear"]


class LabelUpdate(BaseModel):
    human_label: str
    human_notes: str = ""


def save_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        tmp = Path(handle.name)
    tmp.replace(path)


def create_app(packet_path: Path, output_path: Path) -> FastAPI:
    source = json.loads(packet_path.read_text(encoding="utf-8"))
    if output_path.exists():
        current = json.loads(output_path.read_text(encoding="utf-8"))
        if [(r["case_id"], r["finding_id"]) for r in current["rows"]] != [(r["case_id"], r["finding_id"]) for r in source["rows"]]:
            raise ValueError("existing reviewer output does not match the blind packet")
    else:
        current = source
        save_atomic(output_path, current)

    app = FastAPI(title="ForecastLab Reviewer 2")

    @app.get("/", response_class=HTMLResponse)
    def home():
        return HTMLResponse(HTML)

    @app.get("/api/review")
    def review():
        return current

    @app.post("/api/rows/{index}")
    def label_row(index: int, update: LabelUpdate):
        if not 0 <= index < len(current["rows"]):
            raise HTTPException(404, "row not found")
        if update.human_label not in LABELS:
            raise HTTPException(422, "invalid label")
        current["rows"][index]["human_label"] = update.human_label
        current["rows"][index]["human_notes"] = update.human_notes.strip()
        save_atomic(output_path, current)
        done = sum(row.get("human_label") in LABELS for row in current["rows"])
        return {"ok": True, "done": done, "total": len(current["rows"])}

    @app.get("/api/status")
    def status():
        done = sum(row.get("human_label") in LABELS for row in current["rows"])
        return {"done": done, "total": len(current["rows"]), "complete": done == len(current["rows"])}

    return app


HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ForecastLab · Reviewer 2</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif;background:#f5f7f4;color:#18342d;margin:0}.wrap{max-width:1000px;margin:0 auto;padding:28px}.top{display:flex;justify-content:space-between;gap:20px;align-items:center}.card{background:#fff;border:1px solid #dfe8df;border-radius:12px;padding:24px;margin:18px 0;box-shadow:0 5px 18px #2349360b}.meta{font-size:12px;color:#71877c}.claim{font-size:20px;line-height:1.6}.quote{background:#f2f7f2;border-left:4px solid #83b89a;padding:15px 18px;margin:12px 0;white-space:pre-wrap;line-height:1.7}.source{font-size:12px;color:#71877c}.limit{color:#765b32;background:#fff7e9;padding:12px;border-radius:7px}.labels{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.labels button{padding:13px;border:1px solid #cddbd0;background:#fff;border-radius:8px;font-weight:650;cursor:pointer}.labels button.on{background:#0b7768;color:#fff;border-color:#0b7768}textarea{box-sizing:border-box;width:100%;min-height:90px;border:1px solid #d5e1d8;border-radius:8px;padding:12px;margin:14px 0}.nav{display:flex;justify-content:space-between;gap:12px}.nav button{padding:10px 18px;border:0;border-radius:7px;background:#e5eee7;cursor:pointer}.save{background:#0b7768!important;color:#fff}.progress{font-weight:700}.hint{font-size:12px;color:#7d8f86}@media(max-width:700px){.labels{grid-template-columns:1fr 1fr}.wrap{padding:15px}.claim{font-size:17px}}
</style></head><body><div class="wrap">
<div class="top"><div><h1>证据评估 · 第二位盲审</h1><div class="hint">只判断 claim 是否被 exact quote 直接支持；不要使用外部知识。</div></div><div class="progress" id="progress"></div></div>
<div class="card"><div class="meta" id="meta"></div><div class="claim" id="claim"></div><div id="quotes"></div><div class="limit" id="limit"></div></div>
<div class="labels" id="labels"></div><textarea id="notes" placeholder="简短说明判断理由（建议填写）"></textarea>
<div class="nav"><button id="prev">← 上一条</button><button class="save" id="save">保存并下一条 →</button><button id="next">下一条 →</button></div>
<div class="hint" style="margin-top:14px">快捷键：1=supported，2=partially_supported，3=unsupported，4=unclear，Ctrl/Cmd+Enter=保存并下一条。</div>
</div><script>
let data=null,index=0,choice=null;const labels=['supported','partially_supported','unsupported','unclear'];
async function load(){data=await (await fetch('/api/review')).json(); const first=data.rows.findIndex(r=>!r.human_label); index=first>=0?first:0; render()}
function render(){const r=data.rows[index];choice=r.human_label||null;document.querySelector('#meta').textContent=`${index+1}/${data.rows.length} · ${r.case_id} · ${r.finding_id} · ${r.relation}`;document.querySelector('#claim').textContent=r.claim;document.querySelector('#limit').textContent='Limitation: '+(r.limitation||'—');document.querySelector('#notes').value=r.human_notes||'';document.querySelector('#quotes').innerHTML=r.citations.map(c=>`<div class="quote"></div><div class="source"></div>`).join('');[...document.querySelectorAll('.quote')].forEach((e,i)=>e.textContent=r.citations[i].quote);[...document.querySelectorAll('.source')].forEach((e,i)=>e.textContent=`${r.citations[i].evidence_id} · ${r.citations[i].source_title||''} · ${r.citations[i].paragraph_id}`);document.querySelector('#labels').innerHTML='';labels.forEach((l,i)=>{const b=document.createElement('button');b.textContent=`${i+1}. ${l}`;if(choice===l)b.classList.add('on');b.onclick=()=>{choice=l;renderLabels()};document.querySelector('#labels').appendChild(b)});updateProgress()}
function renderLabels(){[...document.querySelectorAll('#labels button')].forEach((b,i)=>b.classList.toggle('on',labels[i]===choice))}
function updateProgress(){const done=data.rows.filter(r=>labels.includes(r.human_label)).length;document.querySelector('#progress').textContent=`${done}/${data.rows.length} 已完成`}
async function save(next=true){if(!choice){alert('请先选择标签');return}const notes=document.querySelector('#notes').value;const resp=await fetch(`/api/rows/${index}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({human_label:choice,human_notes:notes})});if(!resp.ok){alert('保存失败');return}data.rows[index].human_label=choice;data.rows[index].human_notes=notes;updateProgress();if(next&&index<data.rows.length-1){index++;render()}}
document.querySelector('#save').onclick=()=>save(true);document.querySelector('#prev').onclick=()=>{if(index>0){index--;render()}};document.querySelector('#next').onclick=()=>{if(index<data.rows.length-1){index++;render()}};document.addEventListener('keydown',e=>{if(['1','2','3','4'].includes(e.key)){choice=labels[Number(e.key)-1];renderLabels()}if((e.ctrlKey||e.metaKey)&&e.key==='Enter')save(true)});load();
</script></body></html>'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    uvicorn.run(create_app(args.packet, args.output), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
