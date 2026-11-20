"""KubeApi.pod_log sends sinceSeconds only when asked (Block 5 finding (e))."""

import asyncio

from nexus_operator.kube import Credentials, KubeApi


class _Resp:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self):
        return "a\nb"


class _Session:
    def __init__(self):
        self.calls = []

    def get(self, url, headers=None, params=None):
        self.calls.append((url, dict(params or {})))
        return _Resp()

    async def close(self):
        pass


def _read(**kw):
    async def go():
        api = KubeApi(lambda: Credentials("https://k8s.test", None, "test"))
        await api._session.close()
        api._session = fake = _Session()
        text = await api.pod_log("nexus-dev", "p-1", previous=False, tail=200, **kw)
        return text, fake.calls

    return asyncio.run(go())


def test_since_seconds_is_sent_when_given():
    text, [(url, params)] = _read(since_seconds=900)
    assert text == "a\nb" and url.endswith("/namespaces/nexus-dev/pods/p-1/log")
    assert params == {"tailLines": "200", "previous": "false", "sinceSeconds": "900"}


def test_without_it_the_read_is_unchanged():
    _, [(_, params)] = _read()
    assert params == {"tailLines": "200", "previous": "false"}
