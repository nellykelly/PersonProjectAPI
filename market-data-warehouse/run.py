#!/usr/bin/env python
"""Entrypoint for the market data warehouse.

    python run.py ingest                 # pull yfinance -> RAW
    python run.py build                  # dbt deps + seed + run + snapshot + test
    python run.py all                    # ingest, then build
    python run.py all --target duckdb    # force the DuckDB profile

`make` isn't available on the target machine, so this is the task runner.
`build` shells out to whatever `dbt` is on PATH (see README for install).
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
WAREHOUSE_DIR = PROJECT_ROOT / "warehouse"

# so `import ingest...` works regardless of cwd
sys.path.insert(0, str(PROJECT_ROOT))


def _load_dotenv() -> None:
    env = PROJECT_ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


def cmd_ingest(args: argparse.Namespace) -> int:
    from ingest.config import get_settings, load_tickers
    from ingest.pipeline import ingest

    settings = get_settings()
    tickers = load_tickers(args.tickers) if args.tickers else load_tickers()
    written = ingest(tickers, settings)
    total = sum(written.values())
    print(f"\nRAW load complete ({settings.target}): {total} rows")
    for feed, n in written.items():
        print(f"  {feed:16} {n:>8}")
    return 0 if total else 1


def _dbt_executable() -> str:
    """Prefer the `dbt` sitting next to the current interpreter (the
    project venv), fall back to whatever is on PATH."""
    bindir = Path(sys.executable).parent
    for name in ("dbt.exe", "dbt"):
        cand = bindir / name
        if cand.exists():
            return str(cand)
        cand = bindir / "Scripts" / name
        if cand.exists():
            return str(cand)
    return "dbt"


def _dbt(dbt_args: list[str], target: str) -> int:
    cmd = [_dbt_executable(), *dbt_args,
           "--project-dir", str(WAREHOUSE_DIR),
           "--profiles-dir", str(WAREHOUSE_DIR), "--target", target]
    print(f"$ {' '.join(cmd)}")
    return subprocess.call(cmd)


def cmd_build(args: argparse.Namespace) -> int:
    target = args.target
    for step in (["deps"], ["seed"], ["run"], ["snapshot"], ["test"]):
        rc = _dbt(step, target)
        if rc != 0:
            return rc
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    rc = cmd_ingest(args)
    if rc != 0:
        return rc
    return cmd_build(args)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    _load_dotenv()

    # shared options -- accepted either before or after the subcommand
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--target",
        default=os.environ.get("WAREHOUSE_TARGET", "duckdb"),
        choices=["duckdb", "snowflake", "motherduck"],
        help="dbt target / warehouse backend (default: duckdb or $WAREHOUSE_TARGET)",
    )
    common.add_argument(
        "--tickers", help="path to a tickers file (default ingest/tickers.txt)"
    )

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ingest", parents=[common], help="pull yfinance into RAW")
    sub.add_parser("build", parents=[common], help="dbt deps + seed + run + snapshot + test")
    sub.add_parser("all", parents=[common], help="ingest then build")
    args = parser.parse_args()

    # keep env and flag in sync so ingest.config and dbt agree
    os.environ["WAREHOUSE_TARGET"] = args.target
    # pin one absolute DuckDB path for both the loader and the dbt profile
    # (dbt-duckdb resolves a relative path against the invocation cwd, not
    # --project-dir, so an unqualified name would split them)
    os.environ.setdefault("DUCKDB_PATH", str(WAREHOUSE_DIR / "market.duckdb"))

    return {"ingest": cmd_ingest, "build": cmd_build, "all": cmd_all}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
