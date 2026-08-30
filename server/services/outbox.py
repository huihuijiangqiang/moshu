"""
Transactional Outbox Service - 保证异步任务至少投递一次
"""
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_consistency import OutboxEvent


class OutboxService:
    """事务 outbox 服务"""

    @staticmethod
    async def enqueue(
        db: AsyncSession,
        topic: str,
        aggregate_id: str,
        aggregate_rev: int,
        payload: dict,
    ) -> OutboxEvent:
        """
        在事务内入队事件 - 冲突时幂等返回已有记录

        Args:
            db: 数据库会话（调用方负责事务管理）
            topic: 事件主题（如 "chapter.body_saved"）
            aggregate_id: 聚合根 ID（如 chapter_id）
            aggregate_rev: 聚合根版本（如 body_rev）
            payload: 任务 payload（必须包含足够信息重建任务）

        Returns:
            OutboxEvent 实例（新建或已存在）
        """
        # 尝试查找已存在的事件（幂等）
        stmt = select(OutboxEvent).where(
            OutboxEvent.topic == topic,
            OutboxEvent.aggregate_id == aggregate_id,
            OutboxEvent.aggregate_rev == aggregate_rev,
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            return existing

        # 创建新事件
        event = OutboxEvent(
            topic=topic,
            aggregate_id=aggregate_id,
            aggregate_rev=aggregate_rev,
            payload=payload,
            status="pending",
            attempts=0,
            available_at=datetime.now(timezone.utc),
        )
        db.add(event)
        await db.flush()
        return event

    @staticmethod
    async def lease_batch(
        db: AsyncSession,
        owner_id: str,
        batch_size: int = 100,
        lease_duration_seconds: int = 60,
    ) -> list[OutboxEvent]:
        """
        领取一批待投递事件 - 使用 SELECT FOR UPDATE SKIP LOCKED 避免冲突

        Args:
            db: 数据库会话
            owner_id: 领取者标识（通常是 worker 进程 ID）
            batch_size: 批量大小
            lease_duration_seconds: 租约时长（秒）

        Returns:
            领取到的事件列表
        """
        now = datetime.now(timezone.utc)
        lease_token = secrets.token_urlsafe(32)
        lease_until = now + timedelta(seconds=lease_duration_seconds)

        # 查找可用事件：pending 或租约已过期的 dispatching
        stmt = (
            select(OutboxEvent)
            .where(
                (OutboxEvent.status == "pending")
                | (
                    (OutboxEvent.status == "dispatching")
                    & (OutboxEvent.lease_until < now)
                )
            )
            .where(OutboxEvent.available_at <= now)
            .order_by(OutboxEvent.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )

        result = await db.execute(stmt)
        events = list(result.scalars().all())

        if not events:
            return []

        # 更新为 dispatching 状态
        event_ids = [e.id for e in events]
        update_stmt = (
            update(OutboxEvent)
            .where(OutboxEvent.id.in_(event_ids))
            .values(
                status="dispatching",
                lease_owner=owner_id,
                lease_token=lease_token,
                lease_until=lease_until,
                attempts=OutboxEvent.attempts + 1,
            )
        )
        await db.execute(update_stmt)
        await db.commit()

        # 重新加载以获取更新后的数据
        result = await db.execute(select(OutboxEvent).where(OutboxEvent.id.in_(event_ids)))
        return list(result.scalars().all())

    @staticmethod
    async def mark_sent(
        db: AsyncSession,
        event_id: int,
        lease_token: str,
    ) -> bool:
        """
        标记事件已发送 - 必须匹配 lease_token

        Args:
            db: 数据库会话
            event_id: 事件 ID
            lease_token: 租约令牌（防止过期租约覆盖）

        Returns:
            是否成功标记（token 不匹配返回 False）
        """
        now = datetime.now(timezone.utc)
        stmt = (
            update(OutboxEvent)
            .where(
                OutboxEvent.id == event_id,
                OutboxEvent.lease_token == lease_token,
            )
            .values(
                status="sent",
                sent_at=now,
                last_error=None,
            )
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount > 0

    @staticmethod
    async def mark_failed(
        db: AsyncSession,
        event_id: int,
        lease_token: str,
        error_message: str,
        retry_after_seconds: Optional[int] = None,
        max_attempts: int = 5,
    ) -> bool:
        """
        标记事件失败 - 根据重试次数决定是否重试

        Args:
            db: 数据库会话
            event_id: 事件 ID
            lease_token: 租约令牌
            error_message: 错误信息
            retry_after_seconds: 多久后重试（None 表示立即）
            max_attempts: 最大尝试次数

        Returns:
            是否成功标记
        """
        # 先查询当前状态
        stmt = select(OutboxEvent).where(
            OutboxEvent.id == event_id,
            OutboxEvent.lease_token == lease_token,
        )
        result = await db.execute(stmt)
        event = result.scalar_one_or_none()

        if not event:
            return False

        # 判断是否超过最大重试次数
        if event.attempts >= max_attempts:
            new_status = "dead_letter"
            available_at = None
        else:
            new_status = "pending"
            if retry_after_seconds:
                available_at = datetime.now(timezone.utc) + timedelta(seconds=retry_after_seconds)
            else:
                # 指数退避：2^attempts 秒
                backoff = min(2 ** event.attempts, 3600)
                available_at = datetime.now(timezone.utc) + timedelta(seconds=backoff)

        update_stmt = (
            update(OutboxEvent)
            .where(
                OutboxEvent.id == event_id,
                OutboxEvent.lease_token == lease_token,
            )
            .values(
                status=new_status,
                available_at=available_at,
                last_error=error_message[:1000],  # 限制错误信息长度
                lease_owner=None,
                lease_token=None,
                lease_until=None,
            )
        )
        await db.execute(update_stmt)
        await db.commit()
        return True
