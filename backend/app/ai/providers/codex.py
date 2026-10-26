import json
import subprocess
import tempfile
from pathlib import Path

from app.ai.providers.base import LLMError, LLMProvider


def strict_output_schema(schema: dict) -> dict:
    """Adapt Pydantic's schema to Codex's strict structured output format."""
    def convert(value):
        if isinstance(value, list):
            return [convert(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: convert(item) for key, item in value.items() if key != "default"}
        if "properties" in result:
            result["required"] = list(result["properties"])
            result["additionalProperties"] = False
        return result

    return convert(schema)


class CodexProvider(LLMProvider):
    """Run a bounded, schema-constrained Codex analysis."""

    supports_quarterly_review = True
    name = "codex"

    def __init__(
        self,
        executable: str,
        model: str,
        timeout: float,
        reasoning_effort: str = "medium",
    ):
        self.executable = executable
        self.model = model
        self.timeout = timeout
        self.reasoning_effort = reasoning_effort

    def generate(self, system: str, user: str, schema: dict) -> str:
        prompt = (
            f"{system}\n\n{user}\n\n"
            "Return only the requested JSON object. Do not inspect files, run commands, "
            "or use external information."
        )
        try:
            with tempfile.TemporaryDirectory(prefix="almonds-codex-") as directory:
                workdir = Path(directory)
                schema_path = workdir / "schema.json"
                output_path = workdir / "response.json"
                schema_path.write_text(
                    json.dumps(strict_output_schema(schema)), encoding="utf-8"
                )
                result = subprocess.run(
                    [
                        self.executable,
                        "exec",
                        "--model",
                        self.model,
                        "--sandbox",
                        "read-only",
                        "--ephemeral",
                        "--ignore-user-config",
                        "--ignore-rules",
                        "--skip-git-repo-check",
                        "--output-schema",
                        str(schema_path),
                        "--output-last-message",
                        str(output_path),
                        "-c",
                        f'model_reasoning_effort="{self.reasoning_effort}"',
                        "-",
                    ],
                    input=prompt,
                    text=True,
                    capture_output=True,
                    timeout=self.timeout,
                    cwd=workdir,
                    check=False,
                )
                if result.returncode != 0:
                    error = result.stderr.lower()
                    if "login" in error or "auth" in error or "sign in" in error:
                        raise LLMError("CODEX_AUTH_REQUIRED")
                    if "model" in error and (
                        "not found" in error or "not available" in error
                    ):
                        raise LLMError("MODEL_UNAVAILABLE")
                    raise LLMError("CODEX_ERROR")
                if not output_path.is_file():
                    raise LLMError("CODEX_BAD_RESPONSE")
                content = output_path.read_text(encoding="utf-8").strip()
                if not content:
                    raise LLMError("EMPTY_RESPONSE")
                return content
        except subprocess.TimeoutExpired as exc:
            raise LLMError("CODEX_TIMEOUT") from exc
        except FileNotFoundError as exc:
            raise LLMError("CODEX_UNAVAILABLE") from exc
        except OSError as exc:
            raise LLMError("CODEX_UNAVAILABLE") from exc
