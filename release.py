#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
打包发布物并（可选）发到 GitHub Releases。

    python release.py                # 只打 zip + 打印 sha256
    python release.py --publish      # 打 zip 并创建 release、上传资产

发布需要环境变量 GH_TOKEN（classic PAT 勾 repo，或 fine-grained PAT 勾
目标仓库的 Contents: Read/Write + Metadata: Read）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = "GitHubJackLiu/aimax"


def app_version() -> str:
    with open(os.path.join(ROOT, "aimax.py"), encoding="utf-8") as f:
        m = re.search(r'^VERSION\s*=\s*"([^"]+)"', f.read(), re.M)
    if not m:
        raise SystemExit("aimax.py 里找不到 VERSION")
    return m.group(1)


def build_zip(ver: str) -> tuple[str, str]:
    # dist/AIMAX.exe 可能正被运行中的旧版本占用，所以也接受 dist_new/ 里的产物；
    # 两个都在时取 mtime 更新的那个，避免把旧 exe 打进新版本包里。
    cands = [p for p in (os.path.join(ROOT, "dist", "AIMAX.exe"),
                         os.path.join(ROOT, "dist_new", "AIMAX.exe"))
             if os.path.exists(p)]
    if not cands:
        raise SystemExit("先构建：python -m PyInstaller AIMAX.spec --noconfirm")
    exe = max(cands, key=os.path.getmtime)
    print(f"exe     : {exe}  ({os.path.getsize(exe)} bytes)")
    out = os.path.join(ROOT, "dist_new", f"AIMAX-v{ver}-win64.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.write(exe, "AIMAX.exe")
    h = hashlib.sha256()
    with open(out, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return out, h.hexdigest()


def api(path: str, token: str, method: str = "GET", payload=None, raw: bytes | None = None,
        ctype: str = "application/json"):
    url = path if path.startswith("http") else f"https://api.github.com{path}"
    data = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": ctype,
        "User-Agent": "aimax-release",
    })
    with urllib.request.urlopen(req, timeout=120) as r:
        body = r.read()
    return json.loads(body.decode("utf-8", "replace")) if body else {}


def notes_for(ver: str) -> str:
    path = os.path.join(ROOT, "CHANGELOG.md")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return f"v{ver}"
    m = re.search(rf"^##\s*v{re.escape(ver)}\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    return (m.group(1).strip() if m else f"v{ver}")


def publish(ver: str, zip_path: str) -> None:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        raise SystemExit("缺少 GH_TOKEN")

    tag = f"v{ver}"
    rel = api(f"/repos/{REPO}/releases", token, "POST", {
        "tag_name": tag,
        "name": f"AI MAX {tag}",
        "body": notes_for(ver),
        "draft": False,
        "prerelease": False,
    })
    print(f"release: {rel.get('html_url')}")

    name = os.path.basename(zip_path)
    with open(zip_path, "rb") as f:
        blob = f.read()
    up = api(f"https://uploads.github.com/repos/{REPO}/releases/{rel['id']}/assets?name={name}",
             token, "POST", raw=blob, ctype="application/zip")
    print(f"asset: {up.get('browser_download_url')}  ({up.get('size')} bytes)")


def main() -> None:
    ver = app_version()
    zip_path, sha = build_zip(ver)
    print(f"version : {ver}")
    print(f"package : {zip_path}")
    print(f"sha256  : {sha}")
    if "--publish" in sys.argv:
        publish(ver, zip_path)


if __name__ == "__main__":
    main()
