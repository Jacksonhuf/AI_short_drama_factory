import { Alert, Card, Collapse, Descriptions, Empty, Space, Tag, Typography } from 'antd'
import type { ScriptWritingCandidate } from './useScriptWritingTask'

type Props = {
  candidate: ScriptWritingCandidate | null
}

const NOTE_GROUPS: Array<{ key: keyof ScriptWritingCandidate; label: string; color: string }> = [
  { key: 'character_notes', label: '人物提示', color: 'purple' },
  { key: 'continuity_notes', label: '连续性提示', color: 'blue' },
  { key: 'assumptions', label: '生成假设', color: 'gold' },
  { key: 'review_questions', label: '待确认问题', color: 'orange' },
  { key: 'change_summary', label: '改动摘要', color: 'cyan' },
]

/**
 * 统一展示结构化写作候选，将正文、分集大纲和审阅提示保持为可扫描的信息层级。
 */
export function ScriptCandidateReview({ candidate }: Props) {
  if (!candidate) return <Empty description="任务完成后将在这里显示结构化候选" />

  const noteItems = NOTE_GROUPS.flatMap((group) => {
    const values = candidate[group.key]
    if (!Array.isArray(values) || values.length === 0) return []
    return [{
      key: group.key,
      label: `${group.label}（${values.length}）`,
      children: (
        <Space direction="vertical" size={6}>
          {values.map((value, index) => (
            <div key={`${group.key}-${index}`} className="text-sm text-slate-700">
              <Tag color={group.color}>{index + 1}</Tag>
              {String(value)}
            </div>
          ))}
        </Space>
      ),
    }]
  })

  return (
    <div className="space-y-4">
      <Descriptions size="small" column={1} bordered>
        <Descriptions.Item label="候选标题">{candidate.title}</Descriptions.Item>
        <Descriptions.Item label="内容摘要">{candidate.summary}</Descriptions.Item>
      </Descriptions>

      {candidate.episode_outline.length > 0 ? (
        <Card size="small" title={`分集大纲（${candidate.episode_outline.length}）`}>
          <Space direction="vertical" size={8} className="w-full">
            {candidate.episode_outline.map((item) => (
              <Alert
                key={`${item.episode}-${item.title}`}
                type="info"
                showIcon
                message={`第 ${item.episode} 集 · ${item.title}`}
                description={item.summary}
              />
            ))}
          </Space>
        </Card>
      ) : null}

      {noteItems.length > 0 ? <Collapse size="small" items={noteItems} /> : null}

      <Card
        size="small"
        title="候选正文"
        bodyStyle={{ maxHeight: 420, overflow: 'auto', background: '#f8fafc' }}
      >
        <Typography.Paragraph
          copyable={{ text: candidate.script_text }}
          style={{ whiteSpace: 'pre-wrap', marginBottom: 0, lineHeight: 1.8 }}
        >
          {candidate.script_text}
        </Typography.Paragraph>
      </Card>
    </div>
  )
}
