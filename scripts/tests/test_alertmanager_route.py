"""G6: the Alertmanager route that groups every nexus_target alert by that label (spec §3), and the
two KSM alerts carry the nexus_target the operator's poller accepts. The Telegram values file, after the
base file, gives that route its receiver (ADR-040 decision 12). Offline; the live Alertmanager
config check is pending (live check pending)."""
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cid", ROOT / "scripts/check-ignore-differences.py")
cid = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cid)

OBS = ROOT / "platform/observability"


class AlertmanagerRoute(unittest.TestCase):
    def test_nexus_target_alerts_are_grouped_by_it_and_the_rest_keep_the_default(self):
        route = cid.load_all(OBS / "kube-prometheus-stack-values.yaml")[0]["alertmanager"]["config"]["route"]
        self.assertEqual(route["group_by"], ["namespace"])
        grouped = [r for r in route["routes"] if r.get("group_by") == ["nexus_target"]]
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["matchers"], ['nexus_target =~ ".+"'])
        # The base file alone has no NEXUS receiver (spec §3): the operator polls the v2 API.
        self.assertEqual({r["receiver"] for r in route["routes"]} | {route["receiver"]}, {"null"})

    def test_the_application_layers_the_telegram_file_after_the_base(self):
        app = cid.load_all(ROOT / "platform/argocd/applications/observability.yaml")[0]
        [chart] = [s for s in app["spec"]["sources"] if s.get("chart")]
        self.assertEqual(chart["helm"]["valueFiles"], [
            "$values/platform/observability/kube-prometheus-stack-values.yaml",
            "$values/platform/observability/alertmanager-telegram-values.yaml"])

    def test_only_the_nexus_target_route_reaches_telegram(self):
        config = cid.load_all(OBS / "alertmanager-telegram-values.yaml")[0]["alertmanager"]["config"]
        route = config["route"]
        self.assertEqual(route["receiver"], "null")
        to_telegram = [r for r in route["routes"] if r["receiver"] != "null"]
        self.assertEqual(to_telegram, [{
            "receiver": "nexus-telegram",
            "matchers": ['nexus_target =~ ".+"'],
            "group_by": ["nexus_target"]}])
        self.assertEqual([r["name"] for r in config["receivers"]], ["null", "nexus-telegram"])

    def test_the_token_and_the_chat_id_are_files_of_the_secret_never_values_in_git(self):
        config = cid.load_all(OBS / "alertmanager-telegram-values.yaml")[0]["alertmanager"]["config"]
        [tg] = [r for r in config["receivers"] if r["name"] == "nexus-telegram"]
        [cfg] = tg["telegram_configs"]
        self.assertNotIn("bot_token", cfg)
        self.assertNotIn("chat_id", cfg)
        mount = "/etc/alertmanager/secrets/alertmanager-telegram/"
        self.assertEqual(cfg["bot_token_file"], mount + "token")
        self.assertEqual(cfg["chat_id_file"], mount + "chat_id")
        self.assertIs(cfg["send_resolved"], True)

    def test_ksm_alerts_carry_namespace_deployment_target(self):
        rule = cid.load_all(OBS / "alerts/nexus-kube-state.yaml")[0]
        alerts = {r["alert"]: r for g in rule["spec"]["groups"] for r in g["rules"]}
        self.assertEqual(set(alerts), {"NexusRolloutStuck", "NexusCrashLooping"})
        for a in alerts.values():
            self.assertEqual(a["labels"]["nexus_target"], "{{ $labels.namespace }}/{{ $labels.deployment }}")
        self.assertEqual(alerts["NexusRolloutStuck"]["for"], "3m")

    def test_both_rule_files_are_in_the_kustomization(self):
        res = cid.load_all(OBS / "alerts/kustomization.yaml")[0]["resources"]
        self.assertEqual(sorted(res), ["nexus-detection.yaml", "nexus-kube-state.yaml"])


if __name__ == "__main__":
    unittest.main()
