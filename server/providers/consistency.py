"""
LLM providers for consistency extraction and summarization
Uses model_gateway configuration with injectable httpx client
"""
import hashlib
import json
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field

from config import settings


# Pydantic models for structured extraction
class ClaimOutput(BaseModel):
    """Structured claim from LLM extraction"""

    subject_text: str = Field(..., min_length=1, max_length=200)
    predicate: str = Field(..., min_length=1, max_length=100)
    object_type: str = Field(..., pattern="^(scalar|entity|location|ability|timestamp)$")
    object_value: Optional[str] = Field(None, max_length=1000)
    polarity: str = Field(default="positive", pattern="^(positive|negative)$")
    certainty: str = Field(default="explicit", pattern="^(explicit|inferred|uncertain)$")
    paragraph_id: Optional[str] = Field(None, max_length=100)
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)


class ExtractionResponse(BaseModel):
    """Response from extraction endpoint"""

    claims: list[ClaimOutput] = Field(default_factory=list)


class ConsistencyProvider:
    """Provider for consistency extraction and summarization using model_gateway"""

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        """
        Args:
            client: Injectable httpx client for testing
        """
        self._client = client
        self.extractor_version = "1.0.0"

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create httpx client"""
        if self._client:
            return self._client
        return httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))

    async def extract_claims(
        self,
        content_html: str,
        project_id: str,
        chapter_id: str,
    ) -> list[dict[str, Any]]:
        """
        Extract structured claims from chapter body

        Args:
            content_html: Chapter HTML content
            project_id: Project ID for context
            chapter_id: Chapter ID

        Returns:
            List of claims with fingerprints
        """
        # Build extraction prompt
        prompt = self._build_extraction_prompt(content_html[:8000])

        client = await self._get_client()
        try:
            response = await client.post(
                settings.model_gateway_main_url,
                headers={
                    "Authorization": f"Bearer {settings.model_gateway_main_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {
                            "role": "system",
                            "content": "Extract factual claims from narrative text. Return valid JSON only.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.0,
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()

            result = response.json()
            content = result["choices"][0]["message"]["content"]
            data = json.loads(content)

            # Validate with Pydantic
            extraction = ExtractionResponse.model_validate(data)

            # Convert to claim dicts with fingerprints
            claims = []
            for claim in extraction.claims:
                claim_dict = claim.model_dump()
                # Generate fingerprint
                fingerprint_data = {
                    "subject_text": claim.subject_text.lower().strip(),
                    "predicate": claim.predicate.lower().strip(),
                    "object_type": claim.object_type,
                    "object_value": (claim.object_value or "").lower().strip(),
                    "polarity": claim.polarity,
                }
                claim_dict["fingerprint"] = hashlib.sha256(
                    json.dumps(fingerprint_data, sort_keys=True).encode()
                ).hexdigest()
                claims.append(claim_dict)

            return claims
        finally:
            if not self._client:
                await client.aclose()

    async def generate_summary(
        self,
        content_html: str,
        summary_type: str = "chapter",
    ) -> tuple[str, int]:
        """
        Generate chapter or volume summary

        Args:
            content_html: Content to summarize
            summary_type: "chapter" or "volume"

        Returns:
            Tuple of (summary_text, estimated_token_count)
        """
        max_words = 150 if summary_type == "chapter" else 500
        prompt = f"Summarize the following {summary_type} in {max_words} words or less:\n\n{content_html[:4000]}"

        client = await self._get_client()
        try:
            response = await client.post(
                settings.model_gateway_main_url,
                headers={
                    "Authorization": f"Bearer {settings.model_gateway_main_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": "You are a concise summarization assistant."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 300,
                },
            )
            response.raise_for_status()

            result = response.json()
            summary_text = result["choices"][0]["message"]["content"].strip()
            # Rough token count estimate
            token_count = result.get("usage", {}).get("total_tokens", len(summary_text) // 4)

            return summary_text, token_count
        finally:
            if not self._client:
                await client.aclose()

    def _build_extraction_prompt(self, content: str) -> str:
        """Build extraction prompt with schema"""
        return f"""Extract factual claims from this narrative text. Return JSON with this exact structure:

{{
  "claims": [
    {{
      "subject_text": "character or entity name",
      "predicate": "alive|dead|owns|knows|located_at|has_ability|etc",
      "object_type": "scalar|entity|location|ability|timestamp",
      "object_value": "the value or null",
      "polarity": "positive|negative",
      "certainty": "explicit|inferred|uncertain",
      "paragraph_id": "optional paragraph identifier",
      "confidence": 0.9
    }}
  ]
}}

Valid object_type values: scalar, entity, location, ability, timestamp
Valid polarity values: positive, negative
Valid certainty values: explicit, inferred, uncertain

Content:
{content}"""
