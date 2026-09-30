"""Drop Cursor sandbox proxy env vars so downloads go direct."""

from __future__ import annotations

import os
import unittest

from omnipath_build.netenv import _SANDBOX_PROXY_VARS, drop_cursor_sandbox_proxies


class TestNetenv(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            key: os.environ.get(key)
            for key in (*_SANDBOX_PROXY_VARS, "__CURSOR_SANDBOX_ENV_RESTORE")
        }

    def tearDown(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_drop_only_when_sandbox_marker_present(self):
        os.environ.pop("__CURSOR_SANDBOX_ENV_RESTORE", None)
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:9"
        self.assertFalse(drop_cursor_sandbox_proxies())
        self.assertEqual(os.environ["HTTPS_PROXY"], "http://127.0.0.1:9")

        os.environ["__CURSOR_SANDBOX_ENV_RESTORE"] = "1"
        self.assertTrue(drop_cursor_sandbox_proxies())
        self.assertNotIn("HTTPS_PROXY", os.environ)
