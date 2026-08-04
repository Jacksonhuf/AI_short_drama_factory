import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Popconfirm,
  Progress,
  Select,
  Space,
  Spin,
  Steps,
  Switch,
  Tag,
  Typography,
} from 'antd'
import {
  CaretRightOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  FileSearchOutlined,
  PauseOutlined,
  PlusOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import type { ProductionPreset, ProductionRunRead, ProductionRunStatus, ProductionStepStatus } from '../../../services/generated'
import { ApiError, StudioProductionRunsService } from '../../../services/generated'
import { ACTIVE_PRODUCTION_STATUSES, useProductionRuns } from './useProductionRuns'
import { ProductionStepItemsPanel } from './ProductionStepItemsPanel'

const PRESET_LABELS: Record<ProductionPreset, string> = {
  script_assist: '剧本辅助',
  prepare_shots: '分镜准备',
  prepare_frames: '帧准备',
  controlled_video: '受控视频生成',
}

const STAGE_LABELS: Record<string, string> = {
  script_write: '生成剧本候选',
  script_review_gate: '人工审阅剧本',
  script_simplify: '精简剧本',
  script_consistency: '检查剧本一致性',
  script_optimize: '优化剧本',
  script_divide: '提取分镜',
  script_extract: '提取资产与对白',
  human_preparation_gate: '人工确认分镜准备',
  frame_prompt: '生成帧提示词',
  frame_image: '生成参考帧',
  video_readiness: '检查视频准备度',
  video_submit_gate: '确认提交视频',
  video_generation: '生成视频',
}

const FRAME_TYPE_OPTIONS = [
  { value: 'first', label: '首帧' },
  { value: 'last', label: '尾帧' },
  { value: 'key', label: '关键帧' },
] as const

const REFERENCE_MODE_OPTIONS = [
  { value: 'first', label: '仅首帧' },
  { value: 'last', label: '仅尾帧' },
  { value: 'key', label: '仅关键帧' },
  { value: 'first_last', label: '首帧 + 尾帧' },
  { value: 'first_last_key', label: '首帧 + 尾帧 + 关键帧' },
  { value: 'text_only', label: '纯文本参考' },
] as const

const VIDEO_RATIO_OPTIONS = ['16:9', '4:3', '1:1', '3:4', '9:16', '21:9'].map((value) => ({ value, label: value }))

const STATUS_META: Record<ProductionRunStatus, { label: string; color: string }> = {
  draft: { label: '草稿', color: 'default' },
  running: { label: '运行中', color: 'processing' },
  waiting_human: { label: '等待人工确认', color: 'warning' },
  paused: { label: '已暂停', color: 'orange' },
  succeeded: { label: '已完成', color: 'success' },
  failed: { label: '失败', color: 'error' },
  cancelled: { label: '已取消', color: 'default' },
}

const STEP_STATUS_MAP: Record<ProductionStepStatus, 'wait' | 'process' | 'finish' | 'error'> = {
  pending: 'wait',
  running: 'process',
  waiting: 'process',
  partial: 'process',
  succeeded: 'finish',
  failed: 'error',
  skipped: 'finish',
  cancelled: 'error',
}

type Props = {
  open: boolean
  chapterId: string | null
  chapterTitle?: string
  initialPreset?: ProductionPreset
  onClose: () => void
  onReviewScriptCandidate?: (taskId: string) => void
  onOpenPreparation?: () => void
  onOpenWorkspace?: () => void
}

type ScriptProcessingSwitchProps = {
  label: string
  checked: boolean
  disabled?: boolean
  onChange: (checked: boolean) => void
}

/** Renders one consistently styled optional script-processing control. */
function ScriptProcessingSwitch({
  label,
  checked,
  disabled = false,
  onChange,
}: ScriptProcessingSwitchProps) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-3 rounded-lg bg-white px-3 py-2 text-sm">
      <span>{label}</span>
      <Switch checked={checked} disabled={disabled} onChange={onChange} />
    </label>
  )
}

function mutationKey(action: string, runId: string): string {
  const suffix = typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}`
  return `${action}:${runId}:${suffix}`
}

function readModelSummary(run: ProductionRunRead): string {
  const config = run.config_snapshot ?? {}
  const scriptConfig = typeof config.script_write === 'object' && config.script_write ? config.script_write as Record<string, unknown> : {}
  const configured = config.model ?? config.model_name ?? scriptConfig.model ?? scriptConfig.model_name
  const stepConfigured = (run.steps ?? []).map((step) => step.output_summary?.model_name ?? step.output_summary?.model).find(Boolean)
  return String(configured ?? stepConfigured ?? '系统默认模型')
}

/**
 * 章节自动生产控制面板。它只展示运行级摘要和生命周期操作，业务候选仍回到写作界面审阅。
 */
export function ProductionRunDrawer({
  open,
  chapterId,
  chapterTitle,
  initialPreset = 'prepare_shots',
  onClose,
  onReviewScriptCandidate,
  onOpenPreparation,
  onOpenWorkspace,
}: Props) {
  const { message } = App.useApp()
  const [preset, setPreset] = useState<ProductionPreset>(initialPreset)
  const [frameTypes, setFrameTypes] = useState<Array<'first' | 'last' | 'key'>>(['first'])
  const [referenceMode, setReferenceMode] = useState('first')
  const [videoRatio, setVideoRatio] = useState('16:9')
  const [simplifyScript, setSimplifyScript] = useState(false)
  const [checkConsistency, setCheckConsistency] = useState(false)
  const [optimizeScript, setOptimizeScript] = useState(false)
  const [mutating, setMutating] = useState(false)
  const production = useProductionRuns(chapterId, open)

  /** Shows a contextual model-settings action for backend preflight failures. */
  const showMutationError = (error: unknown, fallback: string): void => {
    const detail = error instanceof ApiError && typeof error.body?.message === 'string'
      ? error.body.message
      : fallback
    message.error({
      duration: 8,
      content: (
        <Space direction="vertical" size={0}>
          <span>{detail}</span>
          {error instanceof ApiError && error.status === 503 ? (
            <Button type="link" size="small" href="/models" className="!h-auto !p-0">
              前往模型管理
            </Button>
          ) : null}
        </Space>
      ),
    })
  }

  useEffect(() => setPreset(initialPreset), [initialPreset, open])

  const currentStep = production.run?.steps?.find((step) => step.id === production.run?.current_step_id)
  const scriptTaskId = useMemo(() => {
    const step = production.run?.steps?.find((item) => item.stage_key === 'script_write')
    return typeof step?.output_summary?.task_id === 'string' ? step.output_summary.task_id : null
  }, [production.run])
  const completedSteps = production.run?.steps?.filter((step) => ['succeeded', 'skipped'].includes(step.status)).length ?? 0
  const stepCount = production.run?.steps?.length ?? 0
  const runProgress = stepCount > 0 ? Math.round((completedSteps / stepCount) * 100) : 0
  const itemDetailSteps = (production.run?.steps ?? []).filter((step) =>
    ['fan_out', 'barrier'].includes(step.execution_mode) &&
    (
      ['partial', 'failed'].includes(step.status) ||
      (
        production.run?.status === 'waiting_human' &&
        production.run.current_step_id === step.id &&
        step.stage_key === 'video_readiness'
      )
    ),
  )

  const handleConflict = async (error: unknown) => {
    if (error instanceof ApiError && error.status === 409) {
      const detail = typeof error.body?.message === 'string' ? error.body.message : '运行状态已变化'
      message.warning(`操作冲突：${detail}，已刷新最新状态`)
      await production.refresh()
      return true
    }
    return false
  }

  const createAndStart = async () => {
    if (!chapterId) return
    if (['prepare_frames', 'controlled_video'].includes(preset) && frameTypes.length === 0) {
      message.warning('请至少选择一种帧类型')
      return
    }
    setMutating(true)
    try {
      const config = preset === 'script_assist'
        ? {}
        : {
            simplify_script: simplifyScript,
            check_consistency: checkConsistency || optimizeScript,
            optimize_script: optimizeScript,
            ...(['prepare_frames', 'controlled_video'].includes(preset) ? {
            frame_types: frameTypes,
            reference_mode: referenceMode,
            video_ratio: videoRatio,
            } : {}),
          }
      const createdResponse = await StudioProductionRunsService.createProductionRunApiV1StudioChaptersChapterIdProductionRunsPost({
        chapterId,
        requestBody: {
          preset,
          config,
          idempotency_key: mutationKey('create', chapterId),
        },
      })
      const created = createdResponse.data
      if (!created) throw new Error('创建响应为空')
      production.applyRun(created)
      const startedResponse = await StudioProductionRunsService.startProductionRunApiV1StudioProductionRunsRunIdStartPost({
        runId: created.id,
        requestBody: {
          expected_lock_version: created.lock_version,
          idempotency_key: mutationKey('start', created.id),
        },
      })
      if (startedResponse.data) production.applyRun(startedResponse.data)
      message.success(`${PRESET_LABELS[preset]}运行已启动`)
      await production.loadList()
    } catch (error) {
      if (!await handleConflict(error)) showMutationError(error, '创建并启动生产运行失败')
    } finally {
      setMutating(false)
    }
  }

  const mutate = async (action: 'start' | 'pause' | 'resume' | 'cancel') => {
    const run = production.run
    if (!run) return
    setMutating(true)
    const requestBody = {
      expected_lock_version: run.lock_version,
      idempotency_key: mutationKey(action, run.id),
    }
    try {
      const response = action === 'start'
        ? await StudioProductionRunsService.startProductionRunApiV1StudioProductionRunsRunIdStartPost({ runId: run.id, requestBody })
        : action === 'pause'
          ? await StudioProductionRunsService.pauseProductionRunApiV1StudioProductionRunsRunIdPausePost({ runId: run.id, requestBody })
          : action === 'resume'
            ? await StudioProductionRunsService.resumeProductionRunApiV1StudioProductionRunsRunIdResumePost({ runId: run.id, requestBody })
            : await StudioProductionRunsService.cancelProductionRunApiV1StudioProductionRunsRunIdCancelPost({ runId: run.id, requestBody })
      if (response.data) production.applyRun(response.data)
      message.success(action === 'pause' ? '运行已暂停' : action === 'resume' ? '运行已恢复' : action === 'cancel' ? '运行已取消' : '运行已启动')
      await production.loadList()
    } catch (error) {
      if (!await handleConflict(error)) showMutationError(error, '运行状态操作失败')
    } finally {
      setMutating(false)
    }
  }

  const confirmCurrentGate = async () => {
    const run = production.run
    const step = run?.steps?.find((item) => item.id === run.current_step_id)
    if (!run || !step) return
    setMutating(true)
    try {
      const response = await StudioProductionRunsService.confirmProductionRunStepApiV1StudioProductionRunsRunIdStepsStepIdConfirmPost({
        runId: run.id,
        stepId: step.id,
        requestBody: {
          expected_lock_version: run.lock_version,
          idempotency_key: mutationKey('confirm', run.id),
        },
      })
      if (response.data) production.applyRun(response.data)
      message.success('人工闸门已确认，运行将继续推进')
      await production.loadList()
    } catch (error) {
      if (!await handleConflict(error)) message.error('当前业务状态尚未满足闸门要求')
    } finally {
      setMutating(false)
    }
  }

  const run = production.run
  const statusMeta = run ? STATUS_META[run.status] : null

  return (
    <Drawer
      open={open}
      width={620}
      title={`自动生产${chapterTitle ? ` · ${chapterTitle}` : ''}`}
      onClose={onClose}
      destroyOnClose
      extra={<Button icon={<ReloadOutlined />} onClick={() => void production.refresh()} loading={production.loading}>刷新</Button>}
    >
      <div className="space-y-5">
        <Alert
          type="info"
          showIcon
          message="固定预设会按阶段推进"
          description="生产运行只负责编排和轻量状态；剧本候选、提取确认等业务详情仍在对应页面完成。"
        />

        <div className="flex flex-wrap items-end gap-3 rounded-xl border border-slate-200 bg-slate-50 p-3">
          <div className="min-w-[220px] flex-1">
            <div className="mb-1 text-xs text-slate-500">新运行预设</div>
            <Select
              className="w-full"
              value={preset}
              options={[
                { value: 'script_assist', label: '剧本辅助 · 生成候选 → 人工审阅' },
                { value: 'prepare_shots', label: '分镜准备 · 分镜 → 提取 → 人工确认' },
                { value: 'prepare_frames', label: '帧准备 · 分镜准备 → 帧提示词 → 帧图 → readiness' },
                { value: 'controlled_video', label: '受控视频 · 帧准备 → 显式确认 → 视频生成' },
              ]}
              onChange={setPreset}
            />
          </div>
          {preset !== 'script_assist' ? (
            <div className="grid w-full grid-cols-1 gap-3 border-t border-slate-200 pt-3 sm:grid-cols-3">
              <ScriptProcessingSwitch
                label="精简剧本"
                checked={simplifyScript}
                onChange={setSimplifyScript}
              />
              <ScriptProcessingSwitch
                label="一致性检查"
                checked={checkConsistency || optimizeScript}
                disabled={optimizeScript}
                onChange={setCheckConsistency}
              />
              <ScriptProcessingSwitch
                label="优化剧本"
                checked={optimizeScript}
                onChange={(checked) => {
                  setOptimizeScript(checked)
                  if (checked) setCheckConsistency(true)
                }}
              />
              <div className="text-xs text-slate-500 sm:col-span-3">
                选中的文本处理会自动依次执行；优化依赖并自动包含一致性检查。
              </div>
            </div>
          ) : null}
          {['prepare_frames', 'controlled_video'].includes(preset) ? (
            <div className="grid w-full grid-cols-1 gap-3 border-t border-slate-200 pt-3 sm:grid-cols-3">
              <div>
                <div className="mb-1 text-xs text-slate-500">生成帧类型</div>
                <Select
                  mode="multiple"
                  className="w-full"
                  value={frameTypes}
                  options={[...FRAME_TYPE_OPTIONS]}
                  onChange={(values) => setFrameTypes(values)}
                  maxTagCount="responsive"
                />
              </div>
              <div>
                <div className="mb-1 text-xs text-slate-500">视频参考模式</div>
                <Select
                  className="w-full"
                  value={referenceMode}
                  options={[...REFERENCE_MODE_OPTIONS]}
                  onChange={setReferenceMode}
                />
              </div>
              <div>
                <div className="mb-1 text-xs text-slate-500">视频比例</div>
                <Select className="w-full" value={videoRatio} options={VIDEO_RATIO_OPTIONS} onChange={setVideoRatio} />
              </div>
            </div>
          ) : null}
          <Button
            type="primary"
            icon={<PlusOutlined />}
            loading={mutating}
            disabled={production.runs.some((item) => ACTIVE_PRODUCTION_STATUSES.has(item.status))}
            onClick={() => void createAndStart()}
          >
            创建并启动
          </Button>
        </div>

        {production.runs.length > 0 ? (
          <Select
            className="w-full"
            value={production.selectedRunId}
            onChange={production.selectRun}
            options={production.runs.map((item) => ({
              value: item.id,
              label: `${PRESET_LABELS[item.preset_key]} · ${STATUS_META[item.status].label} · ${new Date(item.created_at).toLocaleString()}`,
            }))}
          />
        ) : null}

        {production.loading && !run ? (
          <div className="py-20 text-center"><Spin size="large" /></div>
        ) : !run ? (
          <Empty description="当前章节还没有生产运行" />
        ) : (
          <>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <Space wrap>
                <Typography.Title level={5} className="!mb-0">{PRESET_LABELS[run.preset_key]}</Typography.Title>
                <Tag color={statusMeta?.color}>{statusMeta?.label}</Tag>
              </Space>
              <Space wrap>
                {run.status === 'draft' ? <Button type="primary" icon={<CaretRightOutlined />} loading={mutating} onClick={() => void mutate('start')}>启动</Button> : null}
                {run.status === 'running' ? <Button icon={<PauseOutlined />} loading={mutating} onClick={() => void mutate('pause')}>暂停</Button> : null}
                {run.status === 'paused' ? <Button type="primary" icon={<CaretRightOutlined />} loading={mutating} onClick={() => void mutate('resume')}>继续</Button> : null}
                {ACTIVE_PRODUCTION_STATUSES.has(run.status) ? (
                  <Popconfirm title="确认取消本次生产运行？" onConfirm={() => void mutate('cancel')}>
                    <Button danger icon={<CloseCircleOutlined />} disabled={mutating}>取消</Button>
                  </Popconfirm>
                ) : null}
              </Space>
            </div>

            <Progress percent={run.status === 'succeeded' ? 100 : runProgress} status={run.status === 'failed' ? 'exception' : run.status === 'succeeded' ? 'success' : 'active'} />

            <Descriptions size="small" column={1} bordered>
              <Descriptions.Item label="当前阶段">{currentStep ? STAGE_LABELS[currentStep.stage_key] ?? currentStep.stage_key : '尚未开始'}</Descriptions.Item>
              <Descriptions.Item label="模型">{readModelSummary(run)}</Descriptions.Item>
              <Descriptions.Item label="运行 ID"><Typography.Text copyable>{run.id}</Typography.Text></Descriptions.Item>
              <Descriptions.Item label="更新时间">{new Date(run.updated_at).toLocaleString()}</Descriptions.Item>
            </Descriptions>

            {run.error_message || run.error_code ? (
              <Alert type="error" showIcon message={run.error_code ?? '运行失败'} description={run.error_message ?? '未提供错误摘要'} />
            ) : null}
            {production.pollError ? <Alert type="warning" showIcon message={production.pollError} /> : null}

            {run.status === 'waiting_human' ? (
              currentStep?.stage_key === 'video_submit_gate' ? (
                <Card size="small" title="视频生成提交确认">
                  <Descriptions size="small" column={1} bordered>
                    <Descriptions.Item label="将生成镜头数">
                      {Array.isArray(run.input_snapshot?.target_shot_ids) ? run.input_snapshot.target_shot_ids.length : 0}
                    </Descriptions.Item>
                    <Descriptions.Item label="参考模式">
                      {REFERENCE_MODE_OPTIONS.find((item) => item.value === run.config_snapshot?.reference_mode)?.label ?? String(run.config_snapshot?.reference_mode ?? '—')}
                    </Descriptions.Item>
                    <Descriptions.Item label="视频比例">{String(run.config_snapshot?.video_ratio ?? '—')}</Descriptions.Item>
                  </Descriptions>
                  <Alert
                    className="mt-3"
                    type="warning"
                    showIcon
                    message="确认后将为冻结镜头创建视频生成任务"
                    description="系统会在提交前重新检查 readiness；不会自动包含运行创建后新增的镜头。"
                    action={
                      <Button
                        type="primary"
                        icon={<CheckCircleOutlined />}
                        loading={mutating}
                        onClick={() => void confirmCurrentGate()}
                      >
                        确认并生成视频
                      </Button>
                    }
                  />
                </Card>
              ) : currentStep?.stage_key === 'script_review_gate' ? (
                <Alert
                  type="warning"
                  showIcon
                  message="等待人工审阅剧本"
                  description="请审阅并显式应用本次写作候选，然后确认继续。"
                  action={scriptTaskId && onReviewScriptCandidate ? (
                    <Space direction="vertical" size={6}>
                      <Button size="small" icon={<FileSearchOutlined />} onClick={() => onReviewScriptCandidate(scriptTaskId)}>审阅候选</Button>
                      <Button size="small" type="primary" icon={<CheckCircleOutlined />} loading={mutating} onClick={() => void confirmCurrentGate()}>
                        确认剧本并继续
                      </Button>
                    </Space>
                  ) : undefined}
                />
              ) : currentStep?.stage_key === 'human_preparation_gate' ? (
                <Alert
                  type="warning"
                  showIcon
                  message="等待人工确认分镜准备"
                  description="请到分镜编辑页完成基础信息、资产和对白确认。"
                  action={onOpenPreparation ? <Button size="small" type="primary" onClick={onOpenPreparation}>前往分镜编辑</Button> : undefined}
                />
              ) : (
                <Alert
                  type="warning"
                  showIcon
                  message={`阶段需要定向处理：${currentStep ? STAGE_LABELS[currentStep.stage_key] ?? currentStep.stage_key : '当前阶段'}`}
                  description="请查看下方镜头项的阻断原因；修复业务数据后重试，或明确跳过不再处理的目标。"
                  action={onOpenWorkspace ? <Button size="small" type="primary" onClick={onOpenWorkspace}>前往工作室</Button> : undefined}
                />
              )
            ) : null}

            {itemDetailSteps.map((step) => (
              <ProductionStepItemsPanel
                key={step.id}
                run={run}
                step={step}
                onRunRefresh={production.refresh}
                onOpenWorkspace={onOpenWorkspace}
              />
            ))}

            <div>
              <Typography.Title level={5}>阶段</Typography.Title>
              <Steps
                direction="vertical"
                size="small"
                current={Math.max(0, (run.steps ?? []).findIndex((step) => step.id === run.current_step_id))}
                items={(run.steps ?? []).map((step) => ({
                  title: STAGE_LABELS[step.stage_key] ?? step.stage_key,
                  status: STEP_STATUS_MAP[step.status],
                  description: (
                    <div className="space-y-1 text-xs">
                      <div>状态：{step.status} · 尝试 {step.attempt}</div>
                      {step.blocked_reasons.length > 0 ? (
                        <div className="text-amber-700">阻塞：{step.blocked_reasons.map((reason) => String(reason.message ?? reason.code ?? '待处理')).join('；')}</div>
                      ) : null}
                      {step.output_summary?.error ? <div className="text-red-600">错误：{String(step.output_summary.error)}</div> : null}
                    </div>
                  ),
                }))}
              />
            </div>
          </>
        )}
      </div>
    </Drawer>
  )
}
