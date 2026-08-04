CREATE TABLE IF NOT EXISTS chapter_production_runs (
  id VARCHAR(64) NOT NULL COMMENT '章节生产运行 ID',
  project_id VARCHAR(64) NOT NULL COMMENT '项目 ID',
  chapter_id VARCHAR(64) NOT NULL COMMENT '章节 ID',
  preset_key VARCHAR(64) NOT NULL COMMENT '固定工作流预设',
  manifest_version VARCHAR(32) NOT NULL COMMENT 'manifest 版本',
  manifest_snapshot JSON NOT NULL COMMENT '创建时展开的完整步骤图',
  config_snapshot JSON NOT NULL COMMENT '运行配置快照',
  input_snapshot JSON NOT NULL COMMENT '章节输入与版本快照',
  target_snapshot_hash VARCHAR(64) NULL COMMENT '冻结目标集合摘要',
  status VARCHAR(32) NOT NULL DEFAULT 'draft' COMMENT '运行状态',
  active_chapter_id VARCHAR(64)
    GENERATED ALWAYS AS (
      CASE
        WHEN status IN ('draft','running','waiting_human','paused','failed') THEN chapter_id
        ELSE NULL
      END
    ) STORED COMMENT '活跃状态章节唯一槽',
  current_step_id VARCHAR(64) NULL COMMENT '当前步骤 ID',
  lock_version BIGINT NOT NULL DEFAULT 0 COMMENT 'API 乐观锁版本',
  transition_version BIGINT NOT NULL DEFAULT 0 COMMENT '状态迁移版本',
  cancel_requested BOOLEAN NOT NULL DEFAULT FALSE COMMENT '是否请求取消',
  error_code VARCHAR(64) NULL COMMENT '标准错误码',
  error_message TEXT NULL COMMENT '错误摘要',
  last_reconciled_at DATETIME(6) NULL COMMENT '最近对账时间',
  started_at DATETIME(6) NULL,
  finished_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_chapter_production_runs_active_chapter (active_chapter_id),
  KEY ix_chapter_production_runs_chapter_created (chapter_id, created_at),
  KEY ix_chapter_production_runs_status_updated (status, updated_at),
  KEY ix_chapter_production_runs_reconcile (status, last_reconciled_at, updated_at),
  CONSTRAINT fk_chapter_production_runs_project
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE,
  CONSTRAINT fk_chapter_production_runs_chapter
    FOREIGN KEY (chapter_id) REFERENCES chapters (id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='章节生产运行';

CREATE TABLE IF NOT EXISTS chapter_production_run_steps (
  id VARCHAR(64) NOT NULL COMMENT '步骤 ID',
  run_id VARCHAR(64) NOT NULL COMMENT '生产运行 ID',
  stage_key VARCHAR(64) NOT NULL COMMENT '稳定阶段标识',
  sequence INT NOT NULL COMMENT 'manifest 显示顺序',
  adapter_version VARCHAR(32) NOT NULL DEFAULT 'v1',
  execution_mode VARCHAR(32) NOT NULL COMMENT 'linear/fan_out/barrier/gate',
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  attempt INT NOT NULL DEFAULT 0,
  idempotency_key VARCHAR(128) NOT NULL,
  input_snapshot JSON NOT NULL,
  target_snapshot JSON NOT NULL,
  output_summary JSON NOT NULL,
  blocked_reasons JSON NOT NULL,
  gate_snapshot_hash VARCHAR(64) NULL,
  confirmed_by VARCHAR(64) NULL,
  confirmed_at DATETIME(6) NULL,
  lock_version BIGINT NOT NULL DEFAULT 0,
  started_at DATETIME(6) NULL,
  finished_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_production_run_steps_sequence (run_id, sequence),
  UNIQUE KEY uq_production_run_steps_idempotency (run_id, idempotency_key),
  KEY ix_production_run_steps_run_status_sequence (run_id, status, sequence),
  CONSTRAINT fk_production_run_steps_run
    FOREIGN KEY (run_id) REFERENCES chapter_production_runs (id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='章节生产运行步骤';

CREATE TABLE IF NOT EXISTS chapter_production_run_step_items (
  id VARCHAR(64) NOT NULL COMMENT 'fan-out 项 ID',
  step_id VARCHAR(64) NOT NULL COMMENT '步骤 ID',
  entity_type VARCHAR(32) NOT NULL,
  entity_id VARCHAR(64) NOT NULL,
  target_key VARCHAR(128) NOT NULL,
  entity_version VARCHAR(64) NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  attempt INT NOT NULL DEFAULT 0,
  idempotency_key VARCHAR(128) NOT NULL,
  blocked_reasons JSON NOT NULL,
  output_ref JSON NOT NULL,
  lock_version BIGINT NOT NULL DEFAULT 0,
  started_at DATETIME(6) NULL,
  finished_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_production_step_items_target (step_id, target_key),
  UNIQUE KEY uq_production_step_items_idempotency (step_id, idempotency_key),
  KEY ix_production_step_items_step_status (step_id, status),
  KEY ix_production_step_items_entity (entity_type, entity_id),
  CONSTRAINT fk_production_step_items_step
    FOREIGN KEY (step_id) REFERENCES chapter_production_run_steps (id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='章节生产运行 fan-out 项';

CREATE TABLE IF NOT EXISTS production_run_task_bindings (
  id VARCHAR(64) NOT NULL COMMENT '任务绑定 ID',
  run_id VARCHAR(64) NOT NULL,
  step_id VARCHAR(64) NOT NULL,
  item_id VARCHAR(64) NULL,
  item_scope_id VARCHAR(64)
    GENERATED ALWAYS AS (IFNULL(item_id, '')) STORED COMMENT '使线性步骤 NULL item 也可唯一',
  task_id VARCHAR(64) NOT NULL,
  attempt INT NOT NULL,
  task_kind VARCHAR(64) NOT NULL,
  binding_role VARCHAR(32) NOT NULL,
  terminal_status VARCHAR(32) NULL,
  notified_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_production_run_task_bindings_task (task_id),
  UNIQUE KEY uq_production_run_task_bindings_attempt
    (step_id, item_scope_id, attempt, task_kind),
  KEY ix_production_run_task_bindings_run_step (run_id, step_id),
  KEY ix_production_run_task_bindings_task_notified (task_id, notified_at),
  CONSTRAINT fk_production_run_task_bindings_run
    FOREIGN KEY (run_id) REFERENCES chapter_production_runs (id) ON DELETE CASCADE,
  CONSTRAINT fk_production_run_task_bindings_step
    FOREIGN KEY (step_id) REFERENCES chapter_production_run_steps (id) ON DELETE CASCADE,
  CONSTRAINT fk_production_run_task_bindings_item
    FOREIGN KEY (item_id) REFERENCES chapter_production_run_step_items (id) ON DELETE CASCADE,
  CONSTRAINT fk_production_run_task_bindings_task
    FOREIGN KEY (task_id) REFERENCES generation_tasks (id) ON DELETE RESTRICT
) ENGINE=InnoDB COMMENT='工作流 GenerationTask 尝试历史';

CREATE TABLE IF NOT EXISTS production_run_transitions (
  id VARCHAR(64) NOT NULL COMMENT '迁移审计 ID',
  run_id VARCHAR(64) NOT NULL,
  step_id VARCHAR(64) NULL,
  transition_version BIGINT NOT NULL,
  event_type VARCHAR(32) NOT NULL,
  from_status VARCHAR(32) NULL,
  to_status VARCHAR(32) NOT NULL,
  actor_type VARCHAR(32) NOT NULL,
  actor_id VARCHAR(64) NULL,
  idempotency_key VARCHAR(128) NOT NULL,
  request_hash VARCHAR(64) NOT NULL,
  event_payload JSON NOT NULL,
  result_snapshot JSON NOT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_production_run_transitions_version (run_id, transition_version),
  UNIQUE KEY uq_production_run_transitions_idempotency (run_id, idempotency_key),
  KEY ix_production_run_transitions_run_created (run_id, created_at),
  CONSTRAINT fk_production_run_transitions_run
    FOREIGN KEY (run_id) REFERENCES chapter_production_runs (id) ON DELETE CASCADE,
  CONSTRAINT fk_production_run_transitions_step
    FOREIGN KEY (step_id) REFERENCES chapter_production_run_steps (id) ON DELETE SET NULL
) ENGINE=InnoDB COMMENT='生产运行状态迁移与幂等审计';
