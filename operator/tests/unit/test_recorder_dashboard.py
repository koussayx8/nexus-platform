"""Every query of the Grafana Flight Recorder dashboard runs against the real schema, as the
read-only role Grafana uses (spec §14). Grafana's macros are replaced by plain SQL first."""

import json
import pathlib
import re

DASHBOARD = (
    pathlib.Path(__file__).resolve().parents[3]
    / "platform"
    / "observability"
    / "dashboards"
    / "flight-recorder.json"
)


def queries():
    d = json.loads(DASHBOARD.read_text())
    out = [p["targets"][0]["rawSql"] for p in d["panels"]]
    out.append(d["templating"]["list"][0]["query"])
    return out


def to_plain_sql(sql: str) -> str:
    sql = re.sub(r"\$__timeFilter\([^)]*\)", "true", sql)
    return sql.replace("${run}", "All")


def test_dashboard_has_panels_and_uses_the_provisioned_datasource():
    d = json.loads(DASHBOARD.read_text())
    assert len(d["panels"]) >= 6
    assert {p["datasource"]["uid"] for p in d["panels"]} == {"nexus-flight-recorder"}


def test_every_query_runs_as_the_reader_role(pg):
    for sql in queries():
        r = pg.psql("nexus_reader", to_plain_sql(sql))
        assert r.returncode == 0, f"{sql}\n{r.stderr}"
