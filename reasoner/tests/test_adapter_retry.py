"""The adapter's time budget and its one retry (ADR-035 addendum, 2026-11-20). Offline: a fake
opener, clock and sleep; no provider is called and nothing waits."""

import email.utils
import json
import os
import unittest
import urllib.error

from nexus_reasoner import adapter, config

from .helpers import CFG

OK = {"choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": 1}}


class Resp:
    def __init__(self, doc):
        self.doc = doc

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.doc).encode()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Seq:
    """Answers each call with the next item: an exception is raised, a dict is the JSON body. Each
    call advances the fake clock by `cost` seconds."""

    def __init__(self, clock, *items, cost=2.0):
        self.clock, self.items, self.cost, self.timeouts = clock, list(items), cost, []

    def __call__(self, req, timeout=None):
        self.timeouts.append(timeout)
        self.clock.t += self.cost
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return Resp(item)


def http(code, retry_after=None):
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    return urllib.error.HTTPError("u", code, "x", headers, None)


class RetryTest(unittest.TestCase):
    def setUp(self):
        self.env = dict(os.environ)
        os.environ["T_GROQ_KEY"] = "gsk-test-secret"
        self.clock, self.slept = Clock(), []

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)

    def call(self, op, entry=None):
        def sleep(s):
            self.slept.append(s)
            self.clock.t += s

        return adapter.complete(
            CFG,
            entry or CFG.models["R3"],
            "S",
            "U",
            opener=op,
            clock=self.clock,
            sleep=sleep,
        )

    def test_a_429_with_retry_after_is_retried_once_within_the_budget(self):
        op = Seq(self.clock, http(429, "3"), OK)
        c = self.call(op)
        self.assertEqual(self.slept, [3.0])
        self.assertEqual(c.settings["attempts"], 2)
        # first attempt gets the whole 30 s; the second what is left: 30 - 2 (call) - 3 (wait)
        self.assertEqual(op.timeouts, [30.0, 25.0])

    def test_a_retry_after_that_leaves_too_little_is_not_waited_for(self):
        op = Seq(self.clock, http(429, "24"), OK)  # 30 - 2 - 24 = 4 < MIN_ATTEMPT_S
        with self.assertRaises(adapter.AdapterError) as cm:
            self.call(op)
        self.assertEqual(
            (cm.exception.reason, cm.exception.retry_after), ("http_429", 24.0)
        )
        self.assertEqual((self.slept, len(op.timeouts)), ([], 1))

    def test_a_5xx_and_a_network_error_wait_the_gap(self):
        for first in (http(502), http(504), urllib.error.URLError("reset")):
            self.slept = []
            op = Seq(self.clock, first, OK)
            self.assertEqual(self.call(op).settings["attempts"], 2)
            self.assertEqual(self.slept, [adapter.RETRY_GAP_S])

    def test_one_retry_only(self):
        op = Seq(self.clock, http(503), http(503), OK)
        with self.assertRaises(adapter.AdapterError) as cm:
            self.call(op)
        self.assertEqual((cm.exception.reason, len(op.timeouts)), ("http_503", 2))

    def test_no_retry_for_what_a_retry_cannot_fix(self):
        for first in (http(400), http(401), http(413), http(429), TimeoutError()):
            op = Seq(self.clock, first, OK)
            with self.assertRaises(adapter.AdapterError):
                self.call(op)
            self.assertEqual(len(op.timeouts), 1, repr(first))
        bad = Seq(self.clock, {"unexpected": 1}, OK)
        with self.assertRaises(adapter.AdapterError) as cm:
            self.call(bad)
        self.assertEqual((cm.exception.reason, len(bad.timeouts)), ("bad_response", 1))

    def test_a_slow_first_attempt_leaves_no_retry(self):
        op = Seq(self.clock, http(503), OK, cost=26.0)  # 30 - 26 - 1 = 3 < 5
        with self.assertRaises(adapter.AdapterError):
            self.call(op)
        self.assertEqual(len(op.timeouts), 1)

    def test_the_models_own_timeout_is_the_budget(self):
        cfg = config.parse(
            {"indexPath": "x", "scoreFloor": 1.0, "models": {
                "slow": {"provider": "groq", "model": "m", "baseUrl": "https://g.test/v1",
                         "apiKeyEnv": "T_GROQ_KEY", "timeoutSeconds": 120}}}
        )  # fmt: skip
        op = Seq(self.clock, OK)
        c = self.call(op, cfg.models["slow"])
        self.assertEqual((op.timeouts, c.settings["timeout_s"]), ([120.0], 120.0))


class RetryAfterTest(unittest.TestCase):
    def test_seconds_and_dates(self):
        self.assertEqual(adapter.retry_after("2"), 2.0)
        self.assertEqual(adapter.retry_after(" 0.5 "), 0.5)
        self.assertEqual(adapter.retry_after("-3"), 0.0)
        date = email.utils.formatdate(1_000_010, usegmt=True)
        self.assertEqual(adapter.retry_after(date, now=1_000_000), 10.0)
        for junk in (None, "", "soon", "nan", "inf"):
            self.assertIsNone(adapter.retry_after(junk), junk)


class ConfigTimeoutTest(unittest.TestCase):
    def parse(self, top=None, model=None):
        m = {"provider": "groq", "model": "m", "baseUrl": "u", "apiKeyEnv": "K"}
        if model is not None:
            m["timeoutSeconds"] = model
        doc = {"indexPath": "x", "scoreFloor": 1.0, "models": {"R3": m}}
        if top is not None:
            doc["timeoutSeconds"] = top
        return config.parse(doc)

    def test_default_override_and_bounds(self):
        self.assertEqual(self.parse().models["R3"].timeout_s, 30.0)
        self.assertEqual(self.parse(top=60).models["R3"].timeout_s, 60.0)
        self.assertEqual(self.parse(top=60, model=180).models["R3"].timeout_s, 180.0)
        for bad in (0, -1, 181, True, "30"):
            with self.assertRaises(config.ConfigError, msg=repr(bad)):
                self.parse(model=bad)
            with self.assertRaises(config.ConfigError, msg=repr(bad)):
                self.parse(top=bad)


if __name__ == "__main__":
    unittest.main()
