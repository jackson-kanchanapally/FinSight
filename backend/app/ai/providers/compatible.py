"""Explicit zero-cost OpenAI-compatible route, including local OmniRoute gateways."""
from urllib.parse import urlsplit

import httpx

from app.ai.providers.base import LLMError, LLMProvider
from app.config import settings


class FreeCompatibleProvider(LLMProvider):
    name = "openai_compatible"
    supports_quarterly_review = True

    def __init__(self, timeout):
        self.model = settings.ai_model.strip()
        self.base_url = settings.ai_api_base_url.rstrip("/")
        self.timeout = timeout
        self.key = settings.ai_api_key.get_secret_value()
        parsed = urlsplit(self.base_url)
        if (not self.model or self.model not in settings.ai_free_models
                or self.model.lower() in {"auto", "default", "router", "openrouter/auto"}):
            raise LLMError("FREE_MODEL_NOT_ALLOWED")
        if (parsed.username or parsed.password or parsed.query or parsed.fragment
                or not parsed.hostname or (parsed.scheme != "https" and not (
                    parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}))):
            raise LLMError("INVALID_FREE_PROVIDER_URL")

    def generate(self, system, user, schema):
        # Never iterate models, follow redirects, or select a paid fallback.
        try:
            response = httpx.post(self.base_url + "/chat/completions", timeout=self.timeout,
                follow_redirects=False,
                headers={"Authorization": f"Bearer {self.key}"} if self.key else {},
                json={"model": self.model, "temperature": settings.ai_temperature,
                      "max_tokens": 1600, "stream": False,
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                      "response_format": {"type": "json_schema", "json_schema": {
                          "name": "almonds_analysis", "strict": True, "schema": schema}},
                      "provider": {"allow_fallbacks": False, "max_price": {
                          "prompt": 0, "completion": 0}}})
            if response.status_code == 429:
                raise LLMError("FREE_PROVIDER_RATE_LIMITED")
            if response.status_code != 200:
                raise LLMError("FREE_PROVIDER_UNAVAILABLE")
            body = response.json()
            # A gateway that reports a different route must not be trusted.
            if body.get("model") not in {None, self.model}:
                raise LLMError("FREE_PROVIDER_ROUTE_MISMATCH")
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise LLMError("EMPTY_RESPONSE")
            return content
        except httpx.TimeoutException:
            raise LLMError("FREE_PROVIDER_TIMEOUT") from None
        except httpx.HTTPError:
            raise LLMError("FREE_PROVIDER_UNAVAILABLE") from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise LLMError("FREE_PROVIDER_BAD_RESPONSE") from None
