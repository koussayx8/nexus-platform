"""The display changes of 2026-11-22: trusted links, buttons on result pushes and Explain, the 🧠
Reasoning view, the paged /incident list, the /pods table, the Kill Switch and level pushes, and
the /status buttons. Every one is read-only: no test here may see a patch."""

from datetime import timedelta
from typing import Any

import pytest
from nexus_client.errors import Unreachable
from nexus_client.models import Incident

from nexus_bot import commands, views
from nexus_bot.config import ConfigError, parse
from nexus_bot.kube import Deploy, Pod
from nexus_bot.main import Bot
from nexus_bot.payload import VIEW, View, decode_view
from nexus_bot.push import Pusher
from nexus_bot.richtext import MAX_TEXT, RichText, units
from nexus_bot.telegram import MAX_CALLBACK_DATA, TelegramClient, TelegramError
from nexus_bot.views import Links
from tests.fakes import NOW, OWNER_ID, FakeCluster, FakeTransport, incident_dict, message, press
from tests.helpers import covered, quotes_hold_untrusted
from tests.test_messages import UID, escalated, make, many, resolved, sample

TOKEN = "123456:" + "A" * 35
REPO = "https://github.com/koussayx8/nexus-platform"
SHA = "0123456789abcdef0123456789abcdef01234567"
KEY = UID.replace("-", "")[:16]
NS = ("nexus-dev", "nexus-prod")


def inc(d: dict[str, Any]) -> Incident:
    return Incident.model_validate(d)


def links_of(rt: RichText) -> list[tuple[str, str]]:
    msg = rt.build()
    return [(covered(msg.text, e), e["url"]) for e in msg.entities if e["type"] == "text_link"]


# -- 1. trusted links ------------------------------------------------------------------------------

def test_repo_url_and_ref_defaults_and_validation() -> None:
    cfg = parse({"allowlist": {}}, TOKEN)
    assert (cfg.repo_url, cfg.repo_ref) == (REPO, "main")
    assert parse({"allowlist": {}, "repoRef": "v0.6.0"}, TOKEN).repo_ref == "v0.6.0"
    for bad in ({"repoUrl": "http://github.com/a/b"}, {"repoUrl": "https://evil.example/a/b"},
                {"repoUrl": "https://github.com/a/b/../c"}, {"repoRef": "../x"}, {"repoRef": "a b"},
                {"repoRef": ""}, {"repoRef": 7}):
        with pytest.raises(ConfigError):
            parse({"allowlist": {}} | bad, TOKEN)


def test_prompt_and_runbook_links_point_at_the_ref() -> None:
    d = sample()
    d["status"]["reasoner"]["chunks"] = ["rb-cpu-saturation#2", "rb-bad-rollout#13", "scale#1",
                                         "rb-../../etc#1", "RB-x#1", "rb-dns#x"]
    got = links_of(views.explain(inc(d), Links(REPO, "v0.6.0")))
    assert got == [("rb-cpu-saturation#2", f"{REPO}/blob/v0.6.0/runbooks/rb-cpu-saturation.md"),
                   ("rb-bad-rollout#13", f"{REPO}/blob/v0.6.0/runbooks/rb-bad-rollout.md"),
                   ("prompt/v3", f"{REPO}/blob/v0.6.0/reasoner/nexus_reasoner/prompt.py")]
    assert "rb-../../etc#1, RB-x#1, rb-dns#x" in views.explain(inc(d)).build().text


def test_an_unknown_prompt_version_stays_plain() -> None:
    d = sample()
    d["status"]["reasoner"]["promptVersion"] = "prompt/v3; see https://evil.example"
    assert links_of(views.explain(inc(d))) == [("rb-cpu-saturation#2",
                                                 f"{REPO}/blob/main/runbooks/rb-cpu-saturation.md")]


def test_a_commit_sha_gets_a_trusted_line_and_the_model_text_stays_plain() -> None:
    rt = views.explain(inc(escalated()))
    msg = rt.build()
    assert links_of(rt)[-1] == ("0123456", f"{REPO}/commit/{SHA}")
    line = f"Commit named by the model (not checked): {SHA[:7]}"
    assert line in msg.text
    # the SHA in the suggestion is still there in full, inside the quote, with no entity of its own
    assert f"Suggested change (never applied): Revert {SHA}, then" in msg.text
    quotes_hold_untrusted(msg)
    start = units(msg.text[:msg.text.index(line)])
    assert not any(q["offset"] <= start < q["offset"] + q["length"] for q in msg.entities if "blockquote" in q["type"])


@pytest.mark.parametrize("text", [SHA.upper(), SHA[:39], "x" + SHA + "f", "sha256:" + "ab" * 32, SHA + "ab"])
def test_only_an_exact_40_hex_token_counts(text: str) -> None:
    d = escalated()
    d["status"]["proposal"]["escalation"]["suggestedChange"] = f"revert {text} now"
    assert views.commit_shas(inc(d)) == []


def test_shas_from_every_field_distinct_and_at_most_three() -> None:
    d = escalated()
    p = d["status"]["proposal"]
    p["hypothesis"] = f"bad rollout {SHA}"
    p["parameters"] = {"commit": "a" * 40, "nested": {"x": ["b" * 40]}}
    p["escalation"]["runbookGap"] = "c" * 40
    assert views.commit_shas(inc(d)) == [("Hypothesis", SHA), ("Parameters", "a" * 40), ("Parameters", "b" * 40)]


def test_a_link_url_is_only_ever_the_bot_s() -> None:
    with pytest.raises(ValueError):
        RichText().link("x", "https://evil.example/x")
    with pytest.raises(ValueError):
        RichText().link("x", "javascript:alert(1)")


# -- 2. buttons ------------------------------------------------------------------------------------

def every_button() -> list[tuple[str, str]]:
    out = []
    for d in (resolved(), escalated(), sample("Blocked")):
        out += [b for row in views.incident_rows(inc(d), NS, explain=True) for b in row]
    out += [b for row in commands.STATUS_ROWS for b in row]
    for r in (commands.incidents(FakeCluster(*many()), NS, NOW, "a", 1), commands.pods(FakeCluster(), NS, ["nexus-dev"])):
        out += [b for row in r.rows for b in row]
    return out


def test_every_new_button_is_a_view_payload_within_64_bytes() -> None:
    for label, data in every_button():
        assert len(data.encode()) <= MAX_CALLBACK_DATA, label
        assert VIEW.fullmatch(data) or data.startswith("e:"), (label, data)


def test_result_push_buttons_where_they_apply() -> None:
    labels = [[b[0] for b in row] for row in views.incident_rows(inc(escalated()), NS, explain=True)]
    assert labels == [["Explain", "🧠 Reasoning"], ["📦 Pods", "📚 Runbooks", "🔗 Commit"]]
    bare = incident_dict(phase="Resolved")
    bare["spec"]["target"]["namespace"] = "nexus-system"
    assert [[b[0] for b in row] for row in views.incident_rows(inc(bare), NS, explain=True)] == [["Explain"]]


def test_never_on_awaiting_approval() -> None:
    with pytest.raises(ValueError):
        views.incident_rows(inc(sample()), NS, explain=True)


def test_an_explain_press_answers_with_the_explain_view_and_its_buttons() -> None:
    from nexus_bot.payload import encode

    bot, c, t = make(resolved())
    data = encode("e", inc(resolved()))
    assert data
    bot.handle(press(data))
    sent = t.sent()[-1]
    rows = [[b["text"] for b in row] for row in sent["reply_markup"]["inline_keyboard"]]
    assert rows == [["🧠 Reasoning"], ["📦 Pods", "📚 Runbooks"]] and c.patches == []
    assert sent["text"].startswith("🟢 Resolved\nIncident: inc-669532275a7b73d2-1795292289")


@pytest.mark.parametrize("data_", ["m:" + KEY, "b:" + KEY, "c:" + KEY, "l:a:0", "t:nexus-dev", "k:nexus-dev",
                                   "v:pods", "v:inc"])
@pytest.mark.parametrize("who", ["stranger", "group"])
def test_new_buttons_are_dropped_for_anyone_else(data_: str, who: str) -> None:
    bot, c, t = make(escalated())
    bot.handle(press(data_, user=999) if who == "stranger" else press(data_, chat=-100123, chat_type="group"))
    assert t.calls == [] and c.patches == []


@pytest.mark.parametrize("data_", ["m:" + KEY[:15], "l:x:0", "l:a:100", "l:a:-1", "v:all", "t:kube-system",
                                   "c:" + KEY + "0", "b:", "l:a:"])
def test_malformed_new_payloads_are_refused(data_: str) -> None:
    assert decode_view(data_) is None


def test_decode_view_of_each_kind() -> None:
    assert decode_view("l:o:12") == View("list", "o", 12)
    assert decode_view("v:inc") == View("pick", "inc")
    assert decode_view("k:nexus-prod") == View("cards", "nexus-prod")
    assert decode_view("m:" + KEY) == View("reasoning", KEY)


# -- 3. the 🧠 Reasoning view -----------------------------------------------------------------------

def test_the_reasoning_view() -> None:
    [rt] = views.reasoning(inc(resolved()))
    msg = rt.build()
    quote = next(e for e in msg.entities if e["type"] == "expandable_blockquote")
    assert covered(msg.text, quote).startswith("Model text: untrusted, never a decision\n⚠️ Throttling rose")
    assert msg.text.endswith("8412 characters in total; the full text is in the Flight Recorder")
    quotes_hold_untrusted(msg)


@pytest.mark.parametrize("unit", ["word ", "😀", "https://evil.example/a "])
def test_a_long_excerpt_is_split_under_the_cap(unit: str) -> None:
    d = resolved()
    d["status"]["reasoner"]["reasoningExcerpt"] = unit * 3000
    parts = views.reasoning(inc(d))
    assert len(parts) >= 2
    built = [p.build() for p in parts]
    for i, msg in enumerate(built, start=1):
        assert units(msg.text) <= MAX_TEXT and "(truncated:" not in msg.text
        assert msg.text.startswith(f"🧠 Reasoning ({i} of {len(parts)})\n")
        assert "evil.example" not in msg.text and "https://" not in msg.text
        quotes_hold_untrusted(msg)
    assert [m.text.endswith("the full text is in the Flight Recorder") for m in built] == \
        [False] * (len(built) - 1) + [True]
    pieces = [covered(m.text, next(e for e in m.entities if e["type"] == "expandable_blockquote"))
              .split("\n")[1] for m in built]
    from nexusctl.render.sanitize import sanitize

    from nexus_bot.richtext import defang

    assert "".join(pieces).replace(" ", "") == defang(sanitize(unit * 3000, 10**6)).replace(" ", "")


def test_a_split_reasoning_view_is_sent_in_order_with_the_buttons_last() -> None:
    d = resolved()
    d["status"]["reasoner"]["reasoningExcerpt"] = "thought " * 600
    bot, _c, t = make(d)
    bot.handle(press("m:" + KEY))
    sent = t.sent()
    assert len(sent) == 2 and sent[0]["text"].startswith("🧠 Reasoning (1 of 2)")
    assert "reply_markup" not in sent[0] and sent[1]["reply_markup"]["inline_keyboard"] == \
        [[{"text": "Explain", "callback_data": "i:" + KEY}]]


def test_split_units_counts_utf16_and_never_splits_a_pair() -> None:
    pieces = views.split_units("😀" * 10, 5)
    assert pieces == ["😀😀", "😀😀", "😀😀", "😀😀", "😀😀"]
    assert views.split_units("", 5) == [""]


def test_no_excerpt() -> None:
    d = resolved()
    d["status"]["reasoner"].pop("reasoningExcerpt")
    assert views.reasoning(inc(d))[0].build().text.endswith("No reasoning excerpt recorded.")


# -- 4. /incident list -----------------------------------------------------------------------------

def test_pages_of_five_and_the_open_filter() -> None:
    c = FakeCluster(*many())
    first = commands.incidents(c, NS, NOW)
    assert first.message.build().text.startswith("🗂 Incidents: All\n7 Incidents · page 1 of 2 · as of 10:00 UTC")
    second = commands.incidents(c, NS, NOW, "a", 1, edit=True)
    assert [r[0][1] for r in second.rows[:2]] == ["i:" + "2" * 16, "i:" + "1" * 16]
    assert second.rows[2] == [("◀ Newer", "l:a:0")] and second.edit
    open_ = commands.incidents(c, NS, NOW, "o").message.build().text
    assert "5 Incidents · page 1 of 1" in open_ and "Resolved" not in open_
    assert "page 2 of 2" in commands.incidents(c, NS, NOW, "a", 99).message.build().text  # clamped


def test_filters_follow_the_configured_namespaces() -> None:
    r = commands.incidents(FakeCluster(*many()), ("nexus-dev",), NOW)
    assert [b[0] for b in r.rows[-1]] == ["✓ All", "Open", "nexus-dev"]
    assert commands.incidents(FakeCluster(), NS, NOW).message.build().text.endswith("No Incidents")


def test_paging_edits_the_message_in_place() -> None:
    bot, c, t = make(*many())
    bot.handle(press("l:a:1"))
    [(m1, _), (m2, edit)] = t.calls
    assert (m1, m2) == ("answerCallbackQuery", "editMessageText")
    assert edit["message_id"] == 77 and edit["text"].startswith("🗂 Incidents: All\n7 Incidents · page 2 of 2")
    assert edit["link_preview_options"] == {"is_disabled": True} and "parse_mode" not in edit
    assert c.patches == []


def test_an_edit_telegram_refuses_falls_back_to_a_new_message() -> None:
    bot, _c, t = make(*many())
    t.fail["editMessageText"] = TelegramError("editMessageText: HTTP 400: Bad Request: message to edit not found")
    bot.handle(press("l:o:0"))
    assert [m for m, _ in t.calls] == ["answerCallbackQuery", "editMessageText", "sendMessage"]
    t.calls.clear()
    t.fail["editMessageText"] = TelegramError("editMessageText: HTTP 400: Bad Request: message is not modified")
    bot.handle(press("l:o:0"))
    assert [m for m, _ in t.calls] == ["answerCallbackQuery", "editMessageText"]


# -- 5. /pods table --------------------------------------------------------------------------------

class Workloads(FakeCluster):
    def deployments(self, namespace: str) -> list[Deploy]:
        return [Deploy("sample-api", 3, 3, 3, 3, "2026-11-01T00:00:00Z"),
                Deploy("sample-api-canary-with-a-very-long-name", 0, 1, 1, 0, "2026-11-28T09:00:00Z")]

    def pods(self, namespace: str) -> list[Pod]:
        return [Pod("sample-api-7d9f8b6c5d-x2k4q", "Running", 0, True, 2, 2, "Running", "2026-11-27T10:00:00Z"),
                Pod("sample-api-canary-with-a-very-long-name-5c6d7e8f9a-zz9zz", "Running", 17, False, 1, 0,
                    "CrashLoopBackOff", "2026-11-28T09:00:00Z"),
                Pod("debug-shell", "Succeeded", 0, False, 1, 0, "Completed", None)]


def test_the_table_fits_a_phone() -> None:
    text = commands.pods_table(Workloads().deployments("x"), Workloads().pods("x"), NOW)
    lines = text.split("\n")
    assert all(len(line) <= 36 for line in lines), [line for line in lines if len(line) > 36]
    assert lines[:3] == ["sample-api", "READY UP-TO-DATE AVAILABLE AGE", "3/3   3          3         27d"]
    assert lines[3].split() == ["NAME", "READY", "STATUS", "RESTARTS", "AGE"]
    assert lines[4].split() == ["…5d-x2k4q", "2/2", "Running", "0", "1d"]  # the tail is the pod's own part
    canary = text.split("\n\n")[1].split("\n")
    assert canary[0] == "sample-api-canary-with-a-very-long-…"
    assert canary[4].startswith("…") and " CrashLo… " in canary[4] and canary[4].endswith(" 17       1h")
    cards = commands.pods(Workloads(), NS, ["nexus-dev"], NOW, cards=True).message.build().text
    assert ": CrashLoopBackOff, not ready, 17 restarts" in cards  # Cards keeps the whole reason
    orphans = text.split("\n\n")[2].split("\n")
    assert orphans[0] == "pods of no Deployment" and orphans[2].split() == ["…g-shell", "0/1", "Complet…", "0", "-"]


def test_table_is_the_default_with_a_cards_toggle_and_refresh() -> None:
    r = commands.pods(Workloads(), NS, ["nexus-dev"], NOW)
    msg = r.message.build()
    [pre] = [e for e in msg.entities if e["type"] == "pre"]
    assert covered(msg.text, pre).startswith("sample-api\nREADY UP-TO-DATE AVAILABLE AGE")
    assert r.rows == [[("🃏 Cards", "k:nexus-dev"), ("🔄 Refresh", "t:nexus-dev")]] and not r.edit
    cards = commands.view(View("cards", "nexus-dev"), Workloads(), NS, lambda: NOW)
    assert cards.rows == [[("📋 Table", "t:nexus-dev"), ("🔄 Refresh", "k:nexus-dev")]] and cards.edit
    assert not any(e["type"] == "pre" for e in cards.message.build().entities)


def test_a_cluster_value_in_the_table_is_sanitized() -> None:
    class Odd(FakeCluster):
        def pods(self, namespace: str) -> list[Pod]:
            return [Pod("x\x1b[2J‮-pod", "Running", 0, True, 1, 1, "Run\nning", None)]

    text = commands.pods(Odd(), NS, ["nexus-dev"], NOW).message.build().text
    assert "\x1b" not in text and "‮" not in text and "Run ning" in text


def test_refresh_edits_and_a_pods_button_on_a_push_sends_new() -> None:
    bot, _c, t = make()
    bot.handle(press("t:nexus-dev"))
    bot.handle(press("p:nexus-dev", update_id=3))
    assert [m for m, _ in t.calls] == ["answerCallbackQuery", "editMessageText", "answerCallbackQuery", "sendMessage"]


def test_the_reader_fills_the_kubectl_columns() -> None:
    from datetime import UTC, datetime

    from kubernetes.client import (
        V1Container,
        V1ContainerState,
        V1ContainerStateWaiting,
        V1ContainerStatus,
        V1ObjectMeta,
        V1Pod,
        V1PodSpec,
        V1PodStatus,
    )

    from nexus_bot.kube import _pod_status

    created = datetime(2026, 11, 28, 9, 0, tzinfo=UTC)
    crash = V1ContainerStatus(name="app", image="i", image_id="", ready=False, restart_count=3,
                              state=V1ContainerState(waiting=V1ContainerStateWaiting(reason="CrashLoopBackOff")))
    pod = V1Pod(metadata=V1ObjectMeta(name="p", creation_timestamp=created),
                spec=V1PodSpec(containers=[V1Container(name="app")]),
                status=V1PodStatus(phase="Running", container_statuses=[crash]))
    assert _pod_status(pod) == "CrashLoopBackOff"
    meta: Any = pod.metadata
    meta.deletion_timestamp = created
    assert _pod_status(pod) == "Terminating"


# -- 6. Kill Switch and level pushes ---------------------------------------------------------------

def pusher(c: FakeCluster, t: FakeTransport, chats: list[int] | None = None) -> Pusher:
    return Pusher(c, TelegramClient(t), chats or [OWNER_ID], now=lambda: NOW, catch_up=timedelta(minutes=15))


def test_no_push_for_the_state_found_at_start() -> None:
    c, t = FakeCluster(), FakeTransport()
    c.state, c.levels["nexus-dev"] = "halted", "3"
    p = pusher(c, t)
    assert p.tick() == 0 and p.tick() == 0 and t.sent() == []


def test_the_kill_switch_halted_then_active() -> None:
    c, t = FakeCluster(), FakeTransport()
    p = pusher(c, t)
    p.tick()
    c.state = "halted"
    assert p.tick() == 1 and p.tick() == 0
    c.state = "active"
    assert p.tick() == 1
    assert [m["text"].split("\n")[0] for m in t.sent()] == ["🛑 Kill Switch halted", "🟢 Kill Switch active"]
    assert "Was: halted\nNow: active" in t.sent()[1]["text"]


def test_a_missing_or_odd_state_is_halted() -> None:
    c, t = FakeCluster(), FakeTransport()
    p = pusher(c, t)
    p.tick()
    c.state = None
    p.tick()
    assert t.sent()[-1]["text"].startswith("🛑 Kill Switch halted\nWas: active\nNow: missing")


def test_a_level_change_per_namespace() -> None:
    c, t = FakeCluster(), FakeTransport()
    c.levels["nexus-dev"] = "3"
    p = pusher(c, t)
    p.tick()
    c.levels["nexus-dev"] = "1"
    c.levels.pop("nexus-prod")
    assert p.tick() == 2
    texts = [m["text"] for m in t.sent()]
    assert "Namespace: nexus-dev\nLevel: L3 → L1" in texts[0] and "Namespace: nexus-prod\nLevel: L1 → unset" in texts[1]


def test_a_failed_read_changes_nothing_and_a_failed_send_is_retried() -> None:
    class Flaky(FakeCluster):
        down = False

        def killswitch(self) -> str | None:
            if self.down:
                raise Unreachable("down")
            return self.state

    c, t = Flaky(), FakeTransport()
    p = pusher(c, t, [1, 2])
    p.tick()
    c.down = True
    assert p.tick() == 0
    c.down, c.state = False, "halted"
    t.fail["sendMessage"] = TelegramError("sendMessage: timed out")
    assert p.tick() == 0
    del t.fail["sendMessage"]
    t.calls.clear()
    assert p.tick() == 2 and p.tick() == 0
    assert sorted(m["chat_id"] for m in t.sent()) == [1, 2]


def test_state_pushes_go_on_when_incidents_cannot_be_listed() -> None:
    c, t = FakeCluster(), FakeTransport()
    p = pusher(c, t)
    p.tick()
    c.fail_list, c.state = Unreachable("down"), "halted"
    assert p.tick() == 1


# -- 7. /status buttons ----------------------------------------------------------------------------

def test_status_offers_pods_and_incidents() -> None:
    bot, c, t = make(sample())
    bot.handle(message("/status"))
    rows = t.sent()[-1]["reply_markup"]["inline_keyboard"]
    assert rows == [[{"text": "📦 Pods", "callback_data": "v:pods"},
                     {"text": "🗂 Incidents", "callback_data": "v:inc"}]]
    bot.handle(press("v:pods", update_id=3))
    assert t.sent()[-1]["text"] == "📦 Pods\nWhich namespace?"
    bot.handle(press("v:inc", update_id=4))
    assert t.calls[-1][0] == "sendMessage" and t.sent()[-1]["text"].startswith("🗂 Incidents: All\n1 Incident ·")
    assert c.patches == []


def test_the_bot_uses_the_configured_ref() -> None:
    from nexus_bot.config import Config

    c, t = FakeCluster(escalated()), FakeTransport()
    cfg = Config(allowlist={OWNER_ID: "koussay-approver"}, repo_ref="v0.6.0", token="")
    Bot(cfg, TelegramClient(t), c, c.approver_for, now=lambda: NOW).handle(press("c:" + KEY))
    [e] = [e for e in t.sent()[-1]["entities"] if e["type"] == "text_link"]
    assert e["url"] == f"{REPO}/commit/{SHA}"
    Bot(cfg, TelegramClient(t), c, c.approver_for, now=lambda: NOW).handle(press("b:" + KEY, update_id=3))
    urls = [e["url"] for e in t.sent()[-1]["entities"] if e["type"] == "text_link"]
    assert urls == [f"{REPO}/blob/v0.6.0/reasoner/nexus_reasoner/prompt.py",
                    f"{REPO}/blob/v0.6.0/runbooks/rb-cpu-saturation.md"]
