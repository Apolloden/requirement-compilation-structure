import os, unittest
from unittest.mock import patch
import httpx, openai
from infrastructure.shared.models import text

REQUEST = httpx.Request("POST", "http://test/v1/chat/completions")
def status_error(code):
    cls = openai.BadRequestError if code == 400 else openai.InternalServerError if code >= 500 else openai.RateLimitError
    return cls("boom", response=httpx.Response(code, request=REQUEST), body=None)

class PlatformRetry(unittest.TestCase):
    def run_with(self, errors, **options):
        waits, calls = [], []
        def call():
            calls.append(1)
            if errors: raise errors.pop(0)
            return "ok"
        result = text.retry_platform_errors(call, sleep=waits.append, **options)
        return result, waits, len(calls)

    def test_503_twice_then_success(self):
        self.assertEqual(self.run_with([status_error(503), status_error(503)]), ("ok", [15, 30], 3))

    def test_429_and_lost_connection_are_retried(self):
        errors = [status_error(429), openai.APIConnectionError(request=REQUEST)]
        self.assertEqual(self.run_with(errors), ("ok", [15, 30], 3))

    def test_gives_up_after_the_four_delays(self):
        with self.assertRaises(openai.InternalServerError):
            self.run_with([status_error(503)] * 5)
        # five calls, four waits
        waits = []
        with self.assertRaises(openai.InternalServerError):
            text.retry_platform_errors(lambda: (_ for _ in ()).throw(status_error(503)), sleep=waits.append)
        self.assertEqual(waits, [15, 30, 60, 120])

    def test_http_400_is_not_retried(self):
        waits = []
        with self.assertRaises(openai.BadRequestError):
            text.retry_platform_errors(lambda: (_ for _ in ()).throw(status_error(400)), sleep=waits.append)
        self.assertEqual(waits, [])

    def test_two_size_or_timeout_errors_end_a_plan_call(self):
        waits = []
        errors = [openai.APITimeoutError(request=REQUEST), status_error(502), status_error(502)]
        def call(): raise errors.pop(0)
        with self.assertRaises(openai.InternalServerError):
            text.retry_platform_errors(call, sleep=waits.append, max_size_errors=2)
        self.assertEqual(waits, [15])

class ModelOptions(unittest.TestCase):
    def test_name_and_output_cap(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=False):
            os.environ.pop("MODEL", None); os.environ.pop("MAX_OUTPUT_TOKENS", None)
            self.assertEqual(text.TextModel().name, "deepseek-flash")
            self.assertEqual(text.TextModel().max_output_tokens, 320000)
            model = text.TextModel(name="other-model", max_output_tokens=32000)
            self.assertEqual((model.name, model.max_output_tokens), ("other-model", 32000))
        self.assertEqual(text.PLANNER_MODEL, text.DEFAULT_MODEL)
