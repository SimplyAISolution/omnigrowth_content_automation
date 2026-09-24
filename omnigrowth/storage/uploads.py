"""Content-addressed store for uploaded source files (DATA_MODEL OQ-7: outside the DB).

Files live at ``<upload_dir>/<sha256[:2]>/<sha256>``; ``storage_uri`` is ``upload://<sha256>``.
Only hex digests become path components, so user input never reaches the filesystem path.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

from omnigrowth.core.errors import ErrorCode, OmniGrowthError

_SCHEME = "upload://"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class UploadStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, digest: str) -> Path:
        if not _DIGEST.match(digest):
            raise OmniGrowthError(ErrorCode.VALIDATION_FAILURE, "invalid storage key")
        return self.root / digest[:2] / digest

    def put(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        path = self._path(digest)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write-then-rename so a crash never leaves a truncated object behind.
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".upload-")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, path)
            except OSError:
                Path(tmp).unlink(missing_ok=True)
                raise
        return _SCHEME + digest

    def get(self, uri: str) -> bytes:
        if not uri.startswith(_SCHEME):
            raise OmniGrowthError(ErrorCode.VALIDATION_FAILURE, "unsupported storage uri")
        path = self._path(uri[len(_SCHEME) :])
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise OmniGrowthError(
                ErrorCode.INGESTION_FAILURE,
                "stored upload is missing",
                details={"reason": "UPLOAD_MISSING"},
            ) from exc
