"""One command to run the whole YourDoc test suite.

    python tools/run_tests.py            # fast layers + coverage gates
    python tools/run_tests.py --fuzz     # + property-based / fuzz
    python tools/run_tests.py --perf     # + scale / timing budgets
    python tools/run_tests.py --all      # everything, including the installer
    python tools/run_tests.py --report   # verbose, with every outcome listed

Coverage gates are enforced here rather than in ``pytest.ini`` so that running a
single test file directly never trips them:

* ``src/engine`` must stay at or above ``ENGINE_MIN`` (the download/transform
  core);
* everything shipped (``src`` plus ``app.py``) must stay at or above
  ``OVERALL_MIN``.
"""

from __future__ import annotations

import argparse
import io
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = PROJECT_ROOT / "tests"

ENGINE_MIN = 90.0
OVERALL_MIN = 80.0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the YourDoc test suite.")
    parser.add_argument("--fuzz", action="store_true", help="include property/fuzz tests")
    parser.add_argument(
        "--perf",
        action="store_true",
        help="include the scale/timing tests (they are slow but catch quadratic work)",
    )
    parser.add_argument("--e2e", action="store_true", help="include packaging/installer tests")
    parser.add_argument("--all", action="store_true", help="include every opt-in layer")
    parser.add_argument("--report", action="store_true", help="verbose output, all outcomes")
    parser.add_argument("--no-cov", action="store_true", help="skip coverage and its gates")
    parser.add_argument("-k", dest="keyword", default=None, help="only tests matching this expression")
    return parser.parse_args(argv)


def build_pytest_command(args: argparse.Namespace) -> list[str]:
    cmd = [sys.executable, "-m", "pytest", str(TESTS_DIR)]
    cmd += ["-v", "-rA"] if args.report else ["-q"]
    if args.keyword:
        cmd += ["-k", args.keyword]
    if args.all or args.fuzz:
        cmd.append("--run-fuzz")
    if args.all or args.perf:
        cmd.append("--run-perf")
    if args.all or args.e2e:
        cmd.append("--run-e2e")
    if not args.no_cov:
        cmd += ["--cov=src", "--cov-report=term-missing:skip-covered"]
        # Measure the entry point too, but as a module name so coverage
        # resolves it to the imported ``app`` module rather than a bare path.
        if (PROJECT_ROOT / "app.py").is_file():
            cmd.append("--cov=app")
    return cmd


def coverage_gates() -> int:
    """Check the per-area coverage thresholds against the run just made.

    This is only ever called when the run was *supposed* to collect coverage,
    so anything that stops the measurement from happening is a failure, not a
    reason to quietly pass. An unreadable or absent ``.coverage`` means the
    gates were never actually evaluated.
    """

    try:
        import coverage
    except Exception as exc:  # noqa: BLE001
        print(f"\nFAIL  cannot evaluate coverage gates: {exc}")
        print("      install the test tools: pip install -r requirements-dev.txt")
        return 1

    data_file = PROJECT_ROOT / ".coverage"
    if not data_file.is_file():
        print("\nFAIL  no .coverage data was written, so the gates never ran")
        return 1

    cov = coverage.Coverage(data_file=str(data_file))
    try:
        cov.load()
    except Exception as exc:  # noqa: BLE001
        print(f"\nFAIL  could not read coverage data: {exc}")
        return 1

    def percent(include: list[str]) -> float:
        # No catch-all: if reporting fails the run is broken and must say so
        # rather than reporting 0.0 and being masked by the other figure.
        return float(cov.report(include=include, file=io.StringIO()))

    engine = percent(["src/engine/*"])
    overall = percent(["src/*", "app.py"])

    print("\n" + "=" * 60)
    print("coverage gates")
    print(f"  engine   {engine:6.1f}%  (min {ENGINE_MIN:.0f}%)")
    print(f"  overall  {overall:6.1f}%  (min {OVERALL_MIN:.0f}%)")

    failed = False
    if engine < ENGINE_MIN:
        print("  FAIL  engine coverage below threshold")
        failed = True
    if overall < OVERALL_MIN:
        print("  FAIL  overall coverage below threshold")
        failed = True
    print("=" * 60)
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    cmd = build_pytest_command(args)

    if not args.no_cov:
        # Delete any earlier run's data first. The gate reads .coverage back
        # off disk, so a leftover file could otherwise satisfy it while the
        # current run collected nothing.
        data_file = PROJECT_ROOT / ".coverage"
        if data_file.exists():
            data_file.unlink()

    print(f"running: {' '.join(cmd)}\n")
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), check=False)
    if result.returncode != 0:
        return result.returncode

    if args.no_cov:
        return 0
    return coverage_gates()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
