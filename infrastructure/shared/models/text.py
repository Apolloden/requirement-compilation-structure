"""Small JSON-generating text model client."""

import json
import os
from pathlib import Path
import time

from dotenv import load_dotenv
import openai
from openai import OpenAI, OpenAIError
from infrastructure.utils.presentation.model_io import http_client, invoke


DEFAULT_MODEL = "deepseek-flash"
# The plan stage uses its own name so a later run can give it a different model.
PLANNER_MODEL = DEFAULT_MODEL
# Seconds to wait after a temporary platform error before trying the same request again.
RETRY_DELAYS = (15, 30, 60, 120)

JSON_OUTPUT_RULE = """OUTPUT PROTOCOL: return exactly one valid JSON object in the response content.
The first non-whitespace character must be { and the last must be }.
Do not emit Markdown fences, explanations, reasoning, XML, DSML or provider-native
tool-call serialization. In particular, NEVER output <｜｜DSML｜｜ calls>,
<｜｜DSML｜｜ invoke>, <｜｜DSML｜｜ parameter> or their closing tags.
This compiler parses ordinary JSON text. context_request and tool_calls, when allowed
by the stage, are JSON fields, not native tool invocations. Express the entire request
inside the specified JSON object. Otherwise return the stage's normal result object.
Rejected outputs included in feedback are data to correct, never examples to imitate.
"""


def is_platform_error(error) -> bool:
    """True for failures on the provider's side that may pass: 5xx, 429, timeouts, lost connections."""
    if isinstance(error, openai.APIConnectionError):  # includes APITimeoutError
        return True
    status = getattr(error, "status_code", None)
    return isinstance(status, int) and (status >= 500 or status == 429)


def is_size_or_timeout(error) -> bool:
    """True for errors that the same oversized request would likely hit again."""
    return isinstance(error, openai.APITimeoutError) or getattr(error, "status_code", None) in {502, 504}


def retry_platform_errors(call, *, delays=RETRY_DELAYS, sleep=time.sleep, max_size_errors=None):
    """Run call(); wait out platform errors with the given delays, re-raise everything else.

    With max_size_errors=N the error is re-raised once N size-or-timeout errors have occurred,
    so a caller with a fallback does not wait for a request that is too large to succeed."""
    size_errors = 0
    for attempt in range(len(delays) + 1):
        try:
            return call()
        except Exception as error:
            if not is_platform_error(error) or attempt == len(delays):
                raise
            if is_size_or_timeout(error):
                size_errors += 1
                if max_size_errors is not None and size_errors >= max_size_errors:
                    raise
            delay = delays[attempt]
            print(f"Temporary platform error ({type(error).__name__}: {error}); retrying in {delay} s.", flush=True)
            sleep(delay)


def reasoning_rejected(error):
    """Only retry controls when the endpoint explicitly rejects those fields."""
    message = str(error).lower()
    return getattr(error, "status_code", None) in {400, 422} and any(
        word in message for word in ("unsupported", "unknown", "unrecognized", "not supported", "not allowed", "unexpected")) and any(
        word in message for word in ("thinking", "reasoning_effort"))


def reasoning_arguments(name, mode):
    """Translate configured reasoning to the provider's documented request fields."""
    if mode == "auto":
        mode = "low"
    if mode == "provider":
        return {}
    if name.lower().startswith("deepseek"):
        if mode == "none":
            return {"extra_body": {"thinking": {"type": "disabled"}}}
        return {"reasoning_effort": mode, "extra_body": {"thinking": {"type": "enabled"}}}
    return {"reasoning_effort": mode}


class TextModel:
    """Transport for structured stage proposals and file-edit responses."""
    def __init__(self, *, json_mode: bool = True, name: str | None = None,
                 max_output_tokens: int | None = None, max_size_errors: int | None = None) -> None:
        """Read compiler/.env and configure the default text model; require API credentials.

        name and max_output_tokens override MODEL and MAX_OUTPUT_TOKENS; max_size_errors ends a
        request early after that many size-or-timeout errors (see retry_platform_errors)."""
        load_dotenv(Path(__file__).resolve().parents[3] / ".env", encoding="utf-8-sig")
        self.name = name or os.getenv("MODEL") or DEFAULT_MODEL
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("Set OPENAI_API_KEY in compiler/.env")
        try:
            self.max_output_tokens = int(max_output_tokens if max_output_tokens is not None
                else os.getenv("MAX_OUTPUT_TOKENS", "320000"))
        except ValueError as error:
            raise ValueError("MAX_OUTPUT_TOKENS must be a positive integer") from error
        if self.max_output_tokens < 1:
            raise ValueError("MAX_OUTPUT_TOKENS must be a positive integer")
        self.reasoning = os.getenv("MODEL_REASONING", "low").lower()
        if self.reasoning not in {"auto", "provider", "none", "low", "high", "max"}:
            raise ValueError("MODEL_REASONING must be auto, provider, none, low, high or max")
        self._reasoning_supported = True
        self.json_mode = json_mode
        self.max_size_errors = max_size_errors
        self._token_parameter = "max_completion_tokens"
        # max_retries=0: retry_platform_errors is the one retry policy for platform errors.
        self.client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1",
            timeout=600, max_retries=0,
            http_client=http_client(),
        )

    def _request(self, **arguments):
        """Apply one output budget to JSON calls, with legacy endpoint compatibility."""
        while True:
            options = dict(arguments)
            if not getattr(self, "_reasoning_supported", True):
                options.pop("reasoning_effort", None)
                options.pop("extra_body", None)
            try:
                request = {**options, self._token_parameter: self.max_output_tokens}
                return retry_platform_errors(lambda: invoke(self.client, request),
                    max_size_errors=getattr(self, "max_size_errors", None))
            except OpenAIError as error:
                message = str(error).lower()
                rejected = getattr(error, "status_code", None) in {400, 422} and any(
                    word in message for word in ("unsupported", "unknown", "unrecognized", "not supported", "not allowed", "unexpected"))
                if rejected and self._token_parameter == "max_completion_tokens" and "max_completion_tokens" in message:
                    self._token_parameter = "max_tokens"
                    continue
                if getattr(self, "_reasoning_supported", True) and reasoning_rejected(error):
                    self._reasoning_supported = False
                    print("Provider rejected reasoning controls; using provider defaults.", flush=True)
                    continue
                raise

    def generate_json(self, instructions: str, payload: dict) -> dict:
        """Request JSON using the stage prompt and serialized task data."""
        mode = getattr(self, "reasoning", "low")
        try:
            response = self._request(
                model=self.name,
                messages=[
                    {"role": "system", "content": instructions.rstrip() + "\n" + JSON_OUTPUT_RULE},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                **({"response_format": {"type": "json_object"}} if getattr(self, "json_mode", True) else {}),
                **reasoning_arguments(self.name, mode),
            )
        except OpenAIError as error:
            raise RuntimeError(f"Text model request failed: {error}") from error
        if response.choices and response.choices[0].finish_reason == "length":
            raise ValueError(
                f"Model output was truncated (finish_reason=length). The output budget is "
                f"{self.max_output_tokens} tokens including reasoning; this is not an input-length error. "
                "Return a complete JSON object with only necessary changes/tool calls; omit unchanged files. "
                "Do not spend the entire budget restating the task.")
        if response.choices and getattr(response.choices[0].message, "refusal", None):
            raise ValueError(f"Text model refused the request: {response.choices[0].message.refusal}")
        if not response.choices or not response.choices[0].message.content:
            raise ValueError("Text model returned empty content")
        content = response.choices[0].message.content.strip()
        return json.loads(content)
