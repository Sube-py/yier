from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

import httpx
from litestar import Request
from litestar.response import File, Response

from yier_web.schemas import FrontendHealth


class FrontendService:
    def __init__(
        self,
        project_root: Path,
        vite_origin: str = "http://127.0.0.1:5173",
    ) -> None:
        self.project_root = project_root.resolve()
        self.web_root = self.project_root / "web"
        self.dist_root = self.web_root / "dist"
        self.vite_origin = vite_origin.rstrip("/")

    async def get_status(self) -> FrontendHealth:
        if await self._vite_available():
            return FrontendHealth(ready=True, mode="proxy", detail=f"Proxying {self.vite_origin}")
        if (self.dist_root / "index.html").exists():
            return FrontendHealth(ready=True, mode="static", detail=f"Serving {self.dist_root}")
        return FrontendHealth(
            ready=False,
            mode="missing",
            detail="Start Vite dev server or build the frontend bundle first.",
        )

    async def handle_request(self, request: Request[Any, Any, Any], path: str) -> Response | File:
        if await self._vite_available():
            return await self._proxy_request(request)

        resolved_path = self._resolve_dist_path(path)
        if resolved_path is not None and resolved_path.exists():
            return File(path=resolved_path)

        index_path = self.dist_root / "index.html"
        if index_path.exists():
            return File(path=index_path)

        return Response(
            content="Frontend is unavailable. Start `pnpm dev` in `web` or build the frontend.",
            media_type="text/plain",
            status_code=503,
        )

    async def _proxy_request(self, request: Request[Any, Any, Any]) -> Response:
        target_url = f"{self.vite_origin}{request.url.path}"
        if request.url.query:
            target_url = f"{target_url}?{request.url.query}"

        request_headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in {"host", "connection", "content-length"}
        }

        async with httpx.AsyncClient(follow_redirects=False, timeout=10.0) as client:
            upstream = await client.request(
                request.method,
                target_url,
                headers=request_headers,
                content=await request.body(),
            )

        response_headers = {
            key: value
            for key, value in upstream.headers.items()
            if key.lower() not in {"connection", "content-length", "transfer-encoding"}
        }
        media_type = upstream.headers.get("content-type")
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=media_type,
            headers=response_headers,
        )

    def _resolve_dist_path(self, path: str) -> Path | None:
        if not path:
            candidate = self.dist_root / "index.html"
            return candidate if candidate.exists() else None

        candidate = (self.dist_root / path).resolve()
        try:
            candidate.relative_to(self.dist_root.resolve())
        except ValueError:
            return None

        if candidate.exists() and candidate.is_file():
            return candidate
        if Path(path).suffix:
            return None
        return None

    async def _vite_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=0.35) as client:
                response = await client.get(self.vite_origin)
        except httpx.HTTPError:
            return False
        return response.status_code < 500
