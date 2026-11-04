"""Prints, as JSON, how Locust's own dispatcher splits a swarm across the NEXUS user classes.

Usage: <python with locust installed> dispatch_probe.py LOCUSTFILE SELECTION N [N...]
SELECTION is "baseline" (DevUser, ProdUser: the master's autostart) or "s2" (those plus DevCpuUser).
Output: {"<N>": {"<class>": users, ...}, ...}. Used by test_ramp.LocustfileTest; needs locust
(pinned 2.46.7 in CI); it starts no users and sends no request.
"""

import importlib.util
import json
import sys

from locust.dispatch import UsersDispatcher
from locust.runners import WorkerNode


def split(classes, n):
    d = UsersDispatcher(worker_nodes=[WorkerNode("w1")], user_classes=classes)
    d.new_dispatch(target_user_count=n, spawn_rate=1000)
    for _ in d:
        pass
    return dict(d._users_on_workers["w1"])


def main(argv):
    path, selection, *counts = argv
    spec = importlib.util.spec_from_file_location("nexus_locustfile", path)
    lf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lf)
    classes = [lf.DevUser, lf.ProdUser] + ([lf.DevCpuUser] if selection == "s2" else [])
    print(json.dumps({n: split(classes, int(n)) for n in counts}))


if __name__ == "__main__":
    main(sys.argv[1:])
