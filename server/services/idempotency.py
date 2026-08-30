"""
Idempotency Service - 保证 API 请求幂等性（两阶段：reserve/complete）
"""

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_consistency import IdempotencyRecord


class IdempotencyConflictError(Exception):
    """幂等键冲突 - 同 key 不同 payload"""

    def __init__(self, scope: str, key: str, expected_hash: str, actual_hash: str):
        self.scope = scope
        self.key = key
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash
        super().__init__(
            f"Idempotency conflict: scope={scope}, key={key}, expected_hash={expected_hash}, actual_hash={actual_hash}"
        )


class IdempotencyInProgressError(Exception):
    """请求正在处理中"""

    def __init__(self, scope: str, key: str):
        self.scope = scope
        self.key = key
        super().__init__(f"Idempotency request in progress: scope={scope}, key={key}")


class IdempotencyService:
    """幂等性服务"""

    @staticmethod
    def compute_canonical_hash(payload: dict) -> str:
        """
        计算请求 payload 的标准化哈希

        规则：
        1. 递归排序所有字典的 key
        2. 使用 separators=(',', ':') 移除空白
        3. ensure_ascii=False 保持 Unicode
        4. SHA-256 哈希

        Args:
            payload: 请求 payload（必须是可 JSON 序列化的）

        Returns:
            十六进制哈希字符串
        """
        canonical_json = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    @staticmethod
    async def reserve(
        db: AsyncSession,
        scope: str,
        key: str,
        request_payload: dict,
        ttl_hours: int = 24,
        lease_seconds: int = 300,
    ) -> dict:
        """
        预留幂等槽位 - 必须在执行副作用前调用

        Args:
            db: 数据库会话
            scope: 幂等范围（通常是 user_id + route）
            key: 幂等键（通常是 Idempotency-Key header）
            request_payload: 请求 payload
            ttl_hours: 过期时长（小时）
            lease_seconds: 租约时长（秒）

        Returns:
            {"action": "execute", "owner_token": "..."} - 应执行请求
            {"action": "replay", "status": 200, "body": {...}} - 返回缓存响应
            {"action": "wait"} - 请求正在处理中

        Raises:
            IdempotencyConflict: 同 key 不同 payload
        """
        request_hash = IdempotencyService.compute_canonical_hash(request_payload)
        now = datetime.now(timezone.utc)
        owner_token = secrets.token_urlsafe(32)
        expires_at = now + timedelta(hours=ttl_hours)
        lease_until = now + timedelta(seconds=lease_seconds)

        # 使用 PostgreSQL INSERT ... ON CONFLICT
        stmt = (
            insert(IdempotencyRecord)
            .values(
                scope=scope,
                key=key,
                request_hash=request_hash,
                status="pending",
                owner_token=owner_token,
                lease_until=lease_until,
                expires_at=expires_at,
            )
            .on_conflict_do_nothing(index_elements=["scope", "key"])
            .returning(IdempotencyRecord)
        )

        result = await db.execute(stmt)
        record = result.scalar_one_or_none()

        if record:
            # 成功插入，返回执行指令
            return {"action": "execute", "owner_token": owner_token}

        # 冲突，查询已存在的记录
        select_stmt = select(IdempotencyRecord).where(
            IdempotencyRecord.scope == scope,
            IdempotencyRecord.key == key,
        )
        result = await db.execute(select_stmt)
        existing = result.scalar_one()

        # 验证 hash 一致性
        if existing.request_hash != request_hash:
            raise IdempotencyConflictError(
                scope=scope,
                key=key,
                expected_hash=existing.request_hash,
                actual_hash=request_hash,
            )

        # 检查是否已完成
        if existing.status == "completed":
            if existing.expires_at < now:
                # 过期记录：删除并重试
                await db.delete(existing)
                await db.flush()
                # 递归重新 reserve
                return await IdempotencyService.reserve(db, scope, key, request_payload, ttl_hours, lease_seconds)

            # 返回已缓存的响应
            return {
                "action": "replay",
                "status": existing.response_status,
                "body": existing.response_body,
            }

        # status == "pending"
        if existing.lease_until and existing.lease_until > now:
            # 租约有效，请求正在处理中
            return {"action": "wait"}

        # 租约过期，尝试抢占
        update_stmt = (
            update(IdempotencyRecord)
            .where(
                IdempotencyRecord.scope == scope,
                IdempotencyRecord.key == key,
                IdempotencyRecord.status == "pending",
                or_(IdempotencyRecord.lease_until.is_(None), IdempotencyRecord.lease_until <= now),
            )
            .values(
                owner_token=owner_token,
                lease_until=lease_until,
            )
        )
        result = await db.execute(update_stmt)

        if result.rowcount > 0:
            return {"action": "execute", "owner_token": owner_token}

        # 抢占失败（其他进程先抢到）
        return {"action": "wait"}

    @staticmethod
    async def complete(
        db: AsyncSession,
        scope: str,
        key: str,
        owner_token: str,
        response_status: int,
        response_body: dict,
    ) -> bool:
        """
        完成幂等请求 - 写入响应并转 completed 状态

        Args:
            db: 数据库会话
            scope: 幂等范围
            key: 幂等键
            owner_token: 预留时返回的 token
            response_status: 响应状态码
            response_body: 响应体

        Returns:
            是否成功完成（token/status 不匹配返回 False）
        """
        stmt = (
            update(IdempotencyRecord)
            .where(
                IdempotencyRecord.scope == scope,
                IdempotencyRecord.key == key,
                IdempotencyRecord.owner_token == owner_token,
                IdempotencyRecord.status == "pending",
            )
            .values(
                status="completed",
                response_status=response_status,
                response_body=response_body,
                owner_token=None,
                lease_until=None,
            )
        )
        result = await db.execute(stmt)
        return result.rowcount > 0

    @staticmethod
    async def cleanup_expired(db: AsyncSession) -> int:
        """
        清理过期的幂等记录 - 调用方控制事务

        Args:
            db: 数据库会话

        Returns:
            删除的记录数
        """
        stmt = delete(IdempotencyRecord).where(
            IdempotencyRecord.status == "completed",
            IdempotencyRecord.expires_at < datetime.now(timezone.utc),
        )
        result = await db.execute(stmt)
        return result.rowcount
