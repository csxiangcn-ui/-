#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""带代理自愈的 git push / pull。

背景：本机通过本地代理访问 GitHub，但环境变量里的代理地址（由运行宿主注入）
会变化且偶尔失效。表现为 git push 报 502 / 连接超时，提交滞留在本地。

策略：
  1. 先用当前环境原样执行；
  2. 失败则逐个探测候选代理端口（环境变量里的 + Clash Verge 常见端口），
     探测通了就用 `git -c http.proxy=<p>` 重试；
  3. 最后再试一次完全直连；
  4. 任一成功即返回 0。

用法：
  python scripts/git_push.py push
  python scripts/git_push.py pull
"""
import os
import subprocess
import sys
import urllib.request

PROBE_URL = "https://github.com"
PROBE_TIMEOUT = 12

# 常见本地代理端口：Clash Verge 7897 / Clash 7890,7891 / v2ray 10808,10809
COMMON_PORTS = [7897, 7890, 7891, 10808, 10809, 2080]


def _candidate_proxies():
    out = []
    for key in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        v = os.environ.get(key, "").strip()
        if not v:
            continue
        if not v.startswith("http"):
            continue
        if v not in out:
            out.append(v)
    for port in COMMON_PORTS:
        v = f"http://127.0.0.1:{port}"
        if v not in out:
            out.append(v)
    return out


def _probe(proxy):
    """经代理访问 github，判断该代理是否可用。"""
    handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    opener = urllib.request.build_opener(handler)
    try:
        with opener.open(PROBE_URL, timeout=PROBE_TIMEOUT) as r:
            return 200 <= r.status < 400
    except Exception:
        return False


def _run_git(args, timeout=180):
    try:
        p = subprocess.run(["git"] + args, capture_output=True, text=True,
                           timeout=timeout, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    except subprocess.TimeoutExpired:
        print(f"  [git {' '.join(args)}] 超时")
        return 1, ""
    msg = (p.stdout or "") + (p.stderr or "")
    msg = msg.strip()
    if msg:
        for line in msg.splitlines()[-4:]:
            print("  " + line)
    return p.returncode, msg


def _env_without_proxy():
    env = dict(os.environ)
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        env.pop(k, None)
    return env


def _run_git_via(proxy, args, timeout=180):
    cmd = ["git"]
    if proxy:
        cmd += ["-c", f"http.proxy={proxy}", "-c", f"https.proxy={proxy}"]
    cmd += args
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           env=_env_without_proxy())
    except subprocess.TimeoutExpired:
        print(f"  [{' '.join(args)} via {proxy or 'direct'}] 超时")
        return 1
    msg = ((p.stdout or "") + (p.stderr or "")).strip()
    if msg:
        for line in msg.splitlines()[-4:]:
            print("  " + line)
    return p.returncode


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "push"
    if action == "push":
        args = ["push", "origin", "HEAD:main"]
    elif action == "pull":
        # --autostash：本地有未提交的改动时自动暂存，避免 "cannot pull with rebase"
        args = ["pull", "--rebase", "--autostash"]
    else:
        print(f"未知动作: {action}")
        return 2

    print(f"== git {action}：先按当前环境尝试 ==")
    if _run_git_via(None, args) == 0:
        print(f"== git {action} 成功 ==")
        return 0

    print(f"== 当前环境不通，开始探测可用代理 ==")
    for proxy in _candidate_proxies():
        if _probe(proxy):
            print(f"  代理可用: {proxy}，重试 git {action}")
            if _run_git_via(proxy, args) == 0:
                print(f"== git {action} 成功（经 {proxy}） ==")
                return 0
        else:
            print(f"  代理不可用: {proxy}")

    print(f"== 最后尝试直连 ==")
    if _run_git_via("", args) == 0:
        print(f"== git {action} 成功（直连） ==")
        return 0

    print(f"!! git {action} 全部失败，请检查网络/代理后重试")
    return 1


if __name__ == "__main__":
    sys.exit(main())
