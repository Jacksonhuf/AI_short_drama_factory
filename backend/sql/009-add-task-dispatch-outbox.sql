SET NAMES utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS task_dispatch_outbox (
  id VARCHAR(64) NOT NULL COMMENT 'Outbox ID',
  task_id VARCHAR(64) NOT NULL COMMENT '业务任务 ID；每个任务最多一个投递意图',
  status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT '投递状态：pending / dispatched / failed',
  attempt INT NOT NULL DEFAULT 0 COMMENT '已尝试投递次数',
  available_at DATETIME(6) NOT NULL COMMENT '下一次允许投递的时间',
  dispatched_at DATETIME(6) NULL COMMENT '成功投递时间',
  last_error TEXT NULL COMMENT '最近一次投递错误',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_task_dispatch_outbox_task_id (task_id),
  KEY ix_task_dispatch_outbox_status_available_at (status, available_at),
  KEY ix_task_dispatch_outbox_task_id (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='GenerationTask 可靠投递 outbox';
