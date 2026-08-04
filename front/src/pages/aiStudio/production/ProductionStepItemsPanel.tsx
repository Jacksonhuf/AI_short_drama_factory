import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, List, Pagination, Popconfirm, Space, Tag, Typography, message } from 'antd'
import { ReloadOutlined, StopOutlined } from '@ant-design/icons'
import type { ProductionRunRead, ProductionRunStepItemRead, ProductionRunStepRead } from '../../../services/generated'
import { ApiError, StudioProductionRunsService } from '../../../services/generated'

const PAGE_SIZE = 10

const ITEM_STATUS_META: Record<ProductionRunStepItemRead['status'], { label: string; color: string }> = {
  pending: { label: '等待中', color: 'default' },
  running: { label: '运行中', color: 'processing' },
  waiting: { label: '等待中', color: 'warning' },
  partial: { label: '部分完成', color: 'warning' },
  succeeded: { label: '已完成', color: 'success' },
  failed: { label: '失败', color: 'error' },
  skipped: { label: '已跳过', color: 'default' },
  cancelled: { label: '已取消', color: 'default' },
}

type Props = {
  run: ProductionRunRead
  step: ProductionRunStepRead
  onRunRefresh: () => Promise<void>
  onOpenWorkspace?: () => void
}

function idempotencyKey(action: string, itemId: string): string {
  const suffix = typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}`
  return `${action}:${itemId}:${suffix}`
}

function targetLabel(item: ProductionRunStepItemRead): string {
  const [, frameType] = item.target_key.split(':')
  const frameLabel = frameType === 'first' ? '首帧' : frameType === 'last' ? '尾帧' : frameType === 'key' ? '关键帧' : null
  return frameLabel ? `镜头 ${item.entity_id} · ${frameLabel}` : `镜头 ${item.entity_id}`
}

/**
 * 分页展示 fan-out/barrier 冻结项，并只对当前失败项开放定向 retry/skip。
 */
export function ProductionStepItemsPanel({ run, step, onRunRefresh, onOpenWorkspace }: Props) {
  const [items, setItems] = useState<ProductionRunStepItemRead[]>([])
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [mutatingItemId, setMutatingItemId] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  const loadItems = useCallback(async (targetPage: number) => {
    setLoading(true)
    try {
      const response = await StudioProductionRunsService.listProductionRunStepItemsApiV1StudioProductionRunsRunIdStepsStepIdItemsGet({
        runId: run.id,
        stepId: step.id,
        page: targetPage,
        pageSize: PAGE_SIZE,
      })
      setItems(response.data?.items ?? [])
      setTotal(response.data?.pagination.total ?? 0)
      setLoadError(null)
    } catch {
      setLoadError('读取阶段项失败')
    } finally {
      setLoading(false)
    }
  }, [run.id, step.id])

  useEffect(() => {
    setPage(1)
    void loadItems(1)
  }, [loadItems, step.id])

  const mutateItem = async (item: ProductionRunStepItemRead, action: 'retry' | 'skip') => {
    setMutatingItemId(item.id)
    const requestBody = {
      expected_lock_version: run.lock_version,
      idempotency_key: idempotencyKey(action, item.id),
    }
    try {
      if (action === 'retry') {
        await StudioProductionRunsService.retryProductionRunStepItemApiV1StudioProductionRunsRunIdStepsStepIdItemsItemIdRetryPost({
          runId: run.id,
          stepId: step.id,
          itemId: item.id,
          requestBody,
        })
      } else {
        await StudioProductionRunsService.skipProductionRunStepItemApiV1StudioProductionRunsRunIdStepsStepIdItemsItemIdSkipPost({
          runId: run.id,
          stepId: step.id,
          itemId: item.id,
          requestBody,
        })
      }
      message.success(action === 'retry' ? '已定向重试该镜头项' : '已跳过该镜头项')
      await Promise.all([onRunRefresh(), loadItems(page)])
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        message.warning('运行状态已变化，已刷新最新状态')
        await onRunRefresh()
      } else {
        message.error(action === 'retry' ? '重试失败' : '跳过失败')
      }
    } finally {
      setMutatingItemId(null)
    }
  }

  return (
    <Card
      size="small"
      title={`${step.stage_key === 'video_readiness' ? '视频准备度' : '阶段'}明细（${total}）`}
      extra={
        <Space>
          {onOpenWorkspace ? <Button size="small" onClick={onOpenWorkspace}>前往工作室处理</Button> : null}
          <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={() => void loadItems(page)}>刷新</Button>
        </Space>
      }
    >
      {loadError ? <Alert className="mb-3" type="warning" showIcon message={loadError} /> : null}
      <List
        loading={loading}
        dataSource={items}
        locale={{ emptyText: '暂无阶段项' }}
        renderItem={(item) => {
          const status = ITEM_STATUS_META[item.status]
          const retryable = run.status === 'waiting_human' &&
            run.current_step_id === step.id &&
            ['failed', 'cancelled'].includes(item.status)
          return (
            <List.Item
              actions={retryable ? [
                <Button
                  key="retry"
                  size="small"
                  type="primary"
                  icon={<ReloadOutlined />}
                  loading={mutatingItemId === item.id}
                  disabled={!!mutatingItemId}
                  onClick={() => void mutateItem(item, 'retry')}
                >
                  重试
                </Button>,
                <Popconfirm
                  key="skip"
                  title="确认跳过该镜头项？"
                  description="跳过后该目标不会再次生成，barrier 将按剩余项重新结算。"
                  onConfirm={() => void mutateItem(item, 'skip')}
                >
                  <Button size="small" danger icon={<StopOutlined />} disabled={!!mutatingItemId}>跳过</Button>
                </Popconfirm>,
              ] : undefined}
            >
              <List.Item.Meta
                title={
                  <Space wrap>
                    <Typography.Text>{targetLabel(item)}</Typography.Text>
                    <Tag color={status.color}>{status.label}</Tag>
                    <Typography.Text type="secondary" className="text-xs">尝试 {item.attempt}</Typography.Text>
                  </Space>
                }
                description={
                  item.blocked_reasons.length > 0 ? (
                    <div className="mt-2 space-y-1">
                      {item.blocked_reasons.map((reason, index) => (
                        <Alert
                          key={`${item.id}-reason-${index}`}
                          type="warning"
                          showIcon
                          message={String(reason.message ?? reason.code ?? '该镜头尚未满足条件')}
                          description={reason.code ? `原因代码：${String(reason.code)}` : undefined}
                        />
                      ))}
                    </div>
                  ) : (
                    <Typography.Text type="secondary">当前没有阻断原因</Typography.Text>
                  )
                }
              />
            </List.Item>
          )
        }}
      />
      {total > PAGE_SIZE ? (
        <div className="mt-3 flex justify-end">
          <Pagination
            size="small"
            current={page}
            pageSize={PAGE_SIZE}
            total={total}
            showSizeChanger={false}
            onChange={(nextPage) => {
              setPage(nextPage)
              void loadItems(nextPage)
            }}
          />
        </div>
      ) : null}
    </Card>
  )
}
