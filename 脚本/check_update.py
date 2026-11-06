#!/usr/bin/env python3
"""Read-only update check for the published skill; emits one JSON object."""

import hashlib
import json
import subprocess
import time
import urllib.request
from pathlib import Path, PurePosixPath


REPOSITORY = "fanzhantupo995-byte/fashion-listing-generator-skill"
SOURCE = f"https://github.com/{REPOSITORY}"
API = f"https://api.github.com/repos/{REPOSITORY}/git/trees/main?recursive=1"


def blob_hash(path):
    data = path.read_bytes()
    return hashlib.sha1(
        b"blob " + str(len(data)).encode("ascii") + b"\0" + data
    ).hexdigest()


def compare(root, tree):
    if tree.get("truncated") or not tree.get("tree"):
        raise ValueError("Incomplete repository file list")
    published = {}
    for entry in tree["tree"]:
        if entry.get("type") != "blob":
            continue
        path = PurePosixPath(entry["path"])
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Invalid repository path")
        if entry.get("mode") not in ("100644", "100755"):
            raise ValueError("Unsupported repository file type")
        published[str(path)] = entry["sha"]
    changed = []
    for name, expected in published.items():
        path = root / name
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(root.resolve())
            or not path.is_file()
            or blob_hash(path) != expected
        ):
            changed.append(name)
    removed = []
    if (root / ".git").exists():
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True, check=True, timeout=3,
        )
        tracked = result.stdout.decode("utf-8").split("\0")
        removed = [name for name in tracked if name and name not in published]
    return {
        "status": "update_available" if changed or removed else "up_to_date",
        "source": SOURCE,
        "remote_tree": tree["sha"],
        "changed_or_missing": sorted(changed),
        "removed_upstream": sorted(removed),
        "note": "Differences may include local edits; no files were changed.",
    }


def main():
    root = Path(__file__).resolve().parent.parent
    try:
        request = urllib.request.Request(
            API + "&check=" + str(time.time_ns()),
            headers={"Accept": "application/vnd.github+json",
                     "Cache-Control": "no-cache",
                     "User-Agent": "fashion-listing-skill-update-check"},
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            tree = json.load(response)
        result = compare(root, tree)
    except Exception as error:
        result = {"status": "unavailable", "source": SOURCE,
                  "reason": str(error)[:300]}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
