"""ark-agent-fleet report channel.

A run's outcome is delivered to the shared fleet journal (``ark-agent-fleet``)
instead of a personal Telegram digest. The fleet is a git repo; its documented
entrypoint for an *external* animal is the CLI
``$ARK_REPO/ark/river-report-flow/publish-cli.ts`` (node v22+), which reads a
``ReportInput`` JSON object on stdin, validates it against ``report.schema.json``
(ajv), writes ``reports/{zoo}/{YYYY-MM-DD}/{animal}-{HHMMSS}.json``, and — unless
``ARK_NO_SYNC=1`` — git-commits + pushes it into the fleet repo.

Fail-soft by contract: a missing/broken ark or node must never abort a publish
run. Every failure is logged (WARN) and swallowed; ``submit`` returns None. The
whole-run report is also written locally (``runs/<date>/report.json``) by the
orchestrator, so the run stays fully observable even when the fleet is down.
"""

from __future__ import annotations

import json
import os
import subprocess


class FleetClient:
    _REL_CLI = "ark/river-report-flow/publish-cli.ts"

    def __init__(self, ark_repo: str, zoo: str = "zoo", *,
                 no_sync: bool = False, node_bin: str = "node",
                 logger=None, timeout: int = 120, runner=subprocess.run):
        self._ark_repo = (ark_repo or "").strip()
        self._zoo = zoo or "zoo"
        self._no_sync = bool(no_sync)
        self._node_bin = node_bin or "node"
        self._log = logger
        self._timeout = timeout
        self._runner = runner        # injected seam for tests (no node needed)

    def submit(self, report_input: dict, *, dry_run: bool = False) -> str | None:
        """Pipe ``report_input`` JSON to publish-cli.ts on stdin.

        Returns the relative report path printed on stdout, or None when the
        submission is skipped (disabled / dry-run) or fails. NEVER raises.
        """
        if not self._ark_repo:
            if self._log:
                self._log.info("fleet reporting disabled: ARK_REPO unset")
            return None
        if dry_run:
            # A dry-run must not write into the shared fleet checkout. The local
            # runs/<date>/report.json still captures exactly what would ship.
            if self._log:
                self._log.info("dry-run: skipping fleet submission")
            return None

        cli = os.path.join(self._ark_repo, self._REL_CLI)
        env = {**os.environ, "ARK_ZOO": self._zoo}
        if self._no_sync:
            env["ARK_NO_SYNC"] = "1"

        try:
            proc = self._runner(
                [self._node_bin, cli],
                input=json.dumps(report_input, ensure_ascii=False),
                env=env, capture_output=True, text=True,
                timeout=self._timeout, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            # OSError covers a missing node binary (FileNotFoundError);
            # SubprocessError covers TimeoutExpired.
            if self._log:
                self._log.warning("fleet submit dropped (%s): %s",
                                  type(exc).__name__, exc)
            return None
        except Exception as exc:  # noqa: BLE001 — never abort a publish
            if self._log:
                self._log.warning("fleet submit dropped: %s", exc)
            return None

        if proc.returncode == 0:
            rel = (proc.stdout or "").strip()
            if self._log:
                self._log.info("fleet report submitted: %s", rel or "(ok)")
            return rel or None
        if self._log:
            self._log.warning("fleet submit failed rc=%s: %s", proc.returncode,
                              (proc.stderr or "").strip()[:500])
        return None
