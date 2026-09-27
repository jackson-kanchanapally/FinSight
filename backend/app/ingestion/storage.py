import os
import re
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.ingestion.downloader import sha256_bytes


class DocumentStorage(Protocol):
    def store(self, content: bytes, digest: str) -> str: ...
    def resolve(self, relative_path: str) -> Path: ...


class LocalDocumentStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def store(self, content: bytes, digest: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or sha256_bytes(content) != digest:
            raise ValueError("Invalid document digest")
        relative = Path(digest[:2]) / f"{digest}.pdf"
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary = target.with_name(f".{digest}.{uuid4().hex}.tmp")
            try:
                temporary.write_bytes(content)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return relative.as_posix()

    def resolve(self, relative_path: str) -> Path:
        target = (self.root / relative_path).resolve()
        if not target.is_relative_to(self.root) or not target.is_file():
            raise FileNotFoundError("Document file is unavailable")
        return target

    def delete(self, relative_path: str) -> None:
        match = re.fullmatch(r"([0-9a-f]{2})/([0-9a-f]{64})\.pdf", relative_path)
        if match is None or not match.group(2).startswith(match.group(1)):
            raise ValueError("Invalid stored document path")
        target = (self.root / relative_path).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("Invalid stored document path")
        target.unlink(missing_ok=True)
        try:
            target.parent.rmdir()
        except OSError:
            pass
