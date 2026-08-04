import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  App,
  Button,
  Card,
  Divider,
  Input,
  InputNumber,
  Modal,
  Progress,
  Select,
  Space,
  Steps,
  Tag,
  Typography,
} from 'antd'
import { CheckOutlined, FileTextOutlined, LoadingOutlined, SendOutlined } from '@ant-design/icons'
import type {
  ChapterRead,
  ScriptGenre,
  ScriptTone,
  ScriptWriteMode,
  ScriptWriteRequest,
} from '../../../services/generated'
import { ApiError, StudioChaptersService } from '../../../services/generated'
import { useNavigate } from 'react-router-dom'
import { ScriptCandidateReview } from './ScriptCandidateReview'
import { useScriptWritingTask } from './useScriptWritingTask'

const { TextArea } = Input

const MODE_OPTIONS: Array<{ value: ScriptWriteMode; label: string; description: string }> = [
  { value: 'from_scratch', label: '从零创作', description: '依据故事前提、风格和人物生成新剧本' },
  { value: 'outline_to_chapter', label: '按大纲成稿', description: '把逐集或本章大纲扩展为完整剧本' },
  { value: 'continue_writing', label: '续写', description: '承接已有上下文，并推进到指定目标' },
  { value: 'rewrite', label: '改写', description: '按明确目标改写已有文本' },
  { value: 'expand_or_compress', label: '扩写 / 压缩', description: '将已有文本调整到目标篇幅或时长' },
]

const GENRE_OPTIONS: Array<{ value: ScriptGenre; label: string }> = [
  ['drama', '剧情'], ['comedy', '喜剧'], ['romance', '爱情'], ['suspense', '悬疑'],
  ['action', '动作'], ['fantasy', '奇幻'], ['science_fiction', '科幻'], ['other', '其他'],
].map(([value, label]) => ({ value: value as ScriptGenre, label }))

const TONE_OPTIONS: Array<{ value: ScriptTone; label: string }> = [
  ['light', '轻快'], ['serious', '严肃'], ['warm', '温暖'], ['dark', '暗黑'],
  ['tense', '紧张'], ['humorous', '幽默'], ['other', '其他'],
].map(([value, label]) => ({ value: value as ScriptTone, label }))

type Props = {
  open: boolean
  projectId: string
  chapterId: string
  chapterTitle?: string
  initialTaskId?: string | null
  onClose: () => void
  onApplied?: () => Promise<void> | void
}

type WritingFormState = {
  mode: ScriptWriteMode
  premise: string
  genre: ScriptGenre
  tone: ScriptTone
  audience: string
  worldSetting: string
  episodeOutline: string
  previousContext: string
  sourceText: string
  rewriteGoal: string
  nextStageGoal: string
  targetLength: number | null
  targetDurationSeconds: number | null
  characters: string
  mustInclude: string
  mustAvoid: string
  additionalInstructions: string
}

const EMPTY_FORM: WritingFormState = {
  mode: 'from_scratch',
  premise: '',
  genre: 'drama',
  tone: 'serious',
  audience: '',
  worldSetting: '',
  episodeOutline: '',
  previousContext: '',
  sourceText: '',
  rewriteGoal: '',
  nextStageGoal: '',
  targetLength: null,
  targetDurationSeconds: null,
  characters: '',
  mustInclude: '',
  mustAvoid: '',
  additionalInstructions: '',
}

function splitLines(value: string): string[] {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
}

function createIdempotencyKey(prefix: string): string {
  const suffix = typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}`
  return `${prefix}:${suffix}`
}

/**
 * 五模式 AI 写作向导。任务只产出候选，用户必须明确选择目标字段后才会写入章节。
 */
export function ScriptWritingWizardModal({
  open,
  projectId,
  chapterId,
  chapterTitle,
  initialTaskId,
  onClose,
  onApplied,
}: Props) {
  const { message, modal } = App.useApp()
  const navigate = useNavigate()
  const [form, setForm] = useState<WritingFormState>(EMPTY_FORM)
  const [chapter, setChapter] = useState<ChapterRead | null>(null)
  const [chapterLoading, setChapterLoading] = useState(false)
  const [applyingField, setApplyingField] = useState<'raw_text' | 'condensed_text' | null>(null)
  const [appliedTarget, setAppliedTarget] = useState<'raw_text' | 'condensed_text' | null>(null)
  const applyKeysRef = useRef<Record<string, string>>({})
  const writingTask = useScriptWritingTask(open)

  const updateForm = <K extends keyof WritingFormState>(key: K, value: WritingFormState[K]) => {
    setForm((previous) => ({ ...previous, [key]: value }))
  }

  const loadChapter = useCallback(async () => {
    setChapterLoading(true)
    try {
      const response = await StudioChaptersService.getChapterApiV1StudioChaptersChapterIdGet({ chapterId })
      const nextChapter = response.data ?? null
      setChapter(nextChapter)
      return nextChapter
    } finally {
      setChapterLoading(false)
    }
  }, [chapterId])

  useEffect(() => {
    if (!open) return
    writingTask.reset()
    applyKeysRef.current = {}
    setApplyingField(null)
    setAppliedTarget(null)
    setForm(EMPTY_FORM)
    void loadChapter().then((nextChapter) => {
      if (!nextChapter) return
      const source = nextChapter.raw_text || nextChapter.condensed_text || ''
      setForm((previous) => ({
        ...previous,
        premise: nextChapter.summary || nextChapter.title,
        previousContext: source,
        sourceText: source,
      }))
    }).catch(() => message.error('读取章节版本失败，暂时无法安全应用候选'))
    if (initialTaskId) writingTask.attach(initialTaskId)
    // 每次打开重新建立任务与章节版本快照，避免复用上一次会话。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, chapterId, initialTaskId])

  const activeMode = MODE_OPTIONS.find((item) => item.value === form.mode) ?? MODE_OPTIONS[0]
  const step = writingTask.candidate ? 2 : writingTask.taskId ? 1 : 0
  const taskStatus = writingTask.task?.status
  const taskStatusLabel = taskStatus === 'succeeded'
    ? '候选已生成'
    : taskStatus === 'failed'
      ? '生成失败'
      : taskStatus === 'cancelled'
        ? '任务已取消'
        : taskStatus
          ? '正在生成'
          : '等待启动'

  const validationMessage = useMemo(() => {
    if (!form.premise.trim()) return '请填写故事前提'
    if (form.mode === 'outline_to_chapter' && splitLines(form.episodeOutline).length === 0) return '请填写至少一条大纲'
    if (form.mode === 'continue_writing' && (!form.previousContext.trim() || !form.nextStageGoal.trim())) {
      return '续写需要已有上下文和下一阶段目标'
    }
    if (form.mode === 'rewrite' && (!form.sourceText.trim() || !form.rewriteGoal.trim())) {
      return '改写需要原文和改写目标'
    }
    if (
      form.mode === 'expand_or_compress' &&
      (!form.sourceText.trim() || (!form.targetLength && !form.targetDurationSeconds))
    ) {
      return '扩写 / 压缩需要原文，以及目标字数或时长'
    }
    return null
  }, [form])

  const buildRequest = (): ScriptWriteRequest => ({
    mode: form.mode,
    project_id: projectId,
    chapter_id: chapterId,
    premise: form.premise.trim(),
    genre: form.genre,
    tone: form.tone,
    audience: form.audience.trim() || null,
    world_setting: form.worldSetting.trim() || null,
    episode_outline: splitLines(form.episodeOutline),
    previous_context: form.previousContext.trim() || null,
    source_text: form.sourceText.trim() || null,
    rewrite_goal: form.rewriteGoal.trim() || null,
    next_stage_goal: form.nextStageGoal.trim() || null,
    target_length: form.targetLength,
    target_duration_seconds: form.targetDurationSeconds,
    characters: splitLines(form.characters).map((line) => {
      const [name, ...description] = line.split(/[:：]/)
      return { name: name.trim(), description: description.join('：').trim() }
    }),
    must_include: splitLines(form.mustInclude),
    must_avoid: splitLines(form.mustAvoid),
    additional_instructions: form.additionalInstructions.trim() || null,
  })

  const startWriting = async () => {
    if (validationMessage) {
      message.warning(validationMessage)
      return
    }
    try {
      await writingTask.start(buildRequest())
      message.success('AI 写作任务已创建')
    } catch (error) {
      const detail = error instanceof ApiError && typeof error.body?.message === 'string'
        ? error.body.message
        : '创建 AI 写作任务失败'
      if (error instanceof ApiError && error.status === 503) {
        modal.error({
          title: '默认文本模型不可用',
          content: detail,
          okText: '前往模型管理',
          onOk: () => navigate('/models'),
        })
      } else {
        message.error(detail)
      }
    }
  }

  const applyCandidate = async (targetField: 'raw_text' | 'condensed_text') => {
    if (!writingTask.taskId || !writingTask.candidate || !chapter?.updated_at) return
    setApplyingField(targetField)
    const keyId = `${writingTask.taskId}:${targetField}`
    const idempotencyKey = applyKeysRef.current[keyId] ?? createIdempotencyKey('script-apply')
    applyKeysRef.current[keyId] = idempotencyKey
    try {
      const response = await StudioChaptersService.applyScriptResultApiV1StudioChaptersChapterIdApplyScriptTaskResultPost({
        chapterId,
        requestBody: {
          task_id: writingTask.taskId,
          target_field: targetField,
          expected_chapter_updated_at: chapter.updated_at,
          idempotency_key: idempotencyKey,
        },
      })
      const applied = response.data
      if (applied) {
        setChapter((previous) => previous ? { ...previous, updated_at: applied.chapter_updated_at } : previous)
      }
      setAppliedTarget(targetField)
      message.success(targetField === 'raw_text' ? '候选已应用到章节原文' : '候选已应用到精简原文')
      await onApplied?.()
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        const code = typeof error.body?.message === 'string' ? error.body.message : 'CONFLICT'
        if (code === 'CHAPTER_MODIFIED') {
          const latest = await loadChapter().catch(() => null)
          modal.warning({
            title: '章节内容已被修改',
            content: latest
              ? '已刷新到最新章节版本。请核对当前内容和候选后，再次点击应用；系统不会自动覆盖他人的编辑。'
              : '无法读取最新章节版本，请关闭后重新打开写作向导。',
          })
        } else if (code === 'TASK_RESULT_ALREADY_APPLIED') {
          message.info('这个候选已应用过，请刷新章节查看结果')
          await onApplied?.()
        } else {
          message.error(`应用冲突：${code}`)
        }
      } else {
        message.error('应用候选失败')
      }
    } finally {
      setApplyingField(null)
    }
  }

  const modeFields = (
    <>
      {form.mode === 'outline_to_chapter' ? (
        <Field label="章节 / 分集大纲" required hint="每行一条，按顺序生成">
          <TextArea rows={5} value={form.episodeOutline} onChange={(event) => updateForm('episodeOutline', event.target.value)} />
        </Field>
      ) : null}
      {form.mode === 'continue_writing' ? (
        <>
          <Field label="已有上下文" required>
            <TextArea rows={6} value={form.previousContext} onChange={(event) => updateForm('previousContext', event.target.value)} />
          </Field>
          <Field label="下一阶段目标" required>
            <Input value={form.nextStageGoal} onChange={(event) => updateForm('nextStageGoal', event.target.value)} />
          </Field>
        </>
      ) : null}
      {form.mode === 'rewrite' || form.mode === 'expand_or_compress' ? (
        <Field label="待处理原文" required>
          <TextArea rows={7} value={form.sourceText} onChange={(event) => updateForm('sourceText', event.target.value)} />
        </Field>
      ) : null}
      {form.mode === 'rewrite' ? (
        <Field label="改写目标" required>
          <Input value={form.rewriteGoal} onChange={(event) => updateForm('rewriteGoal', event.target.value)} />
        </Field>
      ) : null}
      {form.mode === 'expand_or_compress' ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Field label="目标字数" required>
            <InputNumber min={100} max={100000} className="w-full" value={form.targetLength} onChange={(value) => updateForm('targetLength', value)} />
          </Field>
          <Field label="目标时长（秒）" hint="字数与时长至少填写一项">
            <InputNumber min={10} max={14400} className="w-full" value={form.targetDurationSeconds} onChange={(value) => updateForm('targetDurationSeconds', value)} />
          </Field>
        </div>
      ) : null}
    </>
  )

  return (
    <Modal
      open={open}
      title={`AI 写剧本${chapterTitle ? ` · ${chapterTitle}` : ''}`}
      width={1040}
      onCancel={onClose}
      destroyOnClose
      footer={
        writingTask.candidate ? (
          <Space wrap>
            <Button onClick={onClose}>关闭</Button>
            <Button
              icon={<FileTextOutlined />}
              loading={applyingField === 'condensed_text'}
              disabled={!!applyingField || !!appliedTarget || !chapter?.updated_at}
              onClick={() => void applyCandidate('condensed_text')}
            >
              {appliedTarget === 'condensed_text' ? '已应用到精简原文' : '应用到精简原文'}
            </Button>
            <Button
              type="primary"
              icon={<CheckOutlined />}
              loading={applyingField === 'raw_text'}
              disabled={!!applyingField || !!appliedTarget || !chapter?.updated_at}
              onClick={() => void applyCandidate('raw_text')}
            >
              {appliedTarget === 'raw_text' ? '已应用到章节原文' : '应用到章节原文'}
            </Button>
          </Space>
        ) : (
          <Space>
            <Button onClick={onClose}>关闭</Button>
            {!writingTask.taskId ? (
              <Button type="primary" icon={<SendOutlined />} loading={writingTask.creating} onClick={() => void startWriting()}>
                创建写作任务
              </Button>
            ) : null}
          </Space>
        )
      }
    >
      <Steps
        current={step}
        className="mb-5"
        items={[
          { title: '配置写作', icon: step === 0 ? <FileTextOutlined /> : undefined },
          { title: '生成候选', icon: step === 1 ? <LoadingOutlined /> : undefined },
          { title: '审阅并应用', icon: step === 2 ? <CheckOutlined /> : undefined },
        ]}
      />

      {step === 0 ? (
        <div className="space-y-4">
          <Alert type="info" showIcon message="AI 只生成候选，不会自动覆盖章节" description="生成完成后，请审阅结构化结果，并明确选择应用到章节原文或精简原文。" />
          <Field label="写作模式" required>
            <Select
              className="w-full"
              value={form.mode}
              options={MODE_OPTIONS.map((item) => ({ value: item.value, label: item.label }))}
              onChange={(value) => updateForm('mode', value)}
            />
            <Typography.Text type="secondary" className="text-xs">{activeMode.description}</Typography.Text>
          </Field>
          <Field label="故事前提 / 核心任务" required>
            <TextArea rows={3} value={form.premise} onChange={(event) => updateForm('premise', event.target.value)} />
          </Field>
          {modeFields}
          <Divider orientation="left" plain>创作约束</Divider>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Field label="题材"><Select className="w-full" value={form.genre} options={GENRE_OPTIONS} onChange={(value) => updateForm('genre', value)} /></Field>
            <Field label="语气"><Select className="w-full" value={form.tone} options={TONE_OPTIONS} onChange={(value) => updateForm('tone', value)} /></Field>
            <Field label="目标受众"><Input value={form.audience} onChange={(event) => updateForm('audience', event.target.value)} /></Field>
            <Field label="世界观 / 场景设定"><Input value={form.worldSetting} onChange={(event) => updateForm('worldSetting', event.target.value)} /></Field>
          </div>
          <Field label="人物" hint="每行一人，格式：名字：简介">
            <TextArea rows={3} value={form.characters} onChange={(event) => updateForm('characters', event.target.value)} />
          </Field>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Field label="必须包含" hint="每行一条"><TextArea rows={3} value={form.mustInclude} onChange={(event) => updateForm('mustInclude', event.target.value)} /></Field>
            <Field label="必须避免" hint="每行一条"><TextArea rows={3} value={form.mustAvoid} onChange={(event) => updateForm('mustAvoid', event.target.value)} /></Field>
          </div>
          <Field label="补充要求"><TextArea rows={3} value={form.additionalInstructions} onChange={(event) => updateForm('additionalInstructions', event.target.value)} /></Field>
          {validationMessage ? <Alert type="warning" showIcon message={validationMessage} /> : null}
        </div>
      ) : step === 1 ? (
        <Card>
          <div className="py-10 text-center">
            <LoadingOutlined className="text-4xl text-blue-500" spin={!['failed', 'cancelled'].includes(taskStatus ?? '')} />
            <Typography.Title level={4} className="!mt-4">{taskStatusLabel}</Typography.Title>
            <Tag color={taskStatus === 'failed' ? 'error' : taskStatus === 'cancelled' ? 'default' : 'processing'}>
              {writingTask.taskId}
            </Tag>
            <Progress percent={Math.round(writingTask.task?.progress ?? 0)} status={taskStatus === 'failed' ? 'exception' : 'active'} className="mt-5 max-w-xl" />
            {writingTask.task?.error ? <Alert className="mt-4 text-left" type="error" showIcon message="生成失败" description={writingTask.task.error} /> : null}
            {writingTask.pollError ? <Alert className="mt-4 text-left" type="warning" showIcon message={writingTask.pollError} /> : null}
          </div>
        </Card>
      ) : (
        <div className="space-y-3">
          <Alert
            type="success"
            showIcon
            message="候选已生成，请先审阅再应用"
            description={`当前章节版本：${chapter?.updated_at ? new Date(chapter.updated_at).toLocaleString() : chapterLoading ? '读取中' : '不可用'}`}
          />
          <ScriptCandidateReview candidate={writingTask.candidate} />
        </div>
      )}
    </Modal>
  )
}

type FieldProps = {
  label: string
  required?: boolean
  hint?: string
  children: React.ReactNode
}

/**
 * 写作表单的统一字段容器，保证标签、必填提示和辅助文本在不同模式下保持一致。
 */
function Field({ label, required, hint, children }: FieldProps) {
  return (
    <div>
      <div className="mb-1 flex items-center gap-2 text-sm font-medium text-slate-700">
        <span>{label}</span>
        {required ? <span className="text-red-500">*</span> : null}
        {hint ? <Typography.Text type="secondary" className="text-xs font-normal">{hint}</Typography.Text> : null}
      </div>
      {children}
    </div>
  )
}
