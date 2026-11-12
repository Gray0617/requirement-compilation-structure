"""Small JSON-generating text model client."""

import json
import os
from pathlib import Path
from time import sleep

from dotenv import load_dotenv
from openai import OpenAI, OpenAIError
from infrastructure.utils.presentation.model_io import http_client, invoke


DEFAULT_MODEL = "deepseek-flash"

# Edit rounds receive a machine-generated failure report and answer with tool calls,
# so they are mechanical. Measured runs spent 95% of every output token thinking
# there. Only these rounds default to no thinking; planning stages keep theirs, and
# MODEL_REASONING_FILES=low restores the previous behaviour without a code change.
DEFAULT_FILE_REASONING = "none"

# A provider outage, rate limit or gateway error is not a wrong answer. Retrying a
# few times keeps one bad window from discarding an otherwise complete compilation.
TRANSIENT_STATUS = {408, 409, 429, 500, 502, 503, 504}
# Connection-level failures have no status code; status errors are classified by
# their code so that a 400/401/404 is never retried.
TRANSIENT_TYPES = {"APIConnectionError", "APITimeoutError", "ConnectError", "ConnectTimeout",
    "ReadTimeout", "WriteTimeout", "PoolTimeout", "RemoteProtocolError"}
MODEL_ATTEMPTS = max(1, int(os.getenv("MODEL_ATTEMPTS", "4") or 4))
BACKOFF_SECONDS = max(0.0, float(os.getenv("MODEL_BACKOFF_SECONDS", "2") or 2))


class ModelUnavailable(RuntimeError):
    """The provider stayed unavailable; work must stop rather than be re-planned."""


def transient_failure(error) -> bool:
    """Return True when retrying the same request could succeed."""
    if getattr(error, "status_code", None) in TRANSIENT_STATUS:
        return True
    for kind in type(error).__mro__:
        if kind.__name__ in TRANSIENT_TYPES:
            return True
    return False

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
    def __init__(self, *, json_mode: bool = True) -> None:
        """Read compiler/.env and configure the default text model; require API credentials."""
        load_dotenv(Path(__file__).resolve().parents[3] / ".env", encoding="utf-8-sig")
        self.name = os.getenv("MODEL") or DEFAULT_MODEL
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("Set OPENAI_API_KEY in compiler/.env")
        try:
            self.max_output_tokens = int(os.getenv("MAX_OUTPUT_TOKENS", "320000"))
        except ValueError as error:
            raise ValueError("MAX_OUTPUT_TOKENS must be a positive integer") from error
        if self.max_output_tokens < 1:
            raise ValueError("MAX_OUTPUT_TOKENS must be a positive integer")
        self.reasoning = os.getenv("MODEL_REASONING", "low").lower()
        if self.reasoning not in {"auto", "provider", "none", "low", "high", "max"}:
            raise ValueError("MODEL_REASONING must be auto, provider, none, low, high or max")
        # Edit rounds already receive a precise failure report, so thinking there is
        # optional: MODEL_REASONING_FILES=low restores it when accuracy matters more
        # than cost, while the default keeps the measured-cheaper behaviour.
        self.file_reasoning = (os.getenv("MODEL_REASONING_FILES", DEFAULT_FILE_REASONING).lower()
            or self.reasoning)
        if self.file_reasoning not in {"auto", "provider", "none", "low", "high", "max"}:
            raise ValueError("MODEL_REASONING_FILES must be auto, provider, none, low, high or max")
        self._reasoning_supported = True
        self.json_mode = json_mode
        self._token_parameter = "max_completion_tokens"
        self.client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1",
            timeout=600, max_retries=2,
            http_client=http_client(),
        )

    def _request_once(self, **arguments):
        """Apply one output budget to JSON calls, with legacy endpoint compatibility."""
        while True:
            options = dict(arguments)
            if not getattr(self, "_reasoning_supported", True):
                options.pop("reasoning_effort", None)
                options.pop("extra_body", None)
            try:
                return invoke(self.client, {**options, self._token_parameter: self.max_output_tokens})
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

    def _request(self, **arguments):
        """Retry transient provider failures, then report a bounded unavailability."""
        delay = BACKOFF_SECONDS
        for attempt in range(1, MODEL_ATTEMPTS + 1):
            try:
                return self._request_once(**arguments)
            except OpenAIError as error:
                if not transient_failure(error):
                    raise
                if attempt >= MODEL_ATTEMPTS:
                    raise ModelUnavailable(
                        f"provider unavailable after {MODEL_ATTEMPTS} attempts: {error}") from error
                print(f"[model] transient provider failure ({type(error).__name__}: {error}); "
                    f"retry {attempt}/{MODEL_ATTEMPTS} in {delay:.1f}s", flush=True)
                sleep(delay)
                delay *= 2
        raise ModelUnavailable(f"provider unavailable after {MODEL_ATTEMPTS} attempts")

    def generate_file_json(self, instructions: str, payload: dict) -> dict:
        """Request one edit-round response without long hidden reasoning.

        Edit rounds carry a precise, machine-generated failure report and return
        tool calls, so they are mechanical work. Measured runs spent 95% of every
        output token on thinking here and gained no accuracy from it.
        """
        return self.generate_json(instructions, payload,
            reasoning=getattr(self, "file_reasoning", "none"))

    def generate_json(self, instructions: str, payload: dict, reasoning: str | None = None) -> dict:
        """Request JSON using the stage prompt and serialized task data."""
        mode = reasoning or getattr(self, "reasoning", "low")
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
