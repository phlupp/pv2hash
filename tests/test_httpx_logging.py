"""Third-party HTTP request success messages must not crowd PV2Hash logs."""
from __future__ import annotations

import io
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pv2hash.logging_ext import setup as logging_setup


class HttpxLogNoiseTest(unittest.TestCase):
    def test_httpx_success_is_quiet_but_warnings_and_pv2hash_info_survive(self):
        root = logging.getLogger()
        httpx_logger = logging.getLogger("httpx")
        app_logger = logging.getLogger("pv2hash.test_logging")
        original_handlers = root.handlers[:]
        original_root_level = root.level
        original_httpx_level = httpx_logger.level
        original_app_level = app_logger.level
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            try:
                with patch.object(logging_setup, "LOG_DIR", path), patch.object(
                    logging_setup, "LOG_FILE", path / "pv2hash.log"
                ):
                    # Include the dynamic global log-level change from the GUI.
                    for level in ("INFO", "DEBUG", "INFO"):
                        logging_setup.setup_logging(level)
                        self.assertEqual(httpx_logger.level, logging.WARNING)
                        stream = io.StringIO()
                        handler = logging.StreamHandler(stream)
                        handler.setLevel(logging.DEBUG)
                        root.addHandler(handler)
                        try:
                            httpx_logger.info('HTTP Request: POST ... "HTTP/1.1 204 No Content"')
                            httpx_logger.warning("HTTP warning should be shown")
                            app_logger.info("PV2Hash logging is still working")
                            content = stream.getvalue()
                            self.assertNotIn("HTTP Request: POST", content)
                            self.assertIn("HTTP warning should be shown", content)
                            self.assertIn("PV2Hash logging is still working", content)
                        finally:
                            root.removeHandler(handler)
                            handler.close()
                    logfile = (path / "pv2hash.log").read_text(encoding="utf-8")
                    self.assertNotIn("204 No Content", logfile)
                    self.assertIn("HTTP warning should be shown", logfile)
                    self.assertIn("PV2Hash logging is still working", logfile)
            finally:
                for handler in root.handlers[:]:
                    if handler not in original_handlers:
                        root.removeHandler(handler)
                        handler.close()
                root.handlers[:] = original_handlers
                root.setLevel(original_root_level)
                httpx_logger.setLevel(original_httpx_level)
                app_logger.setLevel(original_app_level)
