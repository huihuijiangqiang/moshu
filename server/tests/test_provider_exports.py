"""The public providers package must expose the live provider interfaces."""

from providers import (
    EmbeddingProvider,
    GatewayEmbeddingProvider,
    MockEmbeddingProvider,
    MockStructuredExtractionProvider,
    StructuredExtractionProvider,
)
from services.providers import (
    EmbeddingProvider as ServiceEmbeddingProvider,
)
from services.providers import (
    MockEmbeddingProvider as ServiceMockEmbeddingProvider,
)
from services.providers import (
    MockStructuredExtractionProvider as ServiceMockStructuredExtractionProvider,
)
from services.providers import (
    StructuredExtractionProvider as ServiceStructuredExtractionProvider,
)


def test_public_provider_exports_use_current_implementations():
    assert EmbeddingProvider is ServiceEmbeddingProvider
    assert StructuredExtractionProvider is ServiceStructuredExtractionProvider
    assert MockEmbeddingProvider is ServiceMockEmbeddingProvider
    assert MockStructuredExtractionProvider is ServiceMockStructuredExtractionProvider
    assert issubclass(GatewayEmbeddingProvider, EmbeddingProvider)
