"""Model client: deepseek-flash with thinking off, streaming with an early stop, retries, continuation, usage and logs."""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import openai

from arcflash import config

OPTIONAL_FIELDS = ("thinking", "stream_options", "response_format", "max_tokens")
_RUNAWAY_TAIL_CHARS = 20_000  # how much of a streaming answer the runaway check looks at
_PAIR_END = ">>>>>>> REPLACE"


@dataclass
class Completion:
    text: str
    finish_reason: str            # "stop", "length", "runaway", "cut", "guard" (stop_when said stop), or what the API sent
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    seconds: float = 0.0
    estimated: bool = False       # no usage record arrived (cut stream, runaway stop): tokens counted from characters


class LLMError(RuntimeError):
    pass


class _StreamError(Exception):
    """An error while reading a stream that had already started."""


class Usage:
    """Token and cost totals over all requests; thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[dict] = []

    def add(self, purpose: str, completion: Completion) -> None:
        with self._lock:
            self._records.append({"purpose": purpose, "input": completion.input_tokens, "cached": completion.cached_tokens,
                                  "output": completion.output_tokens, "reasoning": completion.reasoning_tokens,
                                  "seconds": round(completion.seconds, 2), "finish": completion.finish_reason,
                                  "estimated": completion.estimated})

    def records(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._records]

    def totals(self) -> dict:
        records = self.records()
        t = {key: sum(r[key] for r in records) for key in ("input", "cached", "output", "reasoning")}
        p = config.PRICES_CNY_PER_MILLION
        list_cny = ((t["input"] - t["cached"]) * p["input"] + t["cached"] * p["cached"] + t["output"] * p["output"]) / 1e6
        return {"calls": len(records), **t, "list_cny": list_cny, "billed_cny": list_cny * config.PLATFORM_COST_FACTOR}


def _ends_in_number_run(text: str) -> bool:
    """The text ends (trailing spaces and one comma ignored) with RUNAWAY_NUMBER_RUN comma-separated integers in a row."""
    parts = text[-config.RUNAWAY_NUMBER_RUN * 16:].rstrip().removesuffix(",").split(",")
    run = 0
    for part in reversed(parts):
        if not part.strip().lstrip("-").isdigit():
            break
        run += 1
        if run >= config.RUNAWAY_NUMBER_RUN:
            return True
    return False


_WORD = re.compile(r"\w+", re.ASCII)


def is_runaway(text: str) -> bool:
    """True when the answer ends in a long blank stretch, the same non-empty line many times, a long run of numbers,
    or one endless word."""
    if len(text) >= config.RUNAWAY_BLANK_CHARS and not text[-config.RUNAWAY_BLANK_CHARS:].strip():
        return True
    if _ends_in_number_run(text):
        return True
    if len(text) >= config.RUNAWAY_WORD_CHARS and _WORD.fullmatch(text[-config.RUNAWAY_WORD_CHARS:]):
        return True
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    last = lines[-config.RUNAWAY_REPEAT_LINES:]
    return len(last) == config.RUNAWAY_REPEAT_LINES and last[0].strip() != "" and all(line == last[0] for line in last)


def strip_overlap(previous: str, continuation: str) -> str:
    """Remove the longest start of the continuation (10 to 400 characters) that the previous text ends with."""
    for size in range(min(400, len(continuation), len(previous)), 9, -1):
        if previous.endswith(continuation[:size]):
            return continuation[size:]
    return continuation


def _messages(system: str | None, user) -> list[dict]:
    return ([{"role": "system", "content": system}] if system is not None else []) + [{"role": "user", "content": user}]


def _render_for_log(messages: list[dict], image_note: str = "") -> str:
    parts = []
    for m in messages:
        content = m["content"]
        if isinstance(content, list):
            content = "\n".join(p["text"] if p.get("type") == "text" else image_note for p in content)
        parts.append(f"### {m['role']}\n{content}")
    return "\n\n".join(parts) + "\n"


class LLM:
    def __init__(self, *, usage: Usage | None = None, log_dir: Path | None = None, client=None, sleep=time.sleep,
                 clock=time.monotonic) -> None:
        if client is None:
            key = os.environ.get("OPENAI_API_KEY")
            if not key:
                raise LLMError("OPENAI_API_KEY is not set")
            client = openai.OpenAI(api_key=key, base_url=os.environ.get("OPENAI_BASE_URL") or None, max_retries=0,
                                   timeout=openai.Timeout(600, read=config.STREAM_IDLE_SECONDS))
        self.client = client
        self.usage = usage if usage is not None else Usage()
        self.log_dir = Path(log_dir) if log_dir else None
        self.sleep = sleep
        self.clock = clock
        self.started = clock()   # HARD_STOP_SECONDS counts from here
        self._dropped: set[str] = set()
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(config.MAX_PARALLEL_CALLS)
        self._background_slots = threading.BoundedSemaphore(config.MAX_BACKGROUND_CALLS)
        self._cache_hits: dict[str, int] = {}   # prompt start → cached tokens of the last call with a usage record
        self._count = 0

    # ---- public calls -------------------------------------------------------------------------

    def complete(self, purpose: str, system: str | None, user: str, *, max_tokens: int,
                 json_mode: bool = False, temperature: float = config.TEMPERATURE) -> Completion:
        return self._request(purpose, _messages(system, user), max_tokens, json_mode, temperature)

    def complete_long(self, purpose: str, system: str | None, user: str, *, max_tokens: int,
                      json_mode: bool = False, temperature: float = config.TEMPERATURE, background: bool = False,
                      done=None, stop_when=None, continue_cut: bool = True) -> Completion:
        """`background` calls (the test writer) wait for their own slots, never for those of the critical path.
        `done(text)` says whether a cut answer is already whole (the cut lost only the end of the stream); it is then
        not continued, because a continuation of a whole answer adds text that breaks it.
        `stop_when(text)` is asked each time a new SEARCH/REPLACE pair ends; True closes the stream (finish "guard"),
        and a guarded answer is never continued. With `continue_cut=False` an answer at the token limit is not continued."""
        messages = _messages(system, user)
        result = self._request(purpose, messages, max_tokens, json_mode, temperature, background=background, stop_when=stop_when)
        if result.finish_reason == "runaway":
            messages = _messages(system, user + "\n\n" + config.RUNAWAY_PROMPT)
            result = self._add(result, self._request(f"{purpose}+retry", messages, max_tokens, json_mode, temperature,
                                                     background=background, stop_when=stop_when), replace=True)
        n = 0
        continued = ("length", "cut") if continue_cut else ("cut",)
        # "cut": the connection closed mid-answer (no finish reason, no usage); the text so far is kept and continued.
        while result.finish_reason in continued and n < config.MAX_CONTINUATIONS:
            if result.finish_reason == "cut" and done is not None and done(result.text):
                result.finish_reason = "stop"
                break
            n += 1
            more = messages + [{"role": "assistant", "content": result.text}, {"role": "user", "content": config.CONTINUE_PROMPT}]
            # A continuation is a fragment, never a JSON object of its own, so JSON mode stays off for it.
            part = self._request(f"{purpose}+{n}", more, max_tokens, False, temperature, background=background,
                                 stop_when=stop_when)
            part.text = strip_overlap(result.text, part.text)
            result = self._add(result, part)
        return result

    def describe_image(self, purpose: str, prompt: str, image: bytes, mime: str, *, max_tokens: int) -> Completion:
        url = f"data:{mime};base64," + base64.b64encode(image).decode("ascii")
        messages = [{"role": "user", "content": [{"type": "text", "text": prompt},
                                                  {"type": "image_url", "image_url": {"url": url}}]}]
        return self._request(purpose, messages, max_tokens, False, config.TEMPERATURE,
                             image_note=f"[image {mime}, {len(image)} bytes]")

    # ---- internals ----------------------------------------------------------------------------

    @staticmethod
    def _add(total: Completion, part: Completion, replace: bool = False) -> Completion:
        return Completion(text=part.text if replace else total.text + part.text, finish_reason=part.finish_reason,
                          input_tokens=total.input_tokens + part.input_tokens,
                          cached_tokens=total.cached_tokens + part.cached_tokens,
                          output_tokens=total.output_tokens + part.output_tokens,
                          reasoning_tokens=total.reasoning_tokens + part.reasoning_tokens,
                          seconds=total.seconds + part.seconds)

    def _kwargs(self, messages, max_tokens, json_mode, temperature) -> dict:
        with self._lock:
            dropped = set(self._dropped)
        kwargs = {"model": config.MODEL, "messages": messages, "temperature": temperature, "stream": True}
        kwargs["max_completion_tokens" if "max_tokens" in dropped else "max_tokens"] = max_tokens
        if "stream_options" not in dropped:
            kwargs["stream_options"] = {"include_usage": True}
        if not config.THINKING and "thinking" not in dropped:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        if json_mode and "response_format" not in dropped:
            kwargs["response_format"] = {"type": "json_object"}
        return kwargs

    def _rejected_field(self, error: openai.APIStatusError) -> str | None:
        message = f"{error} {getattr(error, 'body', '')}"
        with self._lock:
            for name in OPTIONAL_FIELDS:
                if name in message and name not in self._dropped:
                    self._dropped.add(name)
                    return name
        return None

    def _request(self, purpose, messages, max_tokens, json_mode, temperature, image_note="", background=False,
                 stop_when=None) -> Completion:
        waits = list(config.RETRY_WAITS)
        slots = self._background_slots if background else self._slots
        while True:
            if self.clock() - self.started > config.HARD_STOP_SECONDS:
                raise LLMError(f"{purpose}: hard stop after {config.HARD_STOP_SECONDS // 60} minutes; no new request")
            kwargs = self._kwargs(messages, max_tokens, json_mode, temperature)
            try:
                with slots:
                    completion = self._stream(kwargs, stop_when)
                break
            except openai.APIStatusError as e:
                if e.status_code == 400 and self._rejected_field(e):
                    continue
                if e.status_code != 429 and e.status_code < 500:
                    raise LLMError(f"{purpose}: HTTP {e.status_code}: {e}") from e
                error = e
            except (openai.APIConnectionError, _StreamError) as e:  # includes timeouts
                error = e
            if not waits:
                raise LLMError(f"{purpose}: gave up after {len(config.RETRY_WAITS)} waits: {error}") from error
            print(f"[arclaser] {purpose}: retry in {waits[0]} s after {type(error).__name__}: {str(error)[:120]}", flush=True)
            self.sleep(waits.pop(0))
        self._book_cache(messages, completion)
        self.usage.add(purpose, completion)
        self._log(purpose, messages, completion, image_note)
        return completion

    def _book_cache(self, messages, completion: Completion) -> None:
        """A call without a usage record (stopped by the guard, cut, runaway) gets the cache hit of the last call that
        began with the same system prompt and user text: the endpoint cached that prefix, we only lost its count."""
        if any(not isinstance(m["content"], str) for m in messages[:2]):
            return
        key = "\x00".join(m["content"] for m in messages[:2])[:config.CACHE_KEY_CHARS]
        with self._lock:
            if not completion.estimated:
                self._cache_hits[key] = completion.cached_tokens
            elif key in self._cache_hits:
                completion.cached_tokens = min(self._cache_hits[key], completion.input_tokens)

    def _stream(self, kwargs, stop_when=None) -> Completion:
        started = time.monotonic()
        stream = self.client.chat.completions.create(**kwargs)
        text, finish, usage, scanned = "", None, None, 0
        chunks = iter(stream)
        try:
            while True:
                try:
                    chunk = next(chunks)
                except StopIteration:
                    break
                except Exception as e:
                    if not text:   # nothing arrived yet: the request is retried
                        raise _StreamError(str(e)) from e
                    if finish is None:   # an idle timeout or a dropped connection mid-answer: keep the text, continue it
                        finish = "cut"
                    break                # after the finish chunk only the usage record was lost: the answer is whole
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                for choice in getattr(chunk, "choices", None) or []:
                    content = getattr(choice.delta, "content", None) if choice.delta is not None else None
                    if content:
                        text += content
                    if choice.finish_reason:
                        finish = choice.finish_reason
                if text and is_runaway(text[-_RUNAWAY_TAIL_CHARS:]):
                    finish = "runaway"
                    break
                if stop_when is not None and text.find(_PAIR_END, max(0, scanned - len(_PAIR_END))) >= 0:
                    scanned = len(text)   # asked once per new pair end, not per chunk
                    if stop_when(text):
                        finish = "guard"
                        break
                elif stop_when is not None:
                    scanned = len(text)
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        if finish is None and usage is None:   # the connection closed before the answer ended
            if not text:
                raise _StreamError("the stream ended without an answer")
            finish = "cut"
        numbers = _usage_numbers(usage) if usage is not None else _estimated_numbers(kwargs["messages"], text)
        return Completion(text=text, finish_reason=finish or "stop", seconds=time.monotonic() - started,
                          estimated=usage is None, **numbers)

    def _log(self, purpose, messages, completion: Completion, image_note: str) -> None:
        if not self.log_dir:
            return
        with self._lock:
            self._count += 1
            n = self._count
        self.log_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{n:03d}-" + re.sub(r"[^A-Za-z0-9._-]", "_", purpose)
        (self.log_dir / f"{stem}.prompt.txt").write_text(_render_for_log(messages, image_note), encoding="utf-8")
        (self.log_dir / f"{stem}.response.txt").write_text(completion.text, encoding="utf-8")
        line = {"n": n, "purpose": purpose, "input": completion.input_tokens, "cached": completion.cached_tokens,
                "output": completion.output_tokens, "reasoning": completion.reasoning_tokens,
                "seconds": round(completion.seconds, 2), "finish": completion.finish_reason}
        with self._lock, open(self.log_dir / "calls.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")


def _estimated_numbers(messages: list[dict], text: str) -> dict:
    """Tokens from characters for a call without a usage record; input counted as uncached, so the cost cap errs high."""
    chars = 0
    for m in messages:
        content = m["content"]
        chars += len(content) if isinstance(content, str) else sum(len(p.get("text", "")) for p in content)
    return {"input_tokens": round(chars / config.CHARS_PER_TOKEN), "cached_tokens": 0,
            "output_tokens": round(len(text) / config.CHARS_PER_TOKEN), "reasoning_tokens": 0}


def _usage_numbers(usage) -> dict:
    if usage is None:
        return {}
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(usage, "prompt_cache_hit_tokens", None)
    if cached is None and details is not None:
        cached = getattr(details, "cached_tokens", None)
    out_details = getattr(usage, "completion_tokens_details", None)
    reasoning = getattr(out_details, "reasoning_tokens", None) if out_details is not None else None
    return {"input_tokens": getattr(usage, "prompt_tokens", 0) or 0, "cached_tokens": cached or 0,
            "output_tokens": getattr(usage, "completion_tokens", 0) or 0, "reasoning_tokens": reasoning or 0}
