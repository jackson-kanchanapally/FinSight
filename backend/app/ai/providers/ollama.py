import httpx

from app.ai.providers.base import LLMError, LLMProvider


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float,
        temperature: float,
        num_ctx: int = 8192,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self.num_ctx = num_ctx

    def generate(self, system: str, user: str, schema: dict) -> str:
        try:
            response = httpx.post(
                f"{self.base_url}/api/chat",
                timeout=self.timeout,
                json={
                    "model": self.model,
                    "stream": False,
                    "think": False,
                    "format": schema,
                    "options": {
                        "temperature": self.temperature,
                        "num_ctx": self.num_ctx,
                    },
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            if response.status_code == 404:
                raise LLMError("MODEL_UNAVAILABLE")
            response.raise_for_status()
            body = response.json()
            content = body.get("message", {}).get("content")
            if not isinstance(content, str) or not content.strip():
                raise LLMError("EMPTY_RESPONSE")
            return content
        except httpx.TimeoutException as exc:
            raise LLMError("OLLAMA_TIMEOUT") from exc
        except httpx.ConnectError as exc:
            raise LLMError("OLLAMA_UNAVAILABLE") from exc
        except httpx.HTTPStatusError as exc:
            raise LLMError("OLLAMA_HTTP_ERROR") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMError("OLLAMA_BAD_RESPONSE") from exc
