#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
在「git 协议连不上 github.com」的网络环境下，改走 GitHub REST 推送。

    python push_api.py [提交说明]

原理：逐个文件算 git blob sha1（git hash-object），和远端 main 的树对比，
只把有差异的文件作为 base64 blob 提交，再生成 tree/commit 并前移 main 指针。
等价于一次普通 commit，只是不经过 git 传输层。

需要 .gh_token（classic PAT，勾 repo 权限）。
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = "GitHubJackLiu/aimax"
BRANCH = "main"


def token() -> str:
    path = os.path.join(ROOT, ".gh_token")
    if not os.path.exists(path):
        raise SystemExit("缺少 .gh_token")
    return open(path, encoding="utf-8").read().strip()


def api(path: str, tok: str, method: str = "GET", payload=None):
    url = path if path.startswith("http") else f"https://api.github.com{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {tok}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "aimax-push",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
    return json.loads(body.decode("utf-8", "replace")) if body else {}


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout
    return [p.strip() for p in out.splitlines() if p.strip()]


def blob_sha(path: str) -> str:
    return subprocess.run(["git", "hash-object", path], cwd=ROOT,
                          capture_output=True, text=True, check=True).stdout.strip()


def main() -> None:
    tok = token()
    msg = sys.argv[1] if len(sys.argv) > 1 else "update"

    ref = api(f"/repos/{REPO}/git/ref/heads/{BRANCH}", tok)
    head = ref["object"]["sha"]
    commit = api(f"/repos/{REPO}/git/commits/{head}", tok)
    base_tree = commit["tree"]["sha"]
    remote = {e["path"]: e["sha"] for e in api(
        f"/repos/{REPO}/git/trees/{base_tree}?recursive=1", tok)["tree"] if e["type"] == "blob"}

    changed = []
    local_paths = tracked()
    for path in local_paths:
        local = blob_sha(path)
        if remote.get(path) != local:
            changed.append((path, local))

    # 本地已不跟踪、但远端树里还留着的文件——必须显式删掉，否则会一直积累
    gone = [p for p in remote if p not in set(local_paths)]
    for p in gone:
        print(f"delete: {p}")

    if not changed and not gone:
        print("远端已是最新，无需推送")
        return

    entries = []
    for path, _ in changed:
        raw = open(os.path.join(ROOT, path), "rb").read()
        blob = api(f"/repos/{REPO}/git/blobs", tok, "POST", {
            "content": base64.b64encode(raw).decode(),
            "encoding": "base64",
        })
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print(f"blob  : {path}  ({len(raw)} bytes)")

    for p in gone:
        entries.append({"path": p, "mode": "100644", "type": "blob", "sha": None})

    tree = api(f"/repos/{REPO}/git/trees", tok, "POST",
               {"base_tree": base_tree, "tree": entries})
    new_commit = api(f"/repos/{REPO}/git/commits", tok, "POST",
                     {"message": msg, "tree": tree["sha"], "parents": [head]})
    api(f"/repos/{REPO}/git/refs/heads/{BRANCH}", tok, "PATCH",
        {"sha": new_commit["sha"], "force": False})
    print(f"commit: {new_commit['sha'][:12]}  {msg}")


if __name__ == "__main__":
    main()
