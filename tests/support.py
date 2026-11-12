"""Shared test helpers: paths, a safe environment for subprocesses and the integration switch."""
import os
import unittest
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = AGENT_ROOT / "tests" / "fixtures"

# Points the model endpoint at a closed local port, so no test can reach a real model or use a real key.
SAFE_ENV = {**os.environ, "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "OPENAI_API_KEY": "test-key"}


def integration(test_or_class):
    """Skip unless ARCFLASH_INTEGRATION=1 (these tests use real npm and Chromium)."""
    return unittest.skipUnless(os.environ.get("ARCFLASH_INTEGRATION") == "1",
                               "integration test; set ARCFLASH_INTEGRATION=1")(test_or_class)
