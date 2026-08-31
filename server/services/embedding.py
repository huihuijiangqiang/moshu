"""
真实 embedding provider - 走 model gateway 的 /embeddings 接口。

之前只有 MockEmbeddingProvider，检索层永远拿不到真实向量，RAG 写入路径
（codex 条目落库时生成 embedding）也完全缺失。这里补上真实实现：

* embedding 可使用独立网关；未配置时兼容旧部署，从模型网关推导；
* 网关 URL/key/model/维度全部来自 config.settings，不硬编码；
* 响应结构异常一律抛错，绝不返回零向量 —— 零向量会让 cosine_distance 变成
  常量，把任意条目都判成「相似」；
* 批量接口保证返回顺序与输入顺序一致（按响应里的 index 重排）。
"""
from typing import Optional

import httpx

from config import settings
from services.providers import EmbeddingProvider


class EmbeddingProviderError(RuntimeError):
    """embedding 网关返回了无法使用的响应。"""


class GatewayEmbeddingProvider(EmbeddingProvider):
    """OpenAI 兼容的 /embeddings 客户端。"""

    def __init__(
        self,
        client: Optional[httpx.AsyncClient] = None,
        *,
        gateway_tier: Optional[str] = None,
        endpoint: Optional[str] = None,
    ):
        """
        Args:
            client: 可注入的 httpx 客户端（测试用）
            gateway_tier: 覆盖默认网关档位
            endpoint: 覆盖 embeddings 端点；默认由 chat 端点推导
        """
        self._client = client
        self._gateway_tier = gateway_tier
        self._endpoint = endpoint

    @property
    def endpoint(self) -> str:
        """embeddings 端点。

        独立配置既可给 base URL，也可给完整 /embeddings 端点。未配置时从旧的
        chat/completions 地址推导，确保现有部署升级后仍能启动。
        """
        if self._endpoint:
            return self._endpoint
        dedicated = self._dedicated_credentials()
        base = dedicated[0] if dedicated else settings.gateway_url(self._gateway_tier)
        base = base.rstrip("/")
        if base.endswith("/embeddings"):
            return base
        for suffix in ("/chat/completions", "/completions"):
            if base.endswith(suffix):
                return base[: -len(suffix)] + "/embeddings"
        return base + "/embeddings"

    @property
    def headers(self) -> dict[str, str]:
        dedicated = self._dedicated_credentials()
        api_key = dedicated[1] if dedicated else settings.gateway_key(self._gateway_tier)
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _dedicated_credentials() -> Optional[tuple[str, str]]:
        url = settings.embedding_gateway_url
        key = settings.embedding_gateway_key
        if bool(url) != bool(key):
            raise EmbeddingProviderError(
                "EMBEDDING_GATEWAY_URL and EMBEDDING_GATEWAY_KEY must be configured together"
            )
        if url and key:
            return url, key
        return None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client:
            return self._client
        return httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))

    async def embed_text(self, text: str, model: Optional[str] = None) -> list[float]:
        """单条文本向量化。"""
        vectors = await self.embed_batch([text], model)
        return vectors[0]

    async def embed_batch(
        self, texts: list[str], model: Optional[str] = None
    ) -> list[list[float]]:
        """批量向量化；返回顺序与输入一致。

        Raises:
            ValueError: 输入含空文本
            httpx.HTTPError: 网关请求失败
            EmbeddingProviderError: 响应结构或向量维度不对
        """
        if not texts:
            return []
        if any(not text or not text.strip() for text in texts):
            raise ValueError("embedding input must not contain empty text")

        resolved_model = model or settings.embedding_model
        client = await self._get_client()
        try:
            response = await client.post(
                self.endpoint,
                headers=self.headers,
                json={"model": resolved_model, "input": texts},
            )
            response.raise_for_status()
            return self._parse_vectors(response, expected_count=len(texts))
        finally:
            if not self._client:
                await client.aclose()

    @staticmethod
    def _parse_vectors(response: httpx.Response, *, expected_count: int) -> list[list[float]]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise EmbeddingProviderError("embedding response body is not JSON") from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise EmbeddingProviderError("embedding response missing 'data' list")
        if len(data) != expected_count:
            raise EmbeddingProviderError(
                f"embedding response returned {len(data)} vectors for {expected_count} inputs"
            )

        # 按 index 重排，不依赖网关的返回顺序
        ordered: list[Optional[list[float]]] = [None] * expected_count
        for position, item in enumerate(data):
            if not isinstance(item, dict):
                raise EmbeddingProviderError("embedding response item is not an object")
            index = item.get("index", position)
            if not isinstance(index, int) or not 0 <= index < expected_count:
                raise EmbeddingProviderError(f"embedding response has invalid index: {index!r}")
            vector = item.get("embedding")
            if not isinstance(vector, list) or not vector:
                raise EmbeddingProviderError("embedding response item missing 'embedding' vector")
            if len(vector) != settings.embedding_dimensions:
                raise EmbeddingProviderError(
                    f"embedding dimension mismatch: got {len(vector)}, "
                    f"expected {settings.embedding_dimensions}"
                )
            if not all(isinstance(value, (int, float)) for value in vector):
                raise EmbeddingProviderError("embedding vector contains non-numeric values")
            if ordered[index] is not None:
                raise EmbeddingProviderError(f"embedding response has duplicate index {index}")
            ordered[index] = [float(value) for value in vector]

        if any(vector is None for vector in ordered):
            raise EmbeddingProviderError("embedding response is missing some indexes")
        return ordered  # type: ignore[return-value]
