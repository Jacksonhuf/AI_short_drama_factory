import { useState } from 'react'
import { Alert, Button, message } from 'antd'
import { CheckCircleOutlined } from '@ant-design/icons'
import { ApiError, StudioProductionRunsService } from '../../../services/generated'
import { useProductionRuns } from './useProductionRuns'

type Props = {
  chapterId: string
  enabled?: boolean
  onConfirmed?: () => Promise<void> | void
}

function gateKey(runId: string, stepId: string): string {
  const suffix = typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}`
  return `confirm-preparation:${runId}:${stepId}:${suffix}`
}

/**
 * 在分镜准备页识别章节活跃 run 的 preparation gate，并提供不可绕过的确认继续入口。
 */
export function ProductionPreparationGateBanner({ chapterId, enabled = true, onConfirmed }: Props) {
  const production = useProductionRuns(chapterId, enabled)
  const [confirming, setConfirming] = useState(false)
  const run = production.run
  const step = run?.steps?.find((item) => item.id === run.current_step_id)

  if (!run || run.status !== 'waiting_human' || step?.stage_key !== 'human_preparation_gate') {
    return null
  }

  const confirm = async () => {
    setConfirming(true)
    try {
      const response = await StudioProductionRunsService.confirmProductionRunStepApiV1StudioProductionRunsRunIdStepsStepIdConfirmPost({
        runId: run.id,
        stepId: step.id,
        requestBody: {
          expected_lock_version: run.lock_version,
          idempotency_key: gateKey(run.id, step.id),
        },
      })
      if (response.data) production.applyRun(response.data)
      message.success('分镜准备已确认，自动生产将继续推进')
      await onConfirmed?.()
      await production.loadList()
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        const reason = typeof error.body?.message === 'string' ? error.body.message : '准备状态尚未满足'
        message.warning(`暂时无法继续：${reason}`)
        await production.refresh()
      } else {
        message.error('确认 preparation gate 失败')
      }
    } finally {
      setConfirming(false)
    }
  }

  return (
    <Alert
      className="mb-3"
      type="warning"
      showIcon
      message="自动生产正在等待人工确认分镜准备"
      description="完成本章镜头的基础信息、资产和对白确认后，点击继续。后端会再次校验准备状态，不会跳过未完成项。"
      action={
        <Button type="primary" icon={<CheckCircleOutlined />} loading={confirming} onClick={() => void confirm()}>
          确认准备完成并继续
        </Button>
      }
    />
  )
}
