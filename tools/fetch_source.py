"""Fetch only sqlglot's permitted pinned source archive, without git or apt."""
from pathlib import Path
import sys
import tarfile
import tempfile
from urllib.request import urlopen

PIN = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"


def main():
    destination = Path(sys.argv[1])
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile() as archive:
        with urlopen(f"https://github.com/tobymao/sqlglot/archive/{PIN}.tar.gz", timeout=90) as response:
            while chunk := response.read(1024 * 1024):
                archive.write(chunk)
        archive.seek(0)
        with tarfile.open(fileobj=archive, mode="r:gz") as bundle:
            prefix = f"sqlglot-{PIN}/"
            for member in bundle:
                if not member.name.startswith(prefix):
                    continue
                member.name = member.name[len(prefix):]
                if member.name:
                    bundle.extract(member, destination, filter="data")
    (destination / ".blast-radius-pin").write_text(PIN + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
