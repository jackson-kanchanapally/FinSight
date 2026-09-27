from abc import ABC, abstractmethod


class LLMError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class LLMProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def generate(self, system: str, user: str, schema: dict) -> str:
        """Return final JSON text only; never return private model reasoning."""
