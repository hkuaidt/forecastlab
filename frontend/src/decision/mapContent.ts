import type { Evidence, Issue } from '../types'
import { sourceForm } from '../contentLabels'

// Only exact, generic source metadata is grouped. Any additional concrete claim
// stays on the canvas so a substantive contradiction cannot disappear here.
const sourceQualityStatements = new Set([
  '只有摘要', '仅有摘要', '仅为摘要', '只有搜索摘要', '仅有搜索摘要', '仅为搜索摘要',
  '来源只有摘要', '证据只有摘要', '来源仅为搜索摘要', '证据仅为搜索摘要',
  '所有来源均为搜索摘要', '全部来源均为搜索摘要', '当前证据均为搜索摘要',
  '未取得完整正文', '未获取完整正文', '未取得网页正文', '缺少完整正文',
  '发布时间未知', '发布日期未知', '日期未知', '来源日期未知',
  '来源发布时间未知', '来源发布日期未知', '证据发布日期未知', '缺少发布日期',
  '来源缺少发布日期', '无法确认发布时间', '无法确认发布日期',
])

export function isSourceQualityIssue(issue: Issue): boolean {
  const statements = `${issue.claim}；${issue.explanation}`.split(/[，,。；;、\n]/)
    .map(value => value.replace(/[\s：:]/g, '')).filter(Boolean)
  return statements.length > 0 && statements.every(value => sourceQualityStatements.has(value))
}

export function sourceContent(evidence: Evidence): string {
  const claim = evidence.claim.trim()
  const placeholder = /^(?:待核查|待验证|待核实|待确认|待分析|尚待核查|暂无|暂无论点|暂无结论)[。.!！]?$/
  return (claim && !placeholder.test(claim) ? claim : evidence.excerpt) || '此来源尚未保存正文或可展示论点。'
}

// Canvas previews quote a saved passage, never a generated summary. The source
// reader still fetches the complete registered snapshot through /passages.
export function sourcePreview(evidence: Evidence): string {
  const passage = evidence.passages?.find(item => item.text.trim())?.text
  return Array.from(passage || evidence.excerpt || sourceContent(evidence)).slice(0, 420).join('')
}

export function sourcePreviewLabel(evidence: Evidence): string {
  return {
    body: '原文选段 · 展开保存全文',
    snippet: '搜索摘要选段 · 展开保存摘要',
    excerpt: '导入节选 · 展开保存节选',
    unknown: '保存材料选段 · 展开保存材料',
  }[sourceForm(evidence)]
}
