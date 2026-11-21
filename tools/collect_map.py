"""Run baseline pytest with coverage, file-read, and class-use recording."""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path(".work/raw-map.json"))
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--pytest-args", nargs=argparse.REMAINDER, default=[])
    parser.add_argument("--archive", action="store_true", help="source was downloaded by fetch_source.py")
    parser.add_argument("--reads-only", action="store_true", help="refresh dictionary/file reads without coverage or scalar fixtures")
    parser.add_argument("--coverage-only", action="store_true", help="measure unmodified-source coverage without operand/dictionary probes")
    parser.add_argument("--attributes-only", action="store_true", help="measure literal class-setting reads independently")
    parser.add_argument("--types-only", action="store_true", help="refresh operand types without snapshots or coverage")
    parser.add_argument("--ranges-only", action="store_true", help="record integer ranges at comparisons with literal bounds")
    parser.add_argument("--blocks-only", action="store_true", help="record pristine interpreter basic-block execution")
    parser.add_argument("--factories-only", action="store_true", help="record returned-closure users for pure factories")
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if args.archive:
        if (source / ".blast-radius-pin").read_text().strip() != "eb11aec4be8cb2122afe713a4816662c3f0fb2fa":
            raise SystemExit("source archive has no valid pinned-source marker")
    else:
        pin = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        if pin != "eb11aec4be8cb2122afe713a4816662c3f0fb2fa":
            raise SystemExit("source checkout must be the challenge's pinned commit")
        subprocess.run(["git", "-C", str(source), "diff", "--exit-code", "HEAD", "--", "sqlglot", "tests"], check=True, stdout=subprocess.DEVNULL)
    output.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, BLAST_SOURCE=str(source), BLAST_RAW_MAP=str(output), SKIP_INTEGRATION="0", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONPATH=os.pathsep.join([str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parent.parent), str(source)]), OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
    env["BLAST_READS_ONLY"] = "1" if args.reads_only else "0"
    env["BLAST_COVERAGE_ONLY"] = "1" if args.coverage_only else "0"
    env["BLAST_ATTRIBUTES_ONLY"] = "1" if args.attributes_only else "0"
    env["BLAST_TYPES_ONLY"] = "1" if args.types_only else "0"
    env["BLAST_RANGES_ONLY"] = "1" if args.ranges_only else "0"
    env["BLAST_BLOCKS_ONLY"] = "1" if args.blocks_only else "0"
    env["BLAST_FACTORIES_ONLY"] = "1" if args.factories_only else "0"
    with output.with_suffix(".log").open("w", encoding="utf-8") as log:
        run = subprocess.run([sys.executable, "-m", "pytest", "-p", "coverage_plugin", "-v", "--tb=short", "tests", *args.pytest_args], cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout)
    print(f"pytest exit={run.returncode}; map={output}; log={output.with_suffix('.log')}")
    if not output.exists():
        raise SystemExit("coverage collection failed before producing a map")


if __name__ == "__main__":
    main()
