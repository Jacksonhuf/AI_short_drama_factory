import { useCallback, useEffect, useRef, useState } from 'react'
import type { ScriptWriteRequest, TaskResultRead } from '../../../services/generated'
import { FilmService, ScriptProcessingService } from '../../../services/generated'

export type ScriptWritingCandidate = {
  mode: ScriptWriteRequest['mode']
  title: string
  summary: string
  script_text: string
  episode_outline: Array<{ episode: number; title: string; summary: string }>
  character_notes: string[]
  continuity_notes: string[]
  assumptions: string[]
  review_questions: string[]
  change_summary: string[]
}

const TERMINAL_TASK_STATUSES = new Set(['succeeded', 'failed', 'cancelled'])

/**
 * 将任务通用 JSON 结果收窄为写作候选，避免业务 UI 直接消费不可信结构。
 */
function parseCandidate(value: unknown): ScriptWritingCandidate | null {
  if (!value || typeof value !== 'object') return null
  const row = value as Record<string, unknown>
  if (typeof row.title !== 'string' || typeof row.summary !== 'string' || typeof row.script_text !== 'string') {
    return null
  }
  const stringList = (key: string) => {
    const values = row[key]
    return Array.isArray(values) ? values.filter((item: unknown): item is string => typeof item === 'string') : []
  }
  const episodeOutline = Array.isArray(row.episode_outline)
    ? row.episode_outline.flatMap((item) => {
        if (!item || typeof item !== 'object') return []
        const outline = item as Record<string, unknown>
        return typeof outline.episode === 'number' &&
          typeof outline.title === 'string' &&
          typeof outline.summary === 'string'
          ? [{ episode: outline.episode, title: outline.title, summary: outline.summary }]
          : []
      })
    : []
  return {
    mode: (typeof row.mode === 'string' ? row.mode : 'from_scratch') as ScriptWriteRequest['mode'],
    title: row.title,
    summary: row.summary,
    script_text: row.script_text,
    episode_outline: episodeOutline,
    character_notes: stringList('character_notes'),
    continuity_notes: stringList('continuity_notes'),
    assumptions: stringList('assumptions'),
    review_questions: stringList('review_questions'),
    change_summary: stringList('change_summary'),
  }
}

/**
 * 独立轮询 script_write，不复用任务中心状态，确保关闭写作弹窗即可停止业务详情请求。
 */
export function useScriptWritingTask(enabled: boolean) {
  const [taskId, setTaskId] = useState<string | null>(null)
  const [task, setTask] = useState<TaskResultRead | null>(null)
  const [candidate, setCandidate] = useState<ScriptWritingCandidate | null>(null)
  const [creating, setCreating] = useState(false)
  const [pollError, setPollError] = useState<string | null>(null)
  const requestSequence = useRef(0)

  const reset = useCallback(() => {
    requestSequence.current += 1
    setTaskId(null)
    setTask(null)
    setCandidate(null)
    setCreating(false)
    setPollError(null)
  }, [])

  const attach = useCallback((nextTaskId: string) => {
    requestSequence.current += 1
    setTaskId(nextTaskId)
    setTask(null)
    setCandidate(null)
    setPollError(null)
  }, [])

  const start = useCallback(async (request: ScriptWriteRequest) => {
    setCreating(true)
    setPollError(null)
    try {
      const response = await ScriptProcessingService.writeScriptAsyncApiV1ScriptProcessingWriteScriptAsyncPost({
        requestBody: request,
      })
      if (!response.data?.task_id) throw new Error('任务响应缺少 task_id')
      attach(response.data.task_id)
      return response.data.task_id
    } finally {
      setCreating(false)
    }
  }, [attach])

  const poll = useCallback(async () => {
    if (!taskId) return null
    const sequence = requestSequence.current
    try {
      const response = await FilmService.getTaskResultApiV1FilmTasksTaskIdResultGet({ taskId })
      if (sequence !== requestSequence.current) return null
      const nextTask = response.data ?? null
      setTask(nextTask)
      setPollError(null)
      if (nextTask?.status === 'succeeded') {
        const nextCandidate = parseCandidate(nextTask.result)
        setCandidate(nextCandidate)
        if (!nextCandidate) setPollError('任务已完成，但候选结构无法识别')
      }
      return nextTask
    } catch {
      if (sequence === requestSequence.current) setPollError('读取写作任务失败，系统将继续重试')
      return null
    }
  }, [taskId])

  useEffect(() => {
    if (!enabled || !taskId) return
    let disposed = false
    let timer: number | null = null
    const run = async () => {
      const nextTask = await poll()
      if (!disposed && !TERMINAL_TASK_STATUSES.has(nextTask?.status ?? '')) {
        timer = window.setTimeout(run, 2000)
      }
    }
    void run()
    return () => {
      disposed = true
      if (timer !== null) window.clearTimeout(timer)
    }
  }, [enabled, poll, taskId])

  return { taskId, task, candidate, creating, pollError, start, attach, reset, poll }
}
