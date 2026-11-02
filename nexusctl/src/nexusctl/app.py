"""The Typer app: global options, exit codes, and the four core commands (spec §16A).

Commands are thin: they build the Kubernetes-backed API and call the functions in commands/.
`_api_factory`, `_prompt` and `_now` are the seams the tests replace.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from nexus_client import approval as ap
from nexus_client.incidents import IncidentApi, KubeIncidentApi
from rich.console import Console

from . import config as cfg
from .commands import approvals, incidents

app = typer.Typer(no_args_is_help=True,
                  help="Inspect NEXUS incidents and approve or reject proposals. Adds no authority.")


class State:
    context: str | None = None
    kubeconfig: str | None = None
    output: str = "table"
    config_path: Path | None = None
    no_color: bool = False


state = State()


def _api_factory(c: cfg.Config) -> IncidentApi:
    return KubeIncidentApi(namespace=c.incident_namespace, context=state.context,
                           kubeconfig=state.kubeconfig)


def _prompt(text: str) -> str:
    return str(typer.prompt(text, default="", show_default=False))


def _now() -> datetime:
    return datetime.now(UTC)


def _out() -> Callable[[str], None]:
    # markup and highlighting off: nothing printed is ever interpreted as Rich markup
    console = Console(markup=False, highlight=False, emoji=False, no_color=state.no_color,
                      soft_wrap=True)
    return lambda text: console.print(text)


@app.callback()
def main_options(
    context: Annotated[str | None, typer.Option("--context", help="kubeconfig context")] = None,
    kubeconfig: Annotated[str | None, typer.Option("--kubeconfig")] = None,
    output: Annotated[str, typer.Option("-o", "--output", help="table, json or yaml")] = "table",
    config: Annotated[Path | None, typer.Option("--config")] = None,
    no_color: Annotated[bool, typer.Option("--no-color")] = False,
) -> None:
    if output not in ("table", "json", "yaml"):
        raise typer.BadParameter("output must be table, json or yaml")
    state.context, state.kubeconfig, state.output = context, kubeconfig, output
    state.config_path, state.no_color = config, no_color


@app.command("list")
def list_cmd(
    active: Annotated[bool, typer.Option("--active", help="non-terminal phases only")] = False,
    phase: Annotated[str | None, typer.Option("--phase")] = None,
    ns: Annotated[str | None, typer.Option("--ns", help="target namespace")] = None,
    since: Annotated[str | None, typer.Option("--since", help="for example 30m, 2h, 1d")] = None,
    limit: Annotated[int, typer.Option("--limit")] = 50,
) -> None:
    c = cfg.load(state.config_path)
    raise typer.Exit(incidents.run_list(
        _api_factory(c), active=active, phase=phase, ns=ns, since=since, limit=limit,
        output=state.output, now=_now(), out=_out()))


@app.command("explain")
def explain_cmd(
    incident: str,
    evidence: Annotated[bool, typer.Option("--evidence")] = False,
    raw: Annotated[bool, typer.Option("--raw")] = False,
) -> None:
    c = cfg.load(state.config_path)
    raise typer.Exit(incidents.run_explain(
        _api_factory(c), incident, evidence=evidence, raw=raw, output=state.output,
        out=_out(), max_chars=c.max_field_chars))


def _ttl(text: str, maximum: str) -> timedelta:
    try:
        ttl = ap.parse_ttl(text)
        if ttl > ap.parse_ttl(maximum):
            raise ValueError(f"ttl may not exceed the configured maximum {maximum}")
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    return ttl


@app.command("approve")
def approve_cmd(
    incident: str,
    ttl: Annotated[str, typer.Option("--ttl", help="at most 15m")] = "15m",
) -> None:
    c = cfg.load(state.config_path)
    raise typer.Exit(approvals.run_decide(
        _api_factory(c), incident, decision="approved", ttl=_ttl(ttl, c.approval_max_ttl),
        reason=None, prompt=_prompt, now=_now, out=_out(), max_chars=c.max_field_chars))


@app.command("reject")
def reject_cmd(
    incident: str,
    reason: Annotated[str, typer.Option("--reason", help="why (stored in spec.approval.reason)")],
) -> None:
    c = cfg.load(state.config_path)
    raise typer.Exit(approvals.run_decide(
        _api_factory(c), incident, decision="rejected", ttl=ap.MAX_TTL, reason=reason,
        prompt=_prompt, now=_now, out=_out(), max_chars=c.max_field_chars))


def main() -> None:
    app()
