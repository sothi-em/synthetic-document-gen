"""Node pi-agent subprocess lifecycle and SSE proxy.

The web app spawns the TypeScript agent (``agent/dist/index.js``) as a
child process on a free 127.0.0.1 port and proxies it at ``/api/agent/*``
so the browser only ever talks to this origin. When Node is missing or
the agent has not been built (``cd agent && pnpm install && pnpm build``),
the host degrades gracefully: the app keeps working and the chat
endpoints report 503.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import socket
from pathlib import Path
from typing import Any

import httpx
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

#: Repository root (this file lives in ``document_gen/``).
_REPO_ROOT = Path(__file__).resolve().parent.parent

#: Default API port used to point the agent back at this server when the
#: port is not known (see ``DOCUMENT_GEN_API_PORT``, set by the CLI).
_DEFAULT_API_PORT = 8000


def _free_port() -> int:
    """Return a free TCP port on 127.0.0.1."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class AgentHost:
    """Manage the node pi-agent subprocess.

    Args:
        api_port: Port of this FastAPI server; the agent is pointed at it
            via ``DOCUMENT_GEN_API_URL``.
    """

    def __init__(self, api_port: int = _DEFAULT_API_PORT) -> None:
        self._api_port = api_port
        self._proc: asyncio.subprocess.Process | None = None
        self._port: int | None = None
        self._available = False

    @property
    def available(self) -> bool:
        """Whether the agent is running and passed its health check."""
        return self._available

    @property
    def base_url(self) -> str:
        """Agent base URL (empty string when not started)."""
        return f"http://127.0.0.1:{self._port}" if self._port else ""

    async def start(self) -> None:
        """Spawn the agent subprocess.

        When Node is missing or the built entry does not exist, log a
        warning and stay unavailable (no exception).
        """
        node = shutil.which("node")
        entry = _REPO_ROOT / "agent" / "dist" / "index.js"
        if node is None or not entry.is_file():
            logger.warning(
                "pi agent unavailable (node: %s; entry: %s); chat disabled",
                node or "not found",
                "missing" if not entry.is_file() else "ok",
            )
            return

        port = _free_port()
        env = {
            **os.environ,
            "DOCUMENT_GEN_API_URL": f"http://127.0.0.1:{self._api_port}",
            "AGENT_PORT": str(port),
            "PI_CODING_AGENT_DIR": str(_REPO_ROOT / "agent" / ".pi"),
        }
        self._proc = await asyncio.create_subprocess_exec(
            node,
            str(entry),
            env=env,
            cwd=str(_REPO_ROOT / "agent"),
        )
        self._port = port
        logger.info("pi agent started (pid %s) on port %s", self._proc.pid, port)

    async def wait_ready(self, timeout: float = 30.0) -> None:
        """Poll the agent health endpoint until it answers 200 or timeout.

        Sets ``available`` accordingly; also gives up early when the
        subprocess exits.
        """
        if self._port is None or self._proc is None:
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        async with httpx.AsyncClient() as client:
            while loop.time() < deadline:
                if self._proc.returncode is not None:
                    logger.warning(
                        "pi agent exited early (code %s)", self._proc.returncode
                    )
                    return
                try:
                    resp = await client.get(f"{self.base_url}/health", timeout=2.0)
                    if resp.status_code == 200:
                        self._available = True
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.2)
        logger.warning("pi agent did not become ready within %.0fs", timeout)

    async def stop(self) -> None:
        """Terminate the agent subprocess (best effort)."""
        self._available = False
        proc, self._proc = self._proc, None
        if proc is None or proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()


async def proxy_chat(base_url: str, body: dict[str, Any]) -> StreamingResponse:
    """Stream the agent's SSE chat response verbatim to the caller.

    Args:
        base_url: Agent base URL (``AgentHost.base_url``).
        body: Parsed ``POST /api/agent/chat`` JSON body, forwarded as-is.

    Returns:
        A ``StreamingResponse`` with ``text/event-stream`` media type.
        Backend failures are surfaced as a single SSE ``error`` event.
    """

    async def stream() -> Any:
        client = httpx.AsyncClient(
            # LLM streams can be idle for a long time between events
            # (tool execution, slow local models): no read timeout.
            timeout=httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0)
        )
        try:
            async with client.stream("POST", f"{base_url}/chat", json=body) as resp:
                if resp.status_code != 200:
                    yield (
                        "event: error\n"
                        f"data: {json.dumps({'message': f'agent returned {resp.status_code}'})}\n\n"
                    )
                    return
                async for line in resp.aiter_lines():
                    yield line + "\n"
        except httpx.HTTPError as exc:
            yield (
                "event: error\n"
                f"data: {json.dumps({'message': f'agent proxy error: {type(exc).__name__}: {exc}'})}\n\n"
            )
        finally:
            await client.aclose()

    return StreamingResponse(stream(), media_type="text/event-stream")


async def proxy_health(base_url: str) -> dict[str, Any]:
    """Fetch the agent's ``/health`` payload.

    Raises:
        httpx.HTTPError: When the agent is unreachable or unhealthy.
    """
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(f"{base_url}/health")
        resp.raise_for_status()
        return resp.json()
