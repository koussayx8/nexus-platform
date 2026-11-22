"""The batch refuses C8 cells (protocol #234 §5, ADR-041 limit 7). Offline, no cluster."""

import contextlib
import io
import json
import pathlib
import tempfile

from .. import batch
from . import test_batch as tb

C8_CELL = dict(tb.CELLS[0], cell="C8/R3/S3/L3", scenario="S3", reasoner="R3")


class C8RefusedTest(tb.Base):
    def new_dir(self) -> pathlib.Path:
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return pathlib.Path(d.name)

    def test_init_refuses_a_c8_cell_by_id_phase_or_config(self):
        a = dict(tb.CELLS[0], cell="A/R3/S3/L3", scenario="S3", reasoner="R3")
        for cell in (
            C8_CELL,
            dict(C8_CELL, cell="c8"),
            dict(a, phase="C8"),
            dict(a, config="c8"),
        ):
            with self.subTest(cell=cell["cell"]), self.assertRaises(batch.Refused) as e:
                batch.Queue.create(
                    self.new_dir(), tb.REF, [tb.CELLS[0], cell], precheck_cfg=tb.PRE
                )
            self.assertIn("C8 runs are attended, outside the batch", str(e.exception))

    def test_an_s3_cell_that_is_not_c8_is_accepted(self):
        a = dict(tb.CELLS[0], cell="A/R3/S3/L3", scenario="S3", reasoner="R3")
        q = batch.Queue.create(self.new_dir(), tb.REF, [a], precheck_cfg=tb.PRE)
        self.assertEqual(q.doc["cells"][0]["cell"], "A/R3/S3/L3")

    def test_run_refuses_a_queue_that_holds_a_c8_cell_and_runs_nothing(self):
        # a queue written before this check (or edited by hand): drive refuses before any run
        self.q.doc["cells"] = [
            *self.q.doc["cells"],
            dict(C8_CELL),
        ]  # a copy: CELLS is shared
        self.q.save()
        ran = []
        with self.assertRaises(batch.Refused) as e:
            self.drive(lambda c, r, g: ran.append(c) or tb.ok(), queue=self.reload())
        self.assertIn("C8/R3/S3/L3", str(e.exception))
        self.assertEqual((ran, self.reload().runs), ([], []))

    def test_main_reports_the_refusal_with_exit_code_2(self):
        d = self.new_dir()
        cells = d / "cells.json"
        cells.write_text(json.dumps({"precheck": tb.PRE, "cells": [C8_CELL]}))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = batch.main(["init", "--state", str(d / "q"), "--approval-ref", tb.REF, "--cells", str(cells)])  # fmt: skip
        self.assertEqual(rc, 2)
        self.assertIn("C8 runs are attended, outside the batch", err.getvalue())
        self.assertFalse((d / "q" / "queue.json").exists())
