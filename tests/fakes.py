"""Fakes for the model: a fake OpenAI client (for arcflash.llm) and a fake LLM (for everything above it)."""
import threading
import time
from types import SimpleNamespace

import openai

from arcflash.llm import Completion, Usage

try:  # openai 3.x ships httpx2; older versions use httpx
    import httpx2 as _http
except ImportError:  # pragma: no cover
    import httpx as _http

_REQUEST = _http.Request("POST", "http://127.0.0.1:9/v1/chat/completions")
_STATUS_ERRORS = {400: openai.BadRequestError, 401: openai.AuthenticationError, 403: openai.PermissionDeniedError,
                  404: openai.NotFoundError, 409: openai.ConflictError, 422: openai.UnprocessableEntityError,
                  429: openai.RateLimitError}


def stream_chunks(text, *, finish="stop", usage=(100, 0, 10), pieces=3):
    """SDK-shaped stream chunks: `pieces` content chunks, the last with finish_reason, then a usage chunk."""
    size = max(1, -(-len(text) // pieces)) if text else 1
    parts = [text[i:i + size] for i in range(0, len(text), size)] or [""]
    chunks = []
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        choice = SimpleNamespace(delta=SimpleNamespace(content=part), finish_reason=finish if last else None)
        chunks.append(SimpleNamespace(choices=[choice], usage=None))
    prompt, cached, completion = usage
    chunks.append(SimpleNamespace(choices=[], usage=SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion, prompt_cache_hit_tokens=cached)))
    return chunks


def api_error(status: int, message: str) -> Exception:
    """A real openai.APIStatusError subclass, as the SDK raises it."""
    response = _http.Response(status, request=_REQUEST, json={"error": {"message": message}})
    cls = _STATUS_ERRORS.get(status, openai.InternalServerError if status >= 500 else openai.APIStatusError)
    return cls(message, response=response, body={"error": {"message": message}})


def connection_error() -> Exception:
    return openai.APIConnectionError(request=_REQUEST)


class FakeClient:
    """Stands in for openai.OpenAI: chat.completions.create(**kwargs) returns or raises scripted items."""

    def __init__(self, items, delay=0.0):
        self.items = list(items)
        self.delay = delay
        self.requests = []
        self.max_in_flight = 0
        self._in_flight = 0
        self._lock = threading.Lock()
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        with self._lock:
            self.requests.append(kwargs)
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
            item = self.items.pop(0)
        try:
            if self.delay:
                time.sleep(self.delay)
        finally:
            with self._lock:
                self._in_flight -= 1
        if isinstance(item, BaseException):
            raise item
        return item


class FakeLLM:
    """Same three methods as arcflash.llm.LLM, answering from scripted queues keyed by purpose prefix."""

    def __init__(self, script: dict):
        self.script = {key: list(items) for key, items in script.items()}
        self.calls = []
        self.usage = Usage()
        self._lock = threading.Lock()

    def _answer(self, method, purpose, system, user, max_tokens, json_mode=False, temperature=None):
        with self._lock:
            self.calls.append({"method": method, "purpose": purpose, "system": system, "user": user,
                               "max_tokens": max_tokens, "json_mode": json_mode, "temperature": temperature})
            keys = [k for k in self.script if purpose.startswith(k)]
            if not keys:
                raise AssertionError(f"FakeLLM has no answers for purpose {purpose!r}")
            queue = self.script[max(keys, key=len)]
            if not queue:
                raise AssertionError(f"FakeLLM ran out of answers for purpose {purpose!r}")
            item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        completion = item if isinstance(item, Completion) else Completion(text=item, finish_reason="stop")
        self.usage.add(purpose, completion)
        return completion

    def complete(self, purpose, system, user, *, max_tokens, json_mode=False, temperature=0.2):
        return self._answer("complete", purpose, system, user, max_tokens, json_mode, temperature)

    def complete_long(self, purpose, system, user, *, max_tokens, json_mode=False, temperature=0.2):
        return self._answer("complete_long", purpose, system, user, max_tokens, json_mode, temperature)

    def describe_image(self, purpose, prompt, image, mime, *, max_tokens):
        return self._answer("describe_image", purpose, None, prompt, max_tokens)


def as_blocks(files: dict) -> str:
    """Render files as file blocks (plan Appendix B.3)."""
    out = []
    for path, content in files.items():
        body = content if content.endswith("\n") else content + "\n"
        out.append(f"=== FILE: {path} ===\n{body}=== END FILE ===\n")
    return "".join(out)
