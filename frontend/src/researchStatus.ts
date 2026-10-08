import type { ModelCall, Run } from './types'

export function reportFailed(run: Run | null): boolean {
  return !!run && ((['failed', 'partial', 'interrupted'].includes(run.status) && run.failed_stage === 'forecast')
    || (!!run.forecast && !run.forecast.probabilities
      && run.forecast.limitations.some(item => item.includes('校验未通过'))))
}
export function reportFailureReason(run: Run): string {
  return run.errors.at(-1) || run.forecast_attempts?.at(-1)?.validation_errors?.at(-1)
    || run.forecast?.limitations.find(item => item.includes('校验未通过')) || '报告未通过内容与来源检查。'
}
export function requestSeconds(records: ModelCall[], includeReserved = false): number {
  const calls = [...new Map(records.map(call => [call.request_id, call])).values()]
  const intervals = calls.flatMap(call => {
    const start = Date.parse(call.started_at || '') / 1000
    if (!Number.isFinite(start)) return []
    const duration = includeReserved && call.status === 'reserved'
      ? Math.max(0, Date.now() / 1000 - start) : Math.max(0, call.elapsed_seconds)
    return [[start, start + duration]]
  }).sort((a, b) => a[0] - b[0])
  let total = 0, end = -Infinity
  for (const [start, stop] of intervals) {
    total += Math.max(0, stop - Math.max(start, end))
    end = Math.max(end, stop)
  }
  return total
}
export function recordedSeconds(run: Run | null): number {
  if (!run) return 0
  const base = typeof run.active_seconds === 'number' && run.active_seconds > 0
    ? run.active_seconds
    : Object.values(run.stage_durations || {}).reduce((total, value) => total + Math.max(0, value), 0)
  return Math.max(base, requestSeconds(run.model_calls || [], ['queued', 'running'].includes(run.status)))
}
