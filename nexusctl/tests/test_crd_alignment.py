"""The client's models and the written approval agree with the Incident CRD in platform/crds."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from fakes import incident_dict
from nexus_client import approval as ap
from nexus_client.models import Approval, Incident, Source, Target

CRD = Path(__file__).resolve().parents[2] / "platform" / "crds" / "incidents.nexus.io.yaml"


def spec_props() -> dict:
    crd = yaml.safe_load(CRD.read_text())
    return crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]["properties"]


def test_approval_fields_equal_the_crd_schema() -> None:
    assert set(Approval.model_fields) == set(spec_props()["approval"]["properties"])


def test_source_and_target_fields_are_in_the_crd() -> None:
    assert set(Target.model_fields) == set(spec_props()["target"]["properties"])
    # `note` is the human-request note of spec §16A (spec.source.note); the CRD gains it with `request`.
    assert set(Source.model_fields) - {"note"} == set(spec_props()["source"]["properties"])


def test_a_written_approval_is_valid_against_the_crd_schema() -> None:
    props = spec_props()["approval"]["properties"]
    inc = Incident.model_validate(incident_dict())
    now = datetime(2026, 10, 14, 9, 13, tzinfo=UTC)
    body = ap.build_approval(inc, ttl=timedelta(minutes=15), now=now, decision="approved")
    assert set(body) <= set(props)
    assert props["decision"]["enum"] == ["approved", "rejected"]
    assert body["decision"] in props["decision"]["enum"]
    lo, hi = props["parameters"]["properties"]["replicas"]["minimum"], props["parameters"]["properties"]["replicas"]["maximum"]
    assert lo <= body["parameters"]["replicas"] <= hi          # C4
    assert len(body["proposalHash"]) <= props["proposalHash"]["maxLength"]
    assert len(body["actionId"]) <= props["actionId"]["maxLength"]
    # the stamped fields are Kyverno's, never the client's
    assert "approvedBy" not in body and "approvedAt" not in body
