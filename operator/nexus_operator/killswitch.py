"""The kill switch, as M1b-8 reads it (plan M1b-8 rev 3 §1.4; spec §11 K4, §16).

Authority over operator mutations is Kyverno's (K4, M3); the Safety Gate's stage-5 check is
advisory (M2). M1b-8 has neither and makes no mutation, so the state changes no transition:
detection and recording go on while halted. The operator reads it once per poll and logs it at
start and on every change. Anything but data.state "active" is halted; a missing ConfigMap is
treated as halted (§16).
"""

CONFIGMAP = "nexus-killswitch"


def state_of(configmap: dict | None) -> str:
    if configmap is None:
        return "missing"
    return (
        "active" if (configmap.get("data") or {}).get("state") == "active" else "halted"
    )
