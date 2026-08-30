"""
Consistency Celery tasks - extraction, summarization, scanning
"""
from datetime import datetime, timezone

from celery import shared_task
from sqlalchemy import select, update

from db.models_consistency_extended import ConsistencyRun
from db.session import AsyncSessionLocal


@shared_task(bind=True, name="consistency.process_body_saved")
def process_body_saved(self, payload: dict):
    """
    处理 chapter.body_saved 事件

    Args:
        payload: {
            "project_id": str,
            "chapter_id": str,
            "body_rev": int,
            "content_hash": str,
            "trigger": str
        }
    """
    # MVP placeholder: 实际实现需要调用 async 处理函数
    return {
        "status": "not_implemented",
        "task_id": self.request.id,
        "payload": payload,
    }


@shared_task(bind=True, name="consistency.extract_claims")
def extract_claims(self, run_id: int):
    """
    从章节正文提取结构化 claims

    MVP placeholder: 实际实现需要：
    1. 读取 ChapterBody
    2. 调用 LLM 提取 claims
    3. 写入 ConsistencyClaim 表
    4. 更新 ConsistencyRun 状态
    """
    return {
        "status": "not_implemented",
        "task_id": self.request.id,
        "run_id": run_id,
    }


@shared_task(bind=True, name="consistency.generate_summary")
def generate_summary(self, run_id: int):
    """
    生成章节摘要

    MVP placeholder: 实际实现需要：
    1. 读取 ChapterBody
    2. 调用 LLM 生成摘要
    3. 写入 DocumentSummary 表
    4. 更新 ConsistencyRun 状态
    """
    return {
        "status": "not_implemented",
        "task_id": self.request.id,
        "run_id": run_id,
    }


@shared_task(bind=True, name="consistency.scan_rules")
def scan_rules(self, run_id: int):
    """
    执行三大确定性规则扫描

    MVP placeholder: 实际实现需要：
    1. 读取当前 chapter 的所有 claims
    2. 应用三大规则（alive_conflict, ownership_conflict, knowledge_boundary）
    3. 创建 GuardIssue 和 GuardIssueEvidence
    4. 更新 ConsistencyRun 状态
    """
    return {
        "status": "not_implemented",
        "task_id": self.request.id,
        "run_id": run_id,
    }


@shared_task(bind=True, name="consistency.dispatch_outbox")
def dispatch_outbox(self, batch_size: int = 10):
    """
    从 outbox 分发事件到对应的 Celery 任务

    MVP placeholder: 实际实现需要：
    1. OutboxService.lease_batch()
    2. 根据 topic 路由到对应任务
    3. 标记为 sent 或 failed
    """
    return {
        "status": "not_implemented",
        "task_id": self.request.id,
        "batch_size": batch_size,
    }
