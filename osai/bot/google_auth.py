"""Google service-account access tokens for the read-only Drive connector."""

from __future__ import annotations

import threading
from typing import Any

from ..connectors.google_drive import DriveAccessDenied
from ..contracts import ExecutionContext

READ_ONLY_SCOPES = (
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
)


class ServiceAccountTokenProvider:
    """Mint short-lived read-only tokens from a service-account key file.

    Folders the bot may read are shared with the service account's email; it
    has no other access. The key file stays on the server, outside Git.
    """

    def __init__(self, key_file: str, *, credentials: Any = None) -> None:
        self._lock = threading.Lock()
        if credentials is None:
            try:
                from google.oauth2 import service_account
            except ImportError as exc:  # pragma: no cover - depends on install
                raise RuntimeError("install 'google-auth' and 'requests' for Google access") from exc
            credentials = service_account.Credentials.from_service_account_file(key_file, scopes=list(READ_ONLY_SCOPES))
        self._credentials = credentials

    def bearer_token(self, *, context: ExecutionContext, connector_id: str) -> str:
        with self._lock:
            if not self._credentials.valid:
                try:
                    from google.auth.transport.requests import Request

                    self._credentials.refresh(Request())
                except Exception as exc:
                    raise DriveAccessDenied("source credential unavailable") from exc
            token = self._credentials.token
        if not isinstance(token, str) or not token:
            raise DriveAccessDenied("source credential unavailable")
        return token
