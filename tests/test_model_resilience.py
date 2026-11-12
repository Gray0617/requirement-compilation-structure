"""A provider outage must not consume edit rounds or discard a whole stage.

A real ARC-Bench run died with `503 ... model_proxy_error` inside stage 08, which
left every page unimplemented and scored 1/12 hidden tests. These tests pin the
retry policy and the "not a wrong answer" propagation.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from openai import APIConnectionError, APIStatusError, BadRequestError
import httpx

from infrastructure.shared.agent.loop import run_file_agent
from infrastructure.shared.models import text as text_model
from infrastructure.shared.models.text import ModelUnavailable, TextModel, transient_failure


def status_error(code):
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    response = httpx.Response(code, request=request)
    return APIStatusError("provider error", response=response, body=None)


class TransientClassification(unittest.TestCase):
    def test_gateway_and_rate_limit_statuses_are_transient(self):
        for code in (429, 500, 502, 503, 504, 408):
            self.assertTrue(transient_failure(status_error(code)), code)

    def test_request_errors_are_not_transient(self):
        request = httpx.Request("POST", "https://example.invalid")
        self.assertFalse(transient_failure(BadRequestError(
            "bad", response=httpx.Response(400, request=request), body=None)))
        self.assertFalse(transient_failure(status_error(404)))

    def test_connection_errors_are_transient(self):
        request = httpx.Request("POST", "https://example.invalid")
        self.assertTrue(transient_failure(APIConnectionError(request=request)))


class RetryPolicy(unittest.TestCase):
    def model(self):
        instance = TextModel.__new__(TextModel)
        instance.name = "deepseek-flash"
        instance.max_output_tokens = 1000
        instance.reasoning = "low"
        instance._reasoning_supported = True
        instance.json_mode = True
        instance._token_parameter = "max_completion_tokens"
        instance.client = Mock()
        return instance

    def test_transient_failures_are_retried_until_success(self):
        instance = self.model()
        calls = [status_error(503), status_error(502), "ok"]

        def once(**arguments):
            value = calls.pop(0)
            if isinstance(value, str):
                return value
            raise value

        with patch.object(instance, "_request_once", side_effect=once), \
                patch.object(text_model, "sleep", return_value=None):
            self.assertEqual(instance._request(model="m"), "ok")
        self.assertEqual(len(calls), 0)

    def test_permanent_failure_is_raised_immediately(self):
        instance = self.model()
        request = httpx.Request("POST", "https://example.invalid")
        error = BadRequestError("bad request", response=httpx.Response(400, request=request), body=None)
        with patch.object(instance, "_request_once", side_effect=error) as once, \
                patch.object(text_model, "sleep", return_value=None):
            with self.assertRaises(BadRequestError):
                instance._request(model="m")
        self.assertEqual(once.call_count, 1)

    def test_exhausted_retries_report_unavailability(self):
        instance = self.model()
        with patch.object(instance, "_request_once", side_effect=status_error(503)) as once, \
                patch.object(text_model, "sleep", return_value=None):
            with self.assertRaises(ModelUnavailable):
                instance._request(model="m")
        self.assertEqual(once.call_count, text_model.MODEL_ATTEMPTS)


class AgentTurnAccounting(unittest.TestCase):
    def test_provider_outage_does_not_become_repair_feedback(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "a.txt").write_text("x", encoding="utf-8")
            from infrastructure.shared.agent.tools import FileTools
            workspace = FileTools(root, ["a.txt"])
            model = Mock(generate_json=Mock(side_effect=ModelUnavailable("down")))
            gate = Mock()
            console = Mock()
            with self.assertRaises(ModelUnavailable):
                run_file_agent(model, "", {}, workspace, gate, 5, console, "Example")
            self.assertEqual(model.generate_json.call_count, 1)
            gate.assert_not_called()
            self.assertFalse(any("Repair feedback" in call.args[0] for call in console.show.call_args_list))


if __name__ == "__main__":
    unittest.main()
