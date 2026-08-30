"""
Provider interfaces for LLM and embedding services
"""
from abc import ABC, abstractmethod
from typing import Any, Optional


class EmbeddingProvider(ABC):
    """Embedding provider interface"""

    @abstractmethod
    async def embed_text(self, text: str, model: str = "text-embedding-3-small") -> list[float]:
        """
        Generate embedding for text

        Args:
            text: Input text
            model: Model identifier

        Returns:
            Embedding vector (1536-dim for OpenAI)
        """
        pass

    @abstractmethod
    async def embed_batch(self, texts: list[str], model: str = "text-embedding-3-small") -> list[list[float]]:
        """
        Generate embeddings for batch of texts

        Args:
            texts: List of input texts
            model: Model identifier

        Returns:
            List of embedding vectors
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
    """Mock embedding provider for testing"""

    async def embed_text(self, text: str, model: str = "text-embedding-3-small") -> list[float]:
        # Return deterministic mock embedding based on text hash
        import hashlib

        hash_val = int(hashlib.md5(text.encode()).hexdigest(), 16)
        return [(hash_val % 1000) / 1000.0] * 1536

    async def embed_batch(self, texts: list[str], model: str = "text-embedding-3-small") -> list[list[float]]:
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
