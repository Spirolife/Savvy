"""The tool loop's token budget, cut-off replies and refusals. Run: python -m unittest discover tests

The API client is a fake that replays scripted responses and records each
request, so nothing here calls Anthropic.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import tools


def block(kind, text=""):
    return SimpleNamespace(type=kind, text=text) if kind == "text" else SimpleNamespace(type=kind, thinking="", signature="s")


def response(stop_reason, *blocks, out=10, stop_details=None):
    usage = SimpleNamespace(input_tokens=5, output_tokens=out, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocks), usage=usage, stop_details=stop_details)


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


def run(client, max_tokens=1024):
    return tools.chat_with_tools(client, "claude-haiku-5-5", "system", [{"role": "user", "content": "laundry?"}],
                                 max_tokens=max_tokens, tools=[])


class RequestOptionsTests(unittest.TestCase):
    def test_models_that_think_by_default_get_room_to_think(self):
        for model in ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"):
            _, max_tokens = tools.request_options({"effort": "high"}, model, 1024)
            self.assertEqual(max_tokens, 8000, model)

    def test_off_still_thinks_on_those_models(self):
        _, max_tokens = tools.request_options({"thinking": "off"}, "claude-haiku-5-5", 1024)
        self.assertEqual(max_tokens, 8000)

    def test_between_tools_and_older_models_keep_the_cap(self):
        self.assertEqual(tools.request_options({"thinking": "between_tools"}, "claude-sonnet-5-5", 1024)[1], 1024)
        self.assertEqual(tools.request_options({}, "claude-haiku-4-5", 1024)[1], 1024)

    def test_haiku_gets_no_fallback_parameter(self):
        opts, _ = tools.request_options({}, "claude-haiku-5-5", 1024)
        self.assertNotIn("extra_body", opts)


class LoopTests(unittest.TestCase):
    def test_thinking_only_cutoff_retries_bigger_instead_of_continue(self):
        client = FakeClient(
            response("max_tokens", block("thinking"), out=1024),
            response("end_turn", block("thinking"), block("text", "Run the dryer, then the dishes.")),
        )
        reply, _, _ = run(client)
        self.assertEqual(reply, "Run the dryer, then the dishes.")
        retry = client.requests[1]
        self.assertEqual(retry["max_tokens"], 2048)
        # Same conversation as the first request: no cut-off turn, no "Continue" message.
        self.assertEqual(retry["messages"], client.requests[0]["messages"])

    def test_retry_stops_at_the_ceiling(self):
        client = FakeClient(*[response("max_tokens", block("thinking"))] * 3,
                            response("end_turn", block("text", "ok")))
        run(client, max_tokens=9000)
        self.assertEqual([r["max_tokens"] for r in client.requests], [9000, 16000, 16000, 16000])

    def test_text_cutoff_still_continues(self):
        client = FakeClient(
            response("max_tokens", block("text", "First half, ")),
            response("end_turn", block("text", "second half.")),
        )
        reply, _, _ = run(client)
        self.assertEqual(reply, "First half, second half.")
        self.assertIn("Continue", client.requests[1]["messages"][-1]["content"])

    def test_refusal_says_what_happened(self):
        details = SimpleNamespace(category="general_harms", explanation="x")
        reply, _, _ = run(FakeClient(response("refusal", stop_details=details, out=0)))
        self.assertIn("safety filter", reply)
        self.assertIn("general_harms", reply)
        self.assertIn("rephras", reply)

    def test_refusal_keeps_text_already_written(self):
        reply, _, _ = run(FakeClient(response("refusal", block("text", "Partial answer."))))
        self.assertEqual(reply, "Partial answer.")


if __name__ == "__main__":
    unittest.main()
