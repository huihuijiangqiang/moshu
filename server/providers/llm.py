"""
OpenAI-compatible LLM providers for extraction, embedding, and summarization
"""
import hashlib
import json
from typing import Any

import httpx

from config import settings
from services.prompt_security import security_policy, untrusted_text_block


class StructuredExtractionProvider:
    """
    Structured extraction using OpenAI-compatible API
    Extracts claims from chapter body HTML
    """

    def __init__(self):
        self.base_url = settings.openai_api_base
        self.api_key = settings.openai_api_key
        self.model_name = settings.openai_model_name
        self.version = "1.0.0"
        self.timeout = httpx.Timeout(60.0, connect=10.0)

    async def extract_claims(self, content_html: str) -> list[dict[str, Any]]:
        """
        Extract structured claims from chapter body

        Returns:
            List of claims with structure:
            {
                "type": str,  # "character_state", "event", "knowledge", etc.
                "text": str,
                "structured": dict,
                "confidence": float,
                "fingerprint": str
            }
        """
        prompt = self._build_extraction_prompt(content_html)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model_name,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                security_policy("en")
                                + "\n\nYou are a precise literary analysis assistant. "
                                "Extract factual claims from narrative text and return JSON only."
                            ),
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

            # Process claims and add fingerprints
            claims = data.get("claims", [])
            for claim in claims:
                # Generate fingerprint
                fingerprint_data = {
                    "type": claim["type"],
                    "structured": claim.get("structured", {}),
                }
                claim["fingerprint"] = hashlib.sha256(
                    json.dumps(fingerprint_data, sort_keys=True).encode()
                ).hexdigest()

            return claims

    async def generate_summary(self, content_html: str) -> str:
        """
        Generate a concise summary of chapter content
        """
        prompt = (
            "Summarize the following chapter content in 2-3 sentences, focusing on key events, "
            "character actions, and plot progression:\n\n"
            + untrusted_text_block("chapter_content", content_html[:4000])
        )

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model_name,
                    "messages": [
                        {
                            "role": "system",
                            "content": security_policy("en") + "\n\nYou are a concise summarization assistant.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 200,
                },
            )
            response.raise_for_status()

            result = response.json()
            return result["choices"][0]["message"]["content"].strip()

    def _build_extraction_prompt(self, content_html: str) -> str:
        """Build extraction prompt with schema"""
        return f"""Extract factual claims from this chapter. Return JSON with this structure:

{{
  "claims": [
    {{
      "type": "character_state",
      "text": "descriptive text of the claim",
      "structured": {{
        "character": "character name",
        "attribute": "attribute name",
        "value": "value",
        "timeline_ref": "optional reference"
      }},
      "confidence": 0.95
    }},
    {{
      "type": "event",
      "text": "event description",
      "structured": {{
        "action": "action description",
        "participants": ["character1", "character2"],
        "timeline_ref": "optional reference"
      }},
      "confidence": 0.90
    }}
  ]
}}

Valid claim types: character_state, event, knowledge, relationship, location, item_state

Chapter content:
{untrusted_text_block("chapter_content", content_html[:8000])}"""


class EmbeddingProvider:
    """
    Embedding generation using OpenAI-compatible API
    """

    def __init__(self):
        self.base_url = settings.openai_api_base
        self.api_key = settings.openai_api_key
        self.embedding_model = settings.openai_embedding_model
        self.timeout = httpx.Timeout(30.0, connect=10.0)

    async def embed(self, text: str) -> list[float]:
        """
        Generate embedding vector for text

        Returns:
            List of floats (1536 dimensions for text-embedding-3-small)
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.embedding_model,
                    "input": text[:8000],  # Truncate long text
                },
            )
            response.raise_for_status()

            result = response.json()
            return result["data"][0]["embedding"]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embeddings for multiple texts in a single request

        Returns:
            List of embedding vectors
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.embedding_model,
                    "input": [text[:8000] for text in texts],
                },
            )
            response.raise_for_status()

            result = response.json()
            return [item["embedding"] for item in result["data"]]
