"""Public provider interfaces used by the application.

The project originally exposed a second, pre-configuration provider module from
``providers.llm``.  That module still expected the removed ``openai_*`` settings
and made ``from providers import EmbeddingProvider`` return an incompatible
implementation.  Keep one source of truth: the interfaces and gateway adapter
under ``services`` are the implementations used by the API and Celery tasks.
"""

from services.embedding import EmbeddingProviderError, GatewayEmbeddingProvider
from services.providers import (
    EmbeddingProvider,
    MockEmbeddingProvider,
    MockStructuredExtractionProvider,
    StructuredExtractionProvider,
)

__all__ = [
    "EmbeddingProvider",
    "EmbeddingProviderError",
    "GatewayEmbeddingProvider",
    "MockEmbeddingProvider",
    "MockStructuredExtractionProvider",
    "StructuredExtractionProvider",
]
