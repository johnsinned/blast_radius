"""Build and score the sample under the competition's container restrictions."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from score import evaluate_run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--docker", default=shutil.which("docker"))
    parser.add_argument("--image", default="blast-radius-candidate")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--entrypoint", action="store_true", help="check the image's unmodified default command")
    args = parser.parse_args()
    if not args.docker:
        installed = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/DockerDesktop/resources/bin/docker.exe"
        args.docker = str(installed) if installed.is_file() else None
    if not args.docker:
        raise SystemExit("Docker CLI not found; pass --docker with its full path")
    env = dict(os.environ, PATH=str(Path(args.docker).resolve().parent) + os.pathsep + os.environ.get("PATH", ""))
    if not args.skip_build:
        subprocess.run([args.docker, "build", "--platform", "linux/amd64", "--tag", args.image, "."], cwd=ROOT, env=env, check=True, timeout=600)
    name = "blast-radius-check-" + uuid.uuid4().hex[:12]
    command = [args.docker, "run", "--rm", "--name", name, "--platform", "linux/amd64", "--network", "none", "--cpus", "2", "--memory", "4096m", "--memory-swap", "4096m", "--pids-limit", "256", "--read-only", "--tmpfs", "/tmp:rw,size=64m,mode=1777", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "65534:65534", "--env", "HOME=/tmp"]
    for source, target in ((ROOT / "sample/commits", "/data/commits"), (ROOT / "sample/tests.json", "/data/tests.json")):
        command.extend(["--mount", f"type=bind,source={source},target={target},readonly"])
    wrapper = "import resource,runpy,sys;sys.path.insert(0,'/app');sys.argv=['/app/selector.py'];runpy.run_path('/app/selector.py',run_name='__main__');r=resource.getrusage(resource.RUSAGE_SELF);print('BLAST_CPU_SECONDS='+str(r.ru_utime+r.ru_stime),file=sys.stderr)"
    command.extend([args.image] if args.entrypoint else [args.image, "python", "-c", wrapper])
    started = time.perf_counter()
    try:
        run = subprocess.run(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180, check=True)
    except BaseException as error:
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr.decode("utf-8", errors="replace"), file=sys.stderr)
        subprocess.run([args.docker, "rm", "--force", name], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        raise
    elapsed = time.perf_counter() - started
    if len(run.stdout) > 2_000_000:
        raise SystemExit("container stdout exceeds scorer limit")
    (ROOT / "output-container.jsonl").write_bytes(run.stdout)
    cpu = None
    for line in run.stderr.decode("utf-8", errors="replace").splitlines():
        if line.startswith("BLAST_CPU_SECONDS="):
            cpu = float(line.split("=", 1)[1])
        else:
            print(line, file=sys.stderr)
    tests = json.loads((ROOT / "sample/tests.json").read_text("utf-8"))["tests"]
    answers = json.loads((ROOT / "sample/answers.json").read_text("utf-8"))["commits"]
    report = evaluate_run(run.stdout, tests, answers)
    report.update(container_python_cpu_seconds=cpu, docker_wall_seconds=round(elapsed, 3), stdout_bytes=len(run.stdout))
    if report["ok"] and cpu is not None:
        report["sample_total_seconds"] = round(report["simulated_milliseconds"] / 1000 + cpu, 6)
    print(json.dumps(report, indent=2))
    if not report["ok"] or report["missed_commits"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
