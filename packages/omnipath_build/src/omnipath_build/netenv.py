"""Avoid Cursor sandbox proxies hijacking pypath/requests downloads.

Agent-started processes inherit ``HTTP_PROXY=http://127.0.0.1:<port>``. That
proxy returns 403 for scientific hosts such as SIGNOR, so rebuilds fail even
though the machine can reach the internet.
"""

from __future__ import annotations

import os

_SANDBOX_PROXY_VARS = (
    "ALL_PROXY",
    "all_proxy",
    "GIT_HTTP_PROXY",
    "GIT_HTTPS_PROXY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "SOCKS5_PROXY",
    "SOCKS_PROXY",
    "socks5_proxy",
    "socks_proxy",
)


def drop_cursor_sandbox_proxies() -> bool:
    if not os.environ.get("__CURSOR_SANDBOX_ENV_RESTORE"):
        return False
    dropped = False
    for key in _SANDBOX_PROXY_VARS:
        if key in os.environ:
            os.environ.pop(key, None)
            dropped = True
    return dropped
