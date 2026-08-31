"""
Codex embedding 回填任务 - 指数退避 + 有界重试 + 逐批提交。

架构 8：「失败采用指数退避和最大重试，永久失败进入 dead-letter 状态并在项目状态
接口可见。锁只覆盖单章短事务，不在调用模型时持有数据库锁。」对应实现：

* 指数退避 + 有界重试：autoretry_for + retry_backoff + max_retries；
* 逐批提交：commit_each_batch=True。重试时已完成的批次哈希已匹配，下一轮查询
  直接跳过 —— 重试因此是幂等的，不会重复烧网关配额；
* 永久失败可见：耗尽重试后 count_stale_entries 不归零，
  GET/POST /codex/{project_id}/backfill-embeddings 的 remaining_count 就是那个信号。

分工：请求路径（api.codex）不重试，让作者的编辑不被网关抖动拖长；重试与合批
都在这里。回填按项目一次 embed_batch 多条，比逐条 embed_text 便宜得多。
"""
import asyncio

from celery import shared_task
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config import settings
from services.codex import count_stale_entries
from services.codex_embedding import embed_missing_codex_entries
from services.embedding import EmbeddingProviderError, GatewayEmbeddingProvider

engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

#: 视为「网关暂时不可用、值得重试」的异常。httpx.HTTPError 覆盖连接与状态码错误。
RETRYABLE_ERRORS = (EmbeddingProviderError, OSError)


def run_async(coro):
    """与 tasks.consistency 同一套同步包装。"""
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(coro)


async def _backfill_async(project_id: str, batch_size: int) -> dict:
    async with AsyncSessionLocal() as db:
        embedded = await embed_missing_codex_entries(
            db,
            GatewayEmbeddingProvider(),
            project_id=project_id,
            batch_size=batch_size,
            commit_each_batch=True,
        )
        await db.commit()
        return {
            "status": "success",
            "project_id": project_id,
            "embedded_count": embedded,
            "remaining_count": await count_stale_entries(db, project_id),
        }


@shared_task(
    bind=True,
    name="codex.backfill_embeddings",
    autoretry_for=RETRYABLE_ERRORS,
    max_retries=5,
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def backfill_codex_embeddings_task(self, project_id: str, batch_size: int = 32):
    """回填项目内所有待重算条目的向量。

    Args:
        project_id: 项目 ID
        batch_size: 每批条目数（每批一次 embed_batch 调用）

    Returns:
        {"status", "project_id", "embedded_count", "remaining_count"}

    Raises:
        EmbeddingProviderError / OSError: 网关不可用，Celery 按指数退避重试；
            重试耗尽后条目留在待重算状态，remaining_count 会持续暴露欠账。
    """
    return run_async(_backfill_async(project_id, batch_size))


__all__ = ["RETRYABLE_ERRORS", "backfill_codex_embeddings_task"]
