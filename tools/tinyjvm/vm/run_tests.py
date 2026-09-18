#!/usr/bin/env python3
"""Step 1's smoke test harness (build spec section 11): compiles the
native VM host, hand-assembles every sample and trap case, runs each
through it, and asserts stdout/exit-code match what's expected. This is
the "a hand-assembled .tvm runs and prints" deliverable made repeatable,
plus proof the trap system (build spec section 7.1) actually works, not
just the happy path.

Usage (from tools/tinyjvm/vm/, with the toolchain env sourced):
    python run_tests.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
SAMPLES_DIR = HERE / "samples"
HOST_EXE = HERE / ("tinyjvm_host.exe" if sys.platform == "win32" else "tinyjvm_host")


def build_host() -> None:
    print("compiling vm.c + host_native.c ...")
    result = subprocess.run(
        ["gcc", "-std=c11", "-Wall", "-Wextra", "-Wpedantic", "-O2",
         "-o", str(HOST_EXE), "vm.c", "host_native.c"],
        cwd=HERE, capture_output=True, text=True,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        print("BUILD FAILED", file=sys.stderr)
        sys.exit(1)
    print("build OK\n")


def build_fixtures() -> dict[str, tuple[str | None, int]]:
    sys.path.insert(0, str(SAMPLES_DIR))
    import build_samples  # noqa: E402  (path must be set up first)

    expectations: dict[str, tuple[str | None, int]] = {}
    for filename, (builder, expected_stdout, expected_exit) in {
        **build_samples.SAMPLES, **build_samples.TRAP_CASES
    }.items():
        build_samples.write_tvm(str(SAMPLES_DIR / filename), builder())
        expectations[filename] = (expected_stdout, expected_exit)
    return expectations


def run_one(filename: str, expected_stdout: str | None, expected_exit: int) -> bool:
    result = subprocess.run(
        [str(HOST_EXE), str(SAMPLES_DIR / filename)],
        capture_output=True, text=True,
    )
    actual_stdout = result.stdout.strip()
    ok_exit = result.returncode == expected_exit
    ok_stdout = expected_stdout is None or actual_stdout == expected_stdout
    passed = ok_exit and ok_stdout

    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {filename}: exit={result.returncode} (want {expected_exit})", end="")
    if expected_stdout is not None:
        print(f", stdout={actual_stdout!r} (want {expected_stdout!r})", end="")
    else:
        print(f", stderr={result.stderr.strip()!r}", end="")
    print()
    return passed


if __name__ == "__main__":
    build_host()
    expectations = build_fixtures()

    all_passed = True
    for filename, (expected_stdout, expected_exit) in expectations.items():
        if not run_one(filename, expected_stdout, expected_exit):
            all_passed = False

    print()
    if all_passed:
        print(f"all {len(expectations)} cases passed")
        sys.exit(0)
    else:
        print("one or more cases FAILED")
        sys.exit(1)
