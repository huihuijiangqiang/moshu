"""
Idempotency Service - 保证 API 请求幂等性
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_consistency import IdempotencyRecord


class IdempotencyConflict(Exception):
    """幂等键冲突 - 同 key 不同 payload"""

    def __init__(self, scope: str, key: str, expected_hash: str, actual_hash: str):
        self.scope = scope
        self.key = key
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash
        super().__init__(
            f"Idempotency conflict: scope={scope}, key={key}, "
            f"expected_hash={expected_hash}, actual_hash={actual_hash}"
        )


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
    async def check_and_record(
        db: AsyncSession,
        scope: str,
        key: str,
        request_payload: dict,
        response_status: int,
        response_body: dict,
        ttl_hours: int = 24,
    ) -> Optional[dict]:
        """
        检查并记录幂等请求

        Args:
            db: 数据库会话
            scope: 幂等范围（通常是 user_id + route）
            key: 幂等键（通常是 Idempotency-Key header）
            request_payload: 请求 payload
            response_status: 响应状态码
            response_body: 响应体
            ttl_hours: 过期时长（小时）

        Returns:
            如果是重复请求，返回已存在的响应；否则返回 None

        Raises:
            IdempotencyConflict: 同 key 不同 payload
        """
        request_hash = IdempotencyService.compute_canonical_hash(request_payload)

        # 查找已存在的记录
        stmt = select(IdempotencyRecord).where(
            IdempotencyRecord.scope == scope,
            IdempotencyRecord.key == key,
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            # 检查是否过期
            if existing.expires_at < datetime.now(timezone.utc):
                # 过期记录：删除并允许重新执行
                await db.delete(existing)
                await db.commit()
                return None

            # 验证 hash
            if existing.request_hash != request_hash:
                raise IdempotencyConflict(
                    scope=scope,
                    key=key,
                    expected_hash=existing.request_hash,
                    actual_hash=request_hash,
                )

            # 返回已缓存的响应
            return {
                "status": existing.response_status,
                "body": existing.response_body,
            }

        # 新请求：记录结果
        record = IdempotencyRecord(
            scope=scope,
            key=key,
            request_hash=request_hash,
            response_status=response_status,
            response_body=response_body,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=ttl_hours),
        )
        db.add(record)
        await db.flush()
        return None

    @staticmethod
    async def cleanup_expired(db: AsyncSession) -> int:
        """
        清理过期的幂等记录

        Args:
            db: 数据库会话

        Returns:
            删除的记录数
        """
        stmt = select(IdempotencyRecord).where(
            IdempotencyRecord.expires_at < datetime.now(timezone.utc)
        )
        result = await db.execute(stmt)
        expired = result.scalars().all()

        count = len(expired)
        for record in expired:
            await db.delete(record)

        await db.commit()
        return count
