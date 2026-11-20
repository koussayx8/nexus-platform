"""R3 provider switch by config (owner, 2026-11-20): named model configs, `active` picks the one that
serves R3, NVIDIA NIM as an OpenAI-compatible provider. Offline: fake openers, fake keys."""

import json
import os
import unittest

from nexus_reasoner import adapter, config, service

from .helpers import FakeOpener, bundle, good_proposal, retriever

NIM = "https://integrate.api.nvidia.com/v1"


def doc(active="r3-groq", **extra):
    d = {
        "indexPath": "x",
        "scoreFloor": 1.0,
        "models": {
            "R2": {"provider": "groq", "model": "openai/gpt-oss-20b", "baseUrl": "https://g.test/openai/v1", "apiKeyEnv": "T_GROQ_KEY"},
            "r3-groq": {"provider": "groq", "model": "openai/gpt-oss-120b", "baseUrl": "https://g.test/openai/v1", "apiKeyEnv": "T_GROQ_KEY"},
            "r3-primary": {"provider": "nvidia", "model": "vendor/thinker-1", "baseUrl": NIM, "apiKeyEnv": "T_NIM_KEY", "timeoutSeconds": 120, "maxOutputTokens": 8192},
            "r3-backup-1": {"provider": "nvidia", "model": "", "baseUrl": NIM, "apiKeyEnv": "T_NIM_KEY"},
        },
        "active": {"R3": active},
    }  # fmt: skip
    d.update(extra)
    return d


def nim_reply(content, reasoning=None):
    def reply(url, body):
        msg = {"content": content}
        if reasoning is not None:
            msg["reasoning_content"] = reasoning
        return {"choices": [{"message": msg}], "usage": {"prompt_tokens": 9}}

    return reply


class ConfigTest(unittest.TestCase):
    def test_active_resolves_the_arm_and_old_ids_still_resolve(self):
        cfg = config.parse(doc())
        self.assertEqual(cfg.resolve("R3").name, "r3-groq")
        self.assertEqual(cfg.resolve("r3-primary").model, "vendor/thinker-1")
        self.assertEqual(cfg.resolve("openai/gpt-oss-120b").name, "r3-groq")
        self.assertEqual(cfg.resolve("R2").name, "R2")
        self.assertIsNone(cfg.resolve("nope"))
        self.assertIsNone(cfg.resolve(""))  # an empty entry is never found by its id
        self.assertEqual(
            config.parse(doc(active="r3-primary")).resolve("R3").name, "r3-primary"
        )

    def test_active_must_name_a_filled_in_config(self):
        for bad in ("r3-backup-1", "r3-missing"):
            with self.assertRaises(config.ConfigError, msg=bad):
                config.parse(doc(active=bad))
        d = doc()
        d["active"] = {
            "r3-groq": "r3-primary"
        }  # an arm name may not shadow a config name
        with self.assertRaises(config.ConfigError):
            config.parse(d)
        d["active"] = ["R3"]
        with self.assertRaises(config.ConfigError):
            config.parse(d)

    def test_caps_and_response_formats(self):
        cfg = config.parse(doc())
        self.assertEqual(cfg.models["r3-primary"].max_output_tokens, 8192)
        self.assertEqual(
            cfg.models["r3-groq"].max_output_tokens, 1024
        )  # the file default
        self.assertEqual(cfg.models["r3-primary"].response_format, "none")
        self.assertEqual(cfg.models["r3-groq"].response_format, "json_schema")
        for key, bad in (("maxOutputTokens", 0), ("maxOutputTokens", 40000), ("maxOutputTokens", True),
                         ("responseFormat", "xml")):  # fmt: skip
            d = doc()
            d["models"]["r3-primary"][key] = bad
            with self.assertRaises(config.ConfigError, msg=f"{key}={bad!r}"):
                config.parse(d)


class NimAdapterTest(unittest.TestCase):
    def setUp(self):
        self.env = dict(os.environ)
        os.environ["T_NIM_KEY"] = "nvapi-test-secret"
        os.environ["T_GROQ_KEY"] = "gsk-test-secret"
        self.cfg = config.parse(doc())

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)

    def test_nim_request_has_no_groq_options_and_keeps_the_trace_apart(self):
        op = FakeOpener(nim_reply('{"a": 1}', reasoning="I think..."))
        c = adapter.complete(
            self.cfg, self.cfg.models["r3-primary"], "S", "U", opener=op
        )
        call = op.calls[0]
        self.assertEqual(call["url"], NIM + "/chat/completions")
        self.assertEqual(call["headers"]["Authorization"], "Bearer nvapi-test-secret")
        body = call["body"]
        self.assertEqual(
            (body["model"], body["max_tokens"], body["temperature"]),
            ("vendor/thinker-1", 8192, 0),
        )
        for groq_only in (
            "include_reasoning",
            "reasoning_effort",
            "max_completion_tokens",
            "response_format",
        ):
            self.assertNotIn(groq_only, body)
        self.assertEqual((c.text, c.reasoning), ('{"a": 1}', "I think..."))
        self.assertEqual(c.settings["max_output_tokens"], 8192)
        self.assertEqual(call["timeout"], 120.0)

    def test_nim_json_object_when_configured(self):
        d = doc()
        d["models"]["r3-primary"]["responseFormat"] = "json_object"
        cfg = config.parse(d)
        op = FakeOpener(nim_reply("{}"))
        adapter.complete(cfg, cfg.models["r3-primary"], "S", "U", opener=op)
        self.assertEqual(
            op.calls[0]["body"]["response_format"], {"type": "json_object"}
        )

    def test_groq_request_is_unchanged(self):
        op = FakeOpener(nim_reply("{}"))
        c = adapter.complete(self.cfg, self.cfg.models["r3-groq"], "S", "U", opener=op)
        body = op.calls[0]["body"]
        self.assertIs(body["include_reasoning"], False)
        self.assertEqual(
            (body["max_completion_tokens"], body["reasoning_effort"]), (1024, "low")
        )
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertNotIn("max_tokens", body)
        self.assertIsNone(c.reasoning)

    def test_an_empty_config_is_not_configured_and_no_key_is_none(self):
        with self.assertRaises(adapter.AdapterError) as cm:
            adapter.complete(
                self.cfg, self.cfg.models["r3-backup-1"], "S", "U", opener=FakeOpener()
            )
        self.assertEqual(cm.exception.reason, "not_configured")
        os.environ.pop("T_NIM_KEY")
        with self.assertRaises(adapter.AdapterError) as cm:
            adapter.complete(
                self.cfg, self.cfg.models["r3-primary"], "S", "U", opener=FakeOpener()
            )
        self.assertEqual(cm.exception.reason, "no_credentials")

    def test_a_non_string_trace_is_dropped(self):
        op = FakeOpener(
            lambda u, b: {
                "choices": [{"message": {"content": "{}", "reasoning_content": 7}}]
            }
        )
        c = adapter.complete(
            self.cfg, self.cfg.models["r3-primary"], "S", "U", opener=op
        )
        self.assertIsNone(c.reasoning)


class ServiceSwitchTest(unittest.TestCase):
    def setUp(self):
        self.env = dict(os.environ)
        os.environ["T_GROQ_KEY"] = "gsk-test-secret"
        os.environ["T_NIM_KEY"] = "nvapi-test-secret"
        self.b = bundle()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)

    def rs(self, active, opener):
        return service.Reasoner(config.parse(doc(active=active)), retriever(), opener)

    def call(self, rs):
        ctx = {
            "incidentUid": "u-1",
            "level": "L1",
            "catalogueVersion": "1",
            "mode": "llm",
            "model": "R3",
        }
        return rs.propose({"bundle": self.b, "context": ctx})

    def test_every_call_records_the_config_and_model_used(self):
        answer = json.dumps(good_proposal(self.b))
        for active, model in (
            ("r3-groq", "openai/gpt-oss-120b"),
            ("r3-primary", "vendor/thinker-1"),
        ):
            m = self.call(self.rs(active, FakeOpener(nim_reply(answer))))["metadata"]
            self.assertEqual(
                (m["arm"], m["modelConfig"], m["model"]), ("R3", active, model)
            )

    def test_a_failed_call_records_its_config_too(self):
        os.environ.pop("T_NIM_KEY")
        with self.assertRaises(service.Rejected) as cm:
            self.call(self.rs("r3-primary", FakeOpener()))
        self.assertEqual(cm.exception.status, 503)
        self.assertEqual(cm.exception.extra["metadata"]["modelConfig"], "r3-primary")

    def test_healthz_shows_the_active_configs(self):
        h = self.rs("r3-primary", FakeOpener()).health()
        self.assertEqual(
            h["active"],
            {"R3": {"modelConfig": "r3-primary", "model": "vendor/thinker-1"}},
        )


if __name__ == "__main__":
    unittest.main()
