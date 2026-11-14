import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from arcflash import config
from arcflash.llm import LLM, Completion, LLMError, Usage, is_runaway, strip_overlap
from tests.fakes import FakeClient, api_error, connection_error, cut_chunks, stream_chunks


class LLMTests(unittest.TestCase):
    def setUp(self):
        self.sleeps = []

    def make(self, items):
        self.client = FakeClient(items)
        return LLM(client=self.client, sleep=self.sleeps.append, usage=Usage())

    def test_streamed_text_and_usage_are_collected(self):
        c = self.make([stream_chunks("Hello", usage=(100, 60, 5))]).complete("plan", "SYS", "USER", max_tokens=123)
        self.assertEqual((c.text, c.finish_reason, c.input_tokens, c.cached_tokens, c.output_tokens), ("Hello", "stop", 100, 60, 5))

    def test_every_request_uses_flash_without_thinking(self):
        self.make([stream_chunks("{}")]).complete("plan", "SYS", "USER", max_tokens=123, json_mode=True)
        r = self.client.requests[0]
        self.assertEqual(r["model"], "deepseek-flash")
        self.assertEqual(r["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertEqual((r["stream"], r["max_tokens"], r["response_format"]), (True, 123, {"type": "json_object"}))
        self.assertEqual(r["messages"], [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}])

    def test_rejected_optional_field_is_dropped_for_the_run(self):
        llm = self.make([api_error(400, "unknown field: thinking"), stream_chunks("a"), stream_chunks("b")])
        llm.complete("x", None, "u", max_tokens=10); llm.complete("y", None, "u", max_tokens=10)
        self.assertIn("extra_body", self.client.requests[0])
        self.assertNotIn("extra_body", self.client.requests[1]); self.assertNotIn("extra_body", self.client.requests[2])
        self.assertEqual(self.sleeps, [])

    def test_rejected_max_tokens_is_renamed(self):
        llm = self.make([api_error(400, "max_tokens is not supported"), stream_chunks("a")])
        llm.complete("x", None, "u", max_tokens=10)
        self.assertNotIn("max_tokens", self.client.requests[1])
        self.assertEqual(self.client.requests[1]["max_completion_tokens"], 10)

    def test_platform_errors_wait_and_retry(self):
        c = self.make([api_error(503, "busy"), connection_error(), stream_chunks("ok")]).complete("x", None, "u", max_tokens=10)
        self.assertEqual((c.text, self.sleeps), ("ok", [15, 30]))

    def test_error_before_any_text_is_retried(self):
        def broken():
            raise connection_error()
            yield
        c = self.make([broken(), stream_chunks("whole")]).complete("x", None, "u", max_tokens=10)
        self.assertEqual((c.text, self.sleeps), ("whole", [15]))

    def test_error_after_text_keeps_the_text_and_continues(self):
        # v3 PrestaShop: 843 s of the plan phase are in no record, one attempt thrown away mid-answer and retried.
        def broken():
            yield from stream_chunks("line one\nline two", pieces=1)[:1]
            raise connection_error()   # an idle read timeout or "peer closed connection" mid-answer
        llm = self.make([broken(), stream_chunks("\nline three")])
        c = llm.complete_long("plan", None, "u", max_tokens=10)
        self.assertEqual((c.text, c.finish_reason, self.sleeps), ("line one\nline two\nline three", "stop", []))
        self.assertEqual([r["finish"] for r in llm.usage.records()], ["cut", "stop"])

    def test_gives_up_after_the_last_wait(self):
        llm = self.make([api_error(503, "busy")] * 5)
        with self.assertRaises(LLMError): llm.complete("x", None, "u", max_tokens=10)
        self.assertEqual((self.sleeps, len(self.client.requests)), ([15, 30, 60, 120], 5))

    def test_other_client_errors_fail_at_once(self):
        llm = self.make([api_error(400, "messages too long")])
        with self.assertRaises(LLMError): llm.complete("x", None, "u", max_tokens=10)
        self.assertEqual((len(self.client.requests), self.sleeps), (1, []))

    def test_blank_runaway_stops_the_stream(self):
        c = self.make([stream_chunks("{" + " " * 3000 + "}", pieces=60)]).complete("x", None, "u", max_tokens=32000)
        self.assertEqual(c.finish_reason, "runaway"); self.assertLess(len(c.text), 3002)

    def test_repeated_lines_are_a_runaway(self):
        self.assertTrue(is_runaway("start\n" + "same line\n" * 31))
        self.assertFalse(is_runaway("start\n" + "same line\n" * 10))
        self.assertFalse(is_runaway("\n" * 40))  # blank lines are counted by the blank rule, not this one
        self.assertTrue(is_runaway("x" + " " * config.RUNAWAY_BLANK_CHARS))

    def test_counting_sequence_is_a_runaway(self):
        # evidence.md C4: a Ctrip plan filled "notes" with 1 to 10000, one number per line, and hit the token limit 3 times.
        self.assertTrue(is_runaway('{"notes": [' + ", ".join(str(i) for i in range(1, 2000))))
        self.assertTrue(is_runaway('{"app": {"notes": [\n' + "".join(f"    {i},\n" for i in range(1, 400))))

    def test_a_normal_list_of_numbers_is_not(self):
        self.assertFalse(is_runaway('{"ids": [' + ", ".join(str(i) for i in range(1, 60)) + "]}"))
        self.assertFalse(is_runaway('{"ids": [' + ", ".join(str(i) for i in range(1, 2000)) + '], "next": "x"'))

    def test_runaway_is_retried_once_with_a_note(self):
        c = self.make([stream_chunks(" " * 2500, pieces=50), stream_chunks("fine")]).complete_long("x", None, "u", max_tokens=10)
        self.assertEqual(c.text, "fine")
        self.assertTrue(self.client.requests[1]["messages"][-1]["content"].endswith(config.RUNAWAY_PROMPT))

    def test_cut_answer_is_continued_without_overlap(self):
        llm = self.make([stream_chunks("line one\nline two is long", finish="length"), stream_chunks("line two is long\nline three")])
        c = llm.complete_long("plan", "S", "U", max_tokens=10)
        self.assertEqual(c.text, "line one\nline two is long\nline three")
        messages = self.client.requests[1]["messages"]
        self.assertEqual(messages[-2], {"role": "assistant", "content": "line one\nline two is long"})
        self.assertEqual(messages[-1], {"role": "user", "content": config.CONTINUE_PROMPT})
        self.assertEqual([r["purpose"] for r in llm.usage.records()], ["plan", "plan+1"])
        self.assertEqual(c.input_tokens, 200)

    def test_answer_cut_by_the_connection_is_continued(self):
        # v3 Ctrip: plan and frontend:main streams ended after 1,100 to 1,200 s with no finish reason and no usage;
        # the half answer was used as if complete (docs/5-arcflash-v4/plan.md R3).
        llm = self.make([cut_chunks("line one\nline two"), stream_chunks("\nline three")])
        c = llm.complete_long("plan", "S", "U", max_tokens=10)
        self.assertEqual((c.text, c.finish_reason), ("line one\nline two\nline three", "stop"))
        self.assertEqual([r["purpose"] for r in llm.usage.records()], ["plan", "plan+1"])
        self.assertEqual(llm.usage.records()[0]["finish"], "cut")

    def test_a_call_without_usage_is_estimated_from_characters(self):
        # Review point 5: v3's longest calls (plan, frontend:main) had no usage record and counted as 0 tokens.
        llm = self.make([cut_chunks("x" * 3600), stream_chunks("end")])
        llm.complete_long("plan", None, "u" * 7200, max_tokens=10)
        first = llm.usage.records()[0]
        self.assertEqual((first["input"], first["cached"], first["output"], first["estimated"]), (2000, 0, 1000, True))
        self.assertFalse(llm.usage.records()[1]["estimated"])

    def test_a_cut_answer_that_is_already_whole_is_not_continued(self):
        # Review point 7: a continuation of a whole JSON answer adds text, and json.loads then fails on "extra data".
        llm = self.make([cut_chunks('{"a": 1}'), stream_chunks("never sent")])
        c = llm.complete_long("plan", None, "u", max_tokens=10, done=lambda t: t.strip().endswith("}"))
        self.assertEqual((c.text, c.finish_reason, len(self.client.requests)), ('{"a": 1}', "stop", 1))

    def test_a_cut_stream_without_text_is_retried(self):
        c = self.make([cut_chunks(""), stream_chunks("ok")]).complete("x", None, "u", max_tokens=10)
        self.assertEqual((c.text, self.sleeps), ("ok", [15]))

    def test_usage_without_finish_reason_is_a_normal_end(self):
        chunks = [c for c in stream_chunks("done", finish=None)]
        c = self.make([chunks]).complete_long("x", None, "u", max_tokens=10)
        self.assertEqual((c.text, c.finish_reason, len(self.client.requests)), ("done", "stop", 1))

    def test_background_calls_use_their_own_two_slots(self):
        client = FakeClient([stream_chunks("x") for _ in range(10)], delay=0.15)
        llm = LLM(client=client, sleep=lambda s: None)
        background = [threading.Thread(target=llm.complete_long, args=(f"t{i}", None, "u"),
                                       kwargs={"max_tokens": 5, "background": True}) for i in range(6)]
        for t in background: t.start()
        for t in background: t.join()
        self.assertEqual(client.max_in_flight, 2)
        client.max_in_flight = 0
        both = [threading.Thread(target=llm.complete_long, args=(f"t{i}", None, "u"),
                                 kwargs={"max_tokens": 5, "background": i < 2}) for i in range(4)]
        for t in both: t.start()
        for t in both: t.join()
        self.assertEqual(client.max_in_flight, 4)   # two background calls never hold a foreground slot

    def test_continuation_stops_after_two(self):
        c = self.make([stream_chunks(f"part{i} ", finish="length") for i in range(3)]).complete_long("x", None, "u", max_tokens=10)
        self.assertEqual((c.finish_reason, len(self.client.requests)), ("length", 3))

    def test_strip_overlap(self):
        self.assertEqual(strip_overlap("abc 0123456789", "0123456789 next"), " next")
        self.assertEqual(strip_overlap("abc 012", "012 next"), "012 next")  # under 10 characters: kept

    def test_cost_uses_the_cached_price_and_platform_factor(self):
        u = Usage(); u.add("x", Completion("", "stop", input_tokens=1_000_000, cached_tokens=500_000, output_tokens=100_000))
        self.assertAlmostEqual(u.totals()["list_cny"], 0.91); self.assertAlmostEqual(u.totals()["billed_cny"], 1.82)

    def test_at_most_four_requests_in_flight(self):
        client = FakeClient([stream_chunks("x") for _ in range(8)], delay=0.05)
        llm = LLM(client=client, sleep=lambda s: None)
        threads = [threading.Thread(target=llm.complete, args=(f"c{i}", None, "u"), kwargs={"max_tokens": 5}) for i in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertLessEqual(client.max_in_flight, 4)
        self.assertGreater(client.max_in_flight, 1)

    def test_calls_are_logged_without_the_key(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-secret-123"}):
            LLM(client=FakeClient([stream_chunks("ok")]), log_dir=Path(d), sleep=lambda s: None).complete("plan", "S", "U", max_tokens=5)
            names = {p.name for p in Path(d).iterdir()}
            self.assertTrue({"001-plan.prompt.txt", "001-plan.response.txt", "calls.jsonl"} <= names)
            self.assertNotIn("sk-secret-123", "".join(p.read_text() for p in Path(d).iterdir()))

    def test_image_is_sent_and_logged_as_a_placeholder(self):
        with tempfile.TemporaryDirectory() as d:
            client = FakeClient([stream_chunks("Layout: x")])
            c = LLM(client=client, log_dir=Path(d), sleep=lambda s: None).describe_image("vision:a b.png", "Describe", b"\x89PNG1234", "image/png", max_tokens=50)
            self.assertEqual(c.text, "Layout: x")
            content = client.requests[0]["messages"][0]["content"]
            self.assertEqual(content[0], {"type": "text", "text": "Describe"})
            self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
            prompt = (Path(d) / "001-vision_a_b.png.prompt.txt").read_text()
            self.assertIn("[image image/png, 8 bytes]", prompt); self.assertNotIn("base64", prompt)

    def test_real_client_uses_the_environment_only(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "http://127.0.0.1:9/v1"}):
            client = LLM().client
        self.assertEqual(str(client.base_url).rstrip("/"), "http://127.0.0.1:9/v1")
        self.assertEqual(client.max_retries, 0)

    def test_missing_key_is_a_clear_error(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaisesRegex(LLMError, "OPENAI_API_KEY"): LLM()


if __name__ == "__main__":
    unittest.main()
