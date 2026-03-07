import asyncio
from functools import partial
import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


async def request_json(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[Dict[str, Any]] = None,
    timeout: float = 30.0,
) -> Any:
    loop = asyncio.get_running_loop()
    fn = partial(_request_json_sync, method, url, headers, body, timeout)
    return await loop.run_in_executor(None, fn)


def _request_json_sync(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]],
    body: Optional[Dict[str, Any]],
    timeout: float,
) -> Any:
    data = None
    req_headers = headers or {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req_headers = {**req_headers, "Content-Type": "application/json"}

    request = urllib.request.Request(url, data=data, method=method.upper(), headers=req_headers)

    with urllib.request.urlopen(request, timeout=timeout) as resp:
        raw = resp.read()
        text = raw.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
