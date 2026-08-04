import { useCallback, useEffect, useState } from 'react'
import type { ProductionRunRead, ProductionRunSummaryRead } from '../../../services/generated'
import { StudioProductionRunsService } from '../../../services/generated'

export const ACTIVE_PRODUCTION_STATUSES = new Set(['draft', 'running', 'waiting_human', 'paused', 'failed'])
const POLLING_PRODUCTION_STATUSES = new Set(['draft', 'running', 'waiting_human', 'paused'])

/**
 * 读取章节运行列表与详情，并在活跃运行期间独立轮询；该状态不进入通用任务中心。
 */
export function useProductionRuns(chapterId: string | null, enabled: boolean) {
  const [runs, setRuns] = useState<ProductionRunSummaryRead[]>([])
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [run, setRun] = useState<ProductionRunRead | null>(null)
  const [loading, setLoading] = useState(false)
  const [pollError, setPollError] = useState<string | null>(null)
  const pollRunId = run?.id ?? null
  const pollRunStatus = run?.status ?? null

  const loadList = useCallback(async () => {
    if (!chapterId) return []
    const response = await StudioProductionRunsService.listProductionRunsApiV1StudioChaptersChapterIdProductionRunsGet({
      chapterId,
      page: 1,
      pageSize: 30,
    })
    const items = response.data?.items ?? []
    setRuns(items)
    setSelectedRunId((previous) => {
      if (previous && items.some((item) => item.id === previous)) return previous
      return items.find((item) => ACTIVE_PRODUCTION_STATUSES.has(item.status))?.id ?? items[0]?.id ?? null
    })
    return items
  }, [chapterId])

  const loadDetail = useCallback(async (runId?: string | null) => {
    const targetId = runId ?? selectedRunId
    if (!targetId) {
      setRun(null)
      return null
    }
    const response = await StudioProductionRunsService.getProductionRunApiV1StudioProductionRunsRunIdGet({
      runId: targetId,
    })
    const next = response.data ?? null
    setRun(next)
    return next
  }, [selectedRunId])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const items = await loadList()
      const targetId = selectedRunId && items.some((item) => item.id === selectedRunId)
        ? selectedRunId
        : items.find((item) => ACTIVE_PRODUCTION_STATUSES.has(item.status))?.id ?? items[0]?.id
      if (targetId) await loadDetail(targetId)
      else setRun(null)
      setPollError(null)
    } catch {
      setPollError('读取生产运行失败')
    } finally {
      setLoading(false)
    }
  }, [loadDetail, loadList, selectedRunId])

  useEffect(() => {
    if (!enabled || !chapterId) return
    void refresh()
  }, [chapterId, enabled, refresh])

  useEffect(() => {
    if (!enabled || !selectedRunId) return
    void loadDetail(selectedRunId).catch(() => setPollError('读取运行详情失败'))
  }, [enabled, loadDetail, selectedRunId])

  useEffect(() => {
    if (!enabled || !pollRunId || !pollRunStatus || !POLLING_PRODUCTION_STATUSES.has(pollRunStatus)) return
    const timer = window.setInterval(() => {
      void Promise.all([loadDetail(pollRunId), loadList()]).catch(() => setPollError('生产状态刷新失败，系统将继续重试'))
    }, 2500)
    return () => window.clearInterval(timer)
  }, [enabled, loadDetail, loadList, pollRunId, pollRunStatus])

  const selectRun = useCallback((runId: string) => {
    setSelectedRunId(runId)
  }, [])

  const applyRun = useCallback((next: ProductionRunRead) => {
    setRun(next)
    setSelectedRunId(next.id)
    setRuns((previous) => {
      const summary = next as ProductionRunSummaryRead
      const found = previous.some((item) => item.id === next.id)
      return found ? previous.map((item) => item.id === next.id ? summary : item) : [summary, ...previous]
    })
  }, [])

  return { runs, run, selectedRunId, loading, pollError, loadList, loadDetail, refresh, selectRun, applyRun }
}
