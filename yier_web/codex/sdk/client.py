from __future__ import annotations

from typing import Any, Callable

from codex_app_server import AppServerClient, AppServerConfig


class ApprovalAwareAppServerClient(AppServerClient):
    def __init__(
        self,
        config: AppServerConfig,
        approval_callback: Callable[[str, str, dict[str, Any]], dict[str, Any]],
    ) -> None:
        super().__init__(config=config)
        self._approval_callback = approval_callback

    def _handle_server_request(self, msg: dict[str, Any]) -> dict[str, Any]:
        method = msg.get("method")
        params = msg.get("params")
        request_id = msg.get("id")
        if not isinstance(method, str) or not isinstance(request_id, str):
            return {}
        if not isinstance(params, dict):
            params = {}
        return self._approval_callback(request_id, method, params)
