"""A throwaway PostgreSQL for the Flight Recorder tests: initdb in a temp dir, schema and roles
applied from operator/k8s/flight-recorder-db exactly as the pod's init does (05-schema.sql, then
10-roles.sql with the passwords in the environment). Offline: it listens on 127.0.0.1 only.

`start()` returns None when no server binaries are on the machine; the tests then skip.
GitHub's ubuntu runners have them under /usr/lib/postgresql/<n>/bin. initdb refuses to run as
root, so as root everything runs through `runuser -u postgres`.
"""

import contextlib
import glob
import os
import pathlib
import shutil
import socket
import subprocess
import tempfile

K8S = pathlib.Path(__file__).resolve().parents[2] / "k8s" / "flight-recorder-db"

PASSWORDS = {"WRITER": "w-pass", "RUNNER": "r-pass", "READER": "d-pass"}
DB = "nexus_recorder"


def _bindir() -> str | None:
    initdb = shutil.which("initdb")
    if initdb:
        return os.path.dirname(initdb)
    found = sorted(glob.glob("/usr/lib/postgresql/*/bin/initdb"))
    return os.path.dirname(found[-1]) if found else None


class Server:
    def __init__(self, bindir: str, root: str, port: int) -> None:
        self.bindir, self.root, self.port = bindir, root, port
        self.data = os.path.join(root, "data")
        self._as_root = os.geteuid() == 0

    def _prefix(self) -> list[str]:
        return ["runuser", "-u", "postgres", "--"] if self._as_root else []

    def _run(self, argv: list[str], **kw) -> subprocess.CompletedProcess:
        env = {
            "PATH": f"{self.bindir}:/usr/sbin:/usr/bin:/bin",
            "PGPASSWORD": "",
            **kw.pop("env", {}),
        }
        return subprocess.run(
            self._prefix() + argv,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            **kw,
        )

    def psql(
        self, user: str, sql: str, *, password: str = ""
    ) -> subprocess.CompletedProcess:
        return self._run(
            [
                "psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-h",
                "127.0.0.1",
                "-p",
                str(self.port),
                "-U",
                user,
                "-d",
                DB,
                "-At",
                "-c",
                sql,
            ],
            env={"PGPASSWORD": password},
        )

    def _psql_file(self, path: pathlib.Path, env: dict) -> subprocess.CompletedProcess:
        return self._run(
            [
                "psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-h",
                "127.0.0.1",
                "-p",
                str(self.port),
                "-U",
                "postgres",
                "-d",
                DB,
                "-f",
                str(path),
            ],
            env=env,
        )

    def init(self) -> None:
        if self._as_root:
            shutil.chown(self.root, "postgres", "postgres")
        r = self._run(["initdb", "-D", self.data, "-U", "postgres", "--auth=trust"])
        assert r.returncode == 0, r.stderr
        r = self._run(
            [
                "pg_ctl",
                "-D",
                self.data,
                "-w",
                "-l",
                os.path.join(self.root, "log"),
                "-o",
                f"-c listen_addresses=127.0.0.1 -p {self.port} -k {self.root}",
                "start",
            ]
        )
        assert r.returncode == 0, r.stderr + r.stdout
        r = self._run(
            ["createdb", "-h", "127.0.0.1", "-p", str(self.port), "-U", "postgres", DB]
        )
        assert r.returncode == 0, r.stderr
        env = {f"{k}_PASSWORD": v for k, v in PASSWORDS.items()}
        for name in ("05-schema.sql", "10-roles.sql"):
            r = self._psql_file(K8S / name, env)
            assert r.returncode == 0, f"{name}: {r.stderr}"

    def stop(self) -> None:
        self._run(["pg_ctl", "-D", self.data, "-m", "immediate", "stop"])
        shutil.rmtree(self.root, ignore_errors=True)


def _free_port() -> int:
    with contextlib.closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start() -> Server | None:
    bindir = _bindir()
    if bindir is None:
        return None
    if (
        os.geteuid() == 0
        and subprocess.run(
            ["id", "postgres"], capture_output=True, check=False
        ).returncode
    ):
        return None
    root = tempfile.mkdtemp(prefix="nexus-pg-")
    os.chmod(root, 0o755)
    server = Server(bindir, root, _free_port())
    try:
        server.init()
    except BaseException:
        server.stop()
        raise
    return server
