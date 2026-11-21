"""Trace filesystem operations of the pristine suite, including native children."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

PIN = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if (source / ".blast-radius-pin").read_text().strip() != PIN: raise SystemExit("source must be the pinned archive")
    output.mkdir(parents=True, exist_ok=True)
    inventory = {path.relative_to(source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(source.rglob("*")) if path.is_file() and not any(part in (".git", "__pycache__", ".pytest_cache") for part in path.relative_to(source).parts)}
    (output / "inventory.json").write_text(json.dumps(inventory, sort_keys=True))
    env = dict(os.environ, BLAST_SOURCE=str(source), BLAST_RAW_FILES=str(output / "python-files.json"), PYTHONPATH=os.pathsep.join([str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parent.parent), str(source)]), SKIP_INTEGRATION="0", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1", OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
    command = ["/opt/trace/lib/ld-musl-x86_64.so.1", "--library-path", "/opt/trace/lib", "/opt/trace/strace", "-f", "-qq", "-s", "4096", "-yy", "-e", "trace=%file", "-o", str(output / "files.strace"), sys.executable, "-m", "pytest", "-p", "filesystem_plugin", "-v", "--tb=short", "tests"]
    with (output / "pytest.log").open("w") as log:
        run = subprocess.run(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=1200)
    print(f"pristine filesystem pass exit={run.returncode}; output={output}")
    if run.returncode: raise SystemExit(run.returncode)


if __name__ == "__main__": main()
