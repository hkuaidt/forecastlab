import type { Run } from './types'

export function reportFailed(run: Run | null): boolean {
  return !!run && ((['failed', 'partial', 'interrupted'].includes(run.status) && run.failed_stage === 'forecast')
    || (!!run.forecast && !run.forecast.probabilities
      && run.forecast.limitations.some(item => item.includes('校验未通过'))))
}
export function reportFailureReason(run: Run): string {
  return run.errors.at(-1) || run.forecast_attempts?.at(-1)?.validation_errors?.at(-1)
    || run.forecast?.limitations.find(item => item.includes('校验未通过')) || '报告未通过内容与来源检查。'
}
export function recordedSeconds(run: Run | null): number {
  if (!run) return 0
  if (typeof run.active_seconds === 'number' && run.active_seconds > 0) return run.active_seconds
  return Object.values(run.stage_durations || {}).reduce((total, value) => total + Math.max(0, value), 0)
}
