"""Starts the console: `python -m apps.console.main` then open http://127.0.0.1:8500.

Purpose: one command for the demo. If the mock company is not running it is started (and seeded if its data does not exist yet), then
the console server starts on its own port.
Why this design: so the person filming does not need two terminals. The company app runs as a child process and is stopped again
when the console stops; it binds to 127.0.0.1 only, like the console.
How it can fail: if the company port is taken by something that is not the mock company, runs fail with a clear message; the child
process is only started when nothing answers on its port.
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import uvicorn
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]


def ensure_company(base_url: str, data_dir: Path) -> subprocess.Popen | None:
    try:
        httpx.get(base_url + "/", timeout=2)
        return None  # already running
    except httpx.HTTPError:
        pass
    if not (data_dir / "company.db").exists():
        subprocess.run([sys.executable, "-m", "apps.company.seed", "--reset"], cwd=REPO_ROOT, check=True)
    child = subprocess.Popen([sys.executable, "-m", "apps.company.main", "--quiet"], cwd=REPO_ROOT)
    for _ in range(40):
        try:
            httpx.get(base_url + "/", timeout=1)
            return child
        except httpx.HTTPError:
            time.sleep(0.25)
    child.terminate()
    raise SystemExit(f"the company app did not start on {base_url}")


def main() -> int:
    load_dotenv(REPO_ROOT / ".env", override=False)
    parser = argparse.ArgumentParser(description="Run the VerifyOps web console.")
    parser.add_argument("--port", type=int, default=8500)
    args = parser.parse_args()
    from apps.console.server import create_app

    base_url = f"http://127.0.0.1:{os.environ.get('PORT') or 8000}"
    data_dir = Path(os.environ.get("COMPANY_DATA_DIR") or REPO_ROOT / "data")
    child = ensure_company(base_url, data_dir)
    print(f"Company app: {base_url}  |  Console: http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    try:
        uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        if child is not None:
            child.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
