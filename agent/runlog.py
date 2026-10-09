"""The run log: runs/<id>/run.json, the complete audit trail of one run.

Purpose: record everything needed to answer "why did the agent do X?" afterwards: config,
contract, handbook ids, plan versions, every step (reason, tool, arguments, gate
decisions, result summary, detector lines, tokens, milliseconds), self-checks, declared
assertions (every version), audit attempts with per-check results, human interactions
(marked scripted or real), outcome and metrics.
Why this design: one plain JSON file, rewritten after every step so a crash still leaves
the trail up to that point. Nothing from the environment is logged, so API keys cannot end
up in it.
How it can fail: it records what the loop tells it; a bug in the loop would be recorded
faithfully, which is the point. The file is replaced atomically, never half-written.
"""
import json
import os
import uuid
from datetime import datetime
from pathlib import Path


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]


class RunLog:
    def __init__(self, runs_dir: Path, run_id: str | None = None):
        self.run_id = run_id or new_run_id()
        self.dir = Path(runs_dir) / self.run_id
        self.path = self.dir / "run.json"
        self.data: dict = {"run_id": self.run_id, "started_at": datetime.now().isoformat(timespec="seconds")}

    def set(self, key: str, value) -> None:
        self.data[key] = value

    def write(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(temp, self.path)
