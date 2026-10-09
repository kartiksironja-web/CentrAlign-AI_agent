"""Starts the mock company server: `python -m apps.company.main`.

Purpose: run portal + ERP + admin on http://127.0.0.1:<PORT> against the data
folder created by `python -m apps.company.seed --reset`.
Why this design: settings come from the environment (.env), so the eval runner
can start one server per run on a free port with its own data folder and FAULTS
by just setting env vars. It binds to 127.0.0.1 only: the mock world is never
exposed to the network.
How it can fail: if the database has not been seeded it says so and exits
instead of starting an empty app.
"""
import argparse
import os
import sys

import uvicorn

from apps.company import config
from apps.company.app import create_app
from apps.company.faults import parse_fault_list


def main() -> int:
    config.load_env()
    parser = argparse.ArgumentParser(description="Run the mock company (portal + ERP).")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8000))
    parser.add_argument("--quiet", action="store_true", help="hide the per-request log")
    args = parser.parse_args()

    data_dir = config.default_data_dir()
    if not (data_dir / "company.db").exists():
        print(f"No database at {data_dir / 'company.db'}. Run: python -m apps.company.seed --reset")
        return 1
    faults = parse_fault_list(os.environ.get("FAULTS"))
    app = create_app(data_dir, faults=faults, admin_token=os.environ.get("ADMIN_TOKEN") or None)
    print(f"Mock company on http://127.0.0.1:{args.port}  data={data_dir}  faults={faults or 'none'}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning" if args.quiet else "info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
