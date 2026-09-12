"""Escalation ladder — try alternative sources before rejecting a topic.

Researcher+Strategist form a ladder of attempts. The Strategist scores
the chosen topic; below threshold -> next stage. Every transition is
logged to escalation.log and surfaced as a WARN (run-log summary + the
end-of-run fleet report), not per-event. Stage 4 (evergreen seed list) is
API-independent. Every stage must still pass the same quality threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from logging_setup import escalation_log

# Topic score (0..1) at/above which the Strategist's pick is accepted.
SCORE_THRESHOLD = 0.62


@dataclass(frozen=True)
class StageSpec:
    stage: int
    approach: str
    model_key: str          # 'sonnet' | 'opus'
    use_gsc: bool
    use_dataforseo: bool
    use_websearch: bool
    use_seed_list: bool


STAGES: dict[int, StageSpec] = {
    1: StageSpec(1, "GSC near-top (pos 5–20) + DataForSEO expansion",
                 "sonnet", True, True, False, False),
    2: StageSpec(2, "Loosen GSC thresholds + SERP-gap via web search + competitors",
                 "sonnet", True, True, True, False),
    3: StageSpec(3, "Full web-search gap analysis + competitors, reframe intent",
                 "opus", False, False, True, False),
    4: StageSpec(4, "Evergreen seed list minus topic_history (final attempt)",
                 "opus", False, False, False, True),
}


class EscalationLadder:
    def __init__(self, cfg, run_dir: Path, logger, start_stage: int = 1):
        self._cfg = cfg
        self._run_dir = Path(run_dir)
        self._log = logger
        self.stage = max(1, start_stage)
        self.max_stage = min(cfg.max_stage, max(STAGES))
        self._record(self.stage, reason="run start")

    @property
    def spec(self) -> StageSpec:
        return STAGES[self.stage]

    def model_id(self) -> str:
        key = STAGES[self.stage].model_key
        return self._cfg.model_opus if key == "opus" else self._cfg.model_sonnet

    def at_guarantee(self) -> bool:
        return self.stage >= self.max_stage

    def _record(self, stage: int, *, reason: str) -> None:
        spec = STAGES[stage]
        msg = (f"stage {stage} [{spec.model_key}] :: {spec.approach} "
               f":: reason: {reason}")
        escalation_log(self._run_dir, msg)
        if self._log:
            self._log.info("escalation %s", msg)

    def escalate(self, reason: str) -> bool:
        """Advance one stage. False at the ceiling means the caller must
        reject the unqualified topic and retain its artifacts for review.
        """
        if self.stage >= self.max_stage:
            self._record(self.stage, reason=f"at ceiling, rejected: {reason}")
            if self._log:
                self._log.warning("escalation ceiling reached (stage %d) — "
                                  "no qualified candidate: %s",
                                  self.stage, reason)
            return False
        self.stage += 1
        self._record(self.stage, reason=reason)
        # Surface as a WARN so the run-log accumulator collects it and the
        # end-of-run fleet report reflects it — not per-event.
        if self._log:
            self._log.warning("degraded to escalation level %d (%s) — %s",
                              self.stage, STAGES[self.stage].model_key, reason)
        return True
