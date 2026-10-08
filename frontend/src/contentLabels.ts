import type { Evidence } from './types'

export const contentNature = {
  finding: '事实释义 · 模型提取',
  action: '模拟行动',
  step: '模拟状态变化',
  initial: '初始状态 · 含模型假设',
  assumption: '模型假设',
  scenario: '条件情景 · 主观权重未经校准',
} as const

export const simulationNotice = '以下是条件模拟，不代表已经发生的事实；引用来源只支持其记载的依据。'

export function sourceForm(source?: Evidence): 'body' | 'snippet' | 'excerpt' | 'unknown' {
  if (source?.content_kind === 'snippet' || source?.source_type === 'snippet_only') return 'snippet'
  if (source?.content_kind === 'body') return 'body'
  if (source?.content_kind === 'imported_excerpt') return 'excerpt'
  return 'unknown'
}

export function sourceFormLabel(source?: Evidence): string {
  return {body: '正文', snippet: '搜索摘要', excerpt: '导入节选', unknown: '获取形式未标明'}[sourceForm(source)]
}

export function sourceReferenceLabel(id: string, source?: Evidence): string {
  return `${id} · ${sourceFormLabel(source)}${source?.content_truncated ? ' · 保存有截断' : ''}${!source ? ' · 来源记录缺失' : ''}`
}

export function savedSourceAction(source?: Evidence): string {
  return {body: '查看已保存正文', snippet: '查看已保存摘要', excerpt: '查看已保存节选', unknown: '查看已保存材料'}[sourceForm(source)]
}
