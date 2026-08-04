SET NAMES utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS script_task_applications (
  id VARCHAR(64) NOT NULL COMMENT '应用记录 ID',
  task_id VARCHAR(64) NOT NULL COMMENT '已应用的 script_write 任务',
  chapter_id VARCHAR(64) NOT NULL COMMENT '目标章节 ID',
  target_field VARCHAR(32) NOT NULL COMMENT 'raw_text / condensed_text',
  idempotency_key VARCHAR(128) NOT NULL COMMENT '客户端幂等键',
  expected_chapter_updated_at DATETIME(6) NOT NULL COMMENT '请求携带的章节版本',
  chapter_updated_at DATETIME(6) NOT NULL COMMENT '应用后的章节版本',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_script_task_applications_task_id (task_id),
  UNIQUE KEY uq_script_task_applications_idempotency_key (idempotency_key),
  KEY ix_script_task_applications_chapter_id (chapter_id),
  KEY ix_script_task_applications_task_id (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='AI 剧本任务结果显式应用审计';
