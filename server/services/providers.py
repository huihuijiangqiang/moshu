"""
Provider interfaces for LLM and embedding services
"""
from abc import ABC, abstractmethod
from typing import Optional


class EmbeddingProvider(ABC):
    """Embedding provider interface"""

    @abstractmethod
    async def embed_text(self, text: str, model: Optional[str] = None) -> list[float]:
        """
        Generate embedding for text

        Args:
            text: Input text
            model: Model identifier; None 表示用 settings.embedding_model

        Returns:
            Embedding vector (settings.embedding_dimensions 维)
        """
        pass

    @abstractmethod
    async def embed_batch(
        self, texts: list[str], model: Optional[str] = None
    ) -> list[list[float]]:
        """
        Generate embeddings for batch of texts

        Args:
            texts: List of input texts
            model: Model identifier; None 表示用 settings.embedding_model

        Returns:
            List of embedding vectors, 顺序与输入一致
        """
        pass


class StructuredExtractionProvider(ABC):
    """Structured extraction provider interface"""

    @abstractmethod
    async def extract_claims(
        self,
        content: str,
        project_context: Optional[dict] = None,
        model: str = "gpt-4",
    ) -> list[dict]:
        """
        Extract structured claims from content

        Args:
            content: Chapter content (HTML or plain text)
            project_context: Project-specific context (codex entries, style, etc.)
            model: Model identifier

        Returns:
            List of claims with structure:
            {
                "subject_text": str,
                "predicate": str,
                "object_type": str,
                "object_value": Optional[str],
                "polarity": str,
                "certainty": str,
                "confidence": float,
                "paragraph_id": Optional[str],
            }
        """
        pass

    @abstractmethod
    async def generate_summary(
        self,
        content: str,
        summary_type: str = "chapter",
        max_tokens: int = 500,
        model: str = "gpt-4",
    ) -> str:
        """
        Generate summary for content

        Args:
            content: Chapter or volume content
            summary_type: "chapter" or "volume"
            max_tokens: Maximum summary length
            model: Model identifier

        Returns:
            Summary text
        """
        pass


class MockEmbeddingProvider(EmbeddingProvider):
    """Mock embedding provider for testing

    确定性但方向可分：不同文本的向量夹角不同，这样 cosine_distance 在测试里
    才有区分度（早期实现返回常量向量，任意两条都完全相似）。
    """

    async def embed_text(self, text: str, model: Optional[str] = None) -> list[float]:
        import hashlib
        import math

        from config import settings

        dimensions = settings.embedding_dimensions
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vector = [
            (digest[index % len(digest)] - 127.5) / 127.5 for index in range(dimensions)
        ]
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    async def embed_batch(
        self, texts: list[str], model: Optional[str] = None
    ) -> list[list[float]]:
        return [await self.embed_text(text, model) for text in texts]


class MockStructuredExtractionProvider(StructuredExtractionProvider):
    """Mock structured extraction provider for testing"""

    async def extract_claims(
        self,
        content: str,
        project_context: Optional[dict] = None,
        model: str = "gpt-4",
    ) -> list[dict]:
        # Return empty claims for MVP
        return []

    async def generate_summary(
        self,
        content: str,
        summary_type: str = "chapter",
        max_tokens: int = 500,
        model: str = "gpt-4",
    ) -> str:
        # Return truncated content as summary
        words = content.split()[:100]
        return " ".join(words) + "..."
