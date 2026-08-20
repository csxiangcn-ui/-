#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
3D 打印比赛聚合 - 云端独立抓取脚本
替代 WorkBuddy WebFetch 依赖，可在 GitHub Actions(无头环境) 中独立运行。

抓取策略：
- 纯 HTTP/API（无需浏览器）：creality-cn, creality-intl, makeroad, joykings3d,
  makeronline(Nuxt 内嵌JSON经 node 求值), snapmaker(Next.js RSC 转义JSON正则提取)
- 需 Playwright 无头浏览器：makerworld-cn, makerworld-intl, jlc, nexprint(客户端水合)

合并原则（增量、不删旧数据）：
- 以 (site, name) 为键，旧数据优先保留；
- 新抓到且同名的比赛，更新其 desc/start/end/status/url；
- 新抓到且名字不存在的比赛，新增；
- 某平台抓取失败(返回 None) 时，该平台旧数据原样保留，绝不删除。

注意：
- 纵维立方(makeronline) 截止时间页面标注 "YYYY-MM-DD 00:00:00"，实际最后参赛日为前一天，
  该平台 end 统一 -1 天。
- 已结束(ended) 比赛仍写入 contests.json（与历史数据保持一致），由 generate_page.py 页面端过滤。
"""

import json
import re
import sys
import os
import subprocess
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
CONTESTS = PROJECT / "contests.json"
NODE_BIN = os.environ.get("NODE_BIN") or "node"

# ----------------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------------

def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def http_get(url, headers=None, timeout=25):
    import urllib.request
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def http_post(url, body, headers=None, timeout=25):
    import urllib.request
    data = json.dumps(body).encode("utf-8")
    h = {"Content-Type": "application/json",
         "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def node_eval(expr):
    """把一段 JS 表达式交给 node 求值并返回 Python 对象（写文件避免参数过长）。"""
    runner = ROOT / ".node_eval_runner.js"
    exprfile = ROOT / ".node_eval_expr.js"
    runner.write_text(
        "const fs=require('fs');"
        "const e=fs.readFileSync(process.argv[1],'utf8');"
        "process.stdout.write(JSON.stringify(eval(e)));",
        encoding="utf-8")
    exprfile.write_text(expr, encoding="utf-8")
    try:
        out = subprocess.run([NODE_BIN, "--stack-size=40000", runner.name, exprfile.name],
                             cwd=ROOT, capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            raise RuntimeError(out.stderr[:300])
        return json.loads(out.stdout)
    finally:
        for f in (runner, exprfile):
            if f.exists():
                f.unlink()


def strip_html(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def ts_date(ts, unit="s"):
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return ""
    if unit == "ms":
        ts /= 1000.0
    try:
        return datetime.datetime.fromtimestamp(
            ts, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OSError):
        return ""


def str_date(s):
    if not s:
        return ""
    s = s.strip().replace("T", " ").replace("Z", "")
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else ""


def minus1(dstr):
    if not dstr:
        return dstr
    try:
        dt = datetime.date.fromisoformat(dstr)
        return (dt - datetime.timedelta(days=1)).isoformat()
    except ValueError:
        return dstr


def status_by_date(start, end):
    try:
        today = datetime.date.today()
        s = datetime.date.fromisoformat(start) if start else None
        e = datetime.date.fromisoformat(end) if end else None
        if e and e < today:
            return "ended"
        if s and s > today:
            return "upcoming"
        return "ongoing"
    except ValueError:
        return "ongoing"


def status_from_text(t):
    t = (t or "").lower()
    if "已结束" in t or "ended" in t or "关闭" in t:
        return "ended"
    if "评审" in t or "review" in t or "judg" in t:
        return "reviewing"
    if "报名" in t or "upcoming" in t or "未开始" in t or "即将" in t or "预告" in t:
        return "upcoming"
    return "ongoing"


# ----------------------------------------------------------------------------
# 各平台抓取器（返回 list[dict]，或 None 表示抓取失败）
# ----------------------------------------------------------------------------

def _creality_fetch(url, headers, body_base):
    """创想云列表通用抓取（带分页，activityType=9 模型设计比赛）。"""
    out = []
    seen = 0
    total = None
    page = 1
    while page <= 10:
        body = dict(body_base)
        body["page"] = page
        d = json.loads(http_post(url, body, headers))
        result = d.get("result", {})
        total = result.get("count", 0)
        lst = result.get("list", [])
        for a in lst:
            if a.get("activityType") != 9:
                continue
            ac = a.get("acStatus")
            st = {1: "upcoming", 2: "ongoing", 6: "reviewing", 3: "ended"}.get(ac, "ongoing")
            if st == "ended":
                # 跳过已结束：创想云 API 会返回海量历史结束比赛，已结束不展示，
                # 历史 ended 由 merge 沿用旧数据保留，避免 JSON 被历史数据撑爆。
                continue
            lu = a.get("linkUrl", "")
            u = ""
            if isinstance(lu, str) and lu.startswith("{"):
                try:
                    u = json.loads(lu).get("url", "")
                except Exception:
                    u = ""
            if u.startswith("http://"):
                u = u.replace("http://", "https://", 1)
            u = u.replace("m.crealitycloud.com", "www.crealitycloud.com")
            if not u:
                u = "https://www.crealitycloud.com/contest"
            out.append({
                "name": (a.get("name") or "").strip(),
                "desc": strip_html(a.get("desc", "")),
                "start": ts_date(a.get("startTime")),
                "end": ts_date(a.get("endTime")),
                "status": st,
                "url": u,
            })
        seen += len(lst)
        if not lst or (total is not None and seen >= total):
            break
        page += 1
    return out


def fetch_creality_cn():
    return _creality_fetch(
        "https://api.crealitycloud.cn/api/cxy/v2/allActivity/list",
        {"Origin": "https://m.crealitycloud.cn"},
        {"pageSize": 100, "lang": "zh"})


def fetch_creality_intl():
    return _creality_fetch(
        "https://www.crealitycloud.com/api/cxy/v2/allActivity/list",
        {
            "Origin": "https://www.crealitycloud.com",
            "Referer": "https://www.crealitycloud.com/zh/contest",
            "__CXY_PLATFORM_": "2",
            "__CXY_APP_ID_": "cxy-gen2",
            "__CXY_APP_VER_": "7.3.20",
        },
        {"pageSize": 100, "platforms": [2, 3], "status": 0})


def fetch_makeroad():
    h = {"Referer": "https://www.makeroad.com/zh/contests",
         "Accept": "application/json"}
    d = json.loads(http_get("https://www.makeroad.com/api/contest/list", h))
    out = []
    for r in d["data"]["list"]:
        st = {3: "ongoing", 5: "ended"}.get(r.get("status"), "ongoing")
        if st == "ended":
            continue  # 跳过已结束，历史 ended 由 merge 保留
        out.append({
            "name": r.get("name", ""),
            "desc": strip_html(r.get("content", "")),
            "start": ts_date(r.get("gameTimeStart")),
            "end": ts_date(r.get("gameTimeEnd")),
            "status": st,
            "url": f"https://www.makeroad.com/contests/{r.get('id')}",
        })
    return out


def fetch_joykings3d():
    h = {"Origin": "https://www.joykings3d.com"}
    d = json.loads(http_post(
        "https://www.joykings3d.com/api/web/competition/page",
        {"page": 1, "pageSize": 100}, h))
    out = []
    for r in d["data"]["records"]:
        ms = r.get("miniStatus")
        st = {
            "in_progress": "ongoing",
            "not_started": "upcoming",
            "ended": "ended",
            "finished": "ended",
            "review": "reviewing",
            "reviewing": "reviewing",
        }.get(ms)
        if st is None:
            st = status_by_date(str_date(r.get("regStartTime")),
                                str_date(r.get("regEndTime")))
        out.append({
            "name": (r.get("title") or "").replace("\\n", " ").replace("\n", " ").strip(),
            "desc": (r.get("subtitle") or "").strip(),
            "start": str_date(r.get("regStartTime")),
            "end": str_date(r.get("regEndTime")),
            "status": st,
            "url": f"https://www.joykings3d.com/activitys/detail/{r.get('competitionId')}",
        })
    return out


def fetch_makeronline():
    # makeronline 的 SSR 数据使用 Nuxt devalue 变量引用，无法纯正则/求值解析，
    # 改用 Playwright 渲染后读取 DOM 卡片。
    return _playwright_run(
        "makeronline",
        "https://www.makeronline.com/zh/contestList",
        _makeronline_parse)


def _snapmaker_name_lookup(pid):
    """snapmaker 比赛对象无独立 name 字段（contestTitle 恒为 'Contests'），
    改为从现有 contests.json 按 /contest/{pid} 匹配 URL 找回名称。"""
    try:
        data = json.loads(CONTESTS.read_text(encoding="utf-8"))
    except Exception:
        return ""
    for c in data.get("contests", []):
        if c.get("site") == "snapmaker" and f"/contest/{pid}" in (c.get("url") or ""):
            return c.get("name") or ""
    return ""


def fetch_snapmaker_page(pid):
    html = http_get(f"https://models.snapmaker.com/contest/{pid}")
    i = html.find("contestStartAt")
    if i < 0:
        return None
    window = html[max(0, i - 4000): i + 400]
    pairs = dict(re.findall(r'\\"([a-zA-Z]+)\\":\\"([^\\"\\]+)\\"', window))
    # snapmaker 比赛对象无独立 name 字段，contestTitle 恒为 "Contests"，
    # 改为从现有 contests.json 按 /contest/{pid} 匹配 URL 找回名称。
    name = _snapmaker_name_lookup(pid) or f"Snapmaker Contest {pid}"
    start = str_date(pairs.get("contestStartAt"))
    end = str_date(pairs.get("contestEndAt"))
    st = status_from_text(pairs.get("statusDesc", ""))
    if st == "ongoing" and start and end:
        st = status_by_date(start, end)
    return [{
        "name": name,
        "desc": strip_html(pairs.get("contestDesc", "")),
        "start": start,
        "end": end,
        "status": st,
        "url": f"https://models.snapmaker.com/contest/{pid}",
    }]


# ---- Playwright 组（需浏览器） ----

def _playwright_run(site, url, parse_fn):
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        print(f"[skip] {site}: 未安装 playwright，保留旧数据")
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page(locale="zh-CN")
            page.goto(url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3500)
            data = parse_fn(page)
            browser.close()
            return data
    except Exception as e:
        print(f"[err] {site}: {e}")
        return None


def _mw_parse(page):
    page.wait_for_selector('a[href*="/contests/"]', timeout=30000)
    cards = page.eval_on_selector_all(
        'a[href*="/contests/"]',
        "els => els.map(e => {"
        "  const t = (e.innerText || '').replace(/\\s+/g, ' ').trim();"
        "  const dates = (t.match(/\\d{4}-\\d{2}-\\d{2}/g) || []);"
        "  return {url: e.href, text: t, dates};"
        "})")
    seen = set()
    out = []
    for c in cards:
        url = c.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        text = c.get("text", "")
        dates = c.get("dates") or []
        name = text.split("  ")[0].strip() or text.strip()[:40]
        start = dates[0] if dates else ""
        end = dates[1] if len(dates) > 1 else (dates[0] if dates else "")
        st = status_from_text(text)
        if st == "ongoing" and start and end:
            st = status_by_date(start, end)
        out.append({
            "name": name,
            "desc": "",
            "start": start,
            "end": end,
            "status": st,
            "url": url,
        })
    return out


def _jlc_parse(page):
    page.wait_for_selector(".main-race, .race-card, a[href*='/race']", timeout=30000)
    cards = page.eval_on_selector_all(
        "a[href*='/race']",
        "els => els.map(e => {"
        "  const t = (e.innerText || '').replace(/\\s+/g, ' ').trim();"
        "  const dates = (t.match(/\\d{4}[-/]\\d{2}[-/]\\d{2}/g) || []);"
        "  return {url: e.href, text: t, dates};"
        "})")
    seen = set()
    out = []
    for c in cards:
        url = c.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        text = c.get("text", "")
        dates = c.get("dates") or []
        name = text.split("  ")[0].strip() or text.strip()[:40]
        start = dates[0] if dates else ""
        end = dates[1] if len(dates) > 1 else (dates[0] if dates else "")
        st = status_from_text(text)
        if st == "ongoing" and start and end:
            st = status_by_date(start, end)
        out.append({
            "name": name,
            "desc": "",
            "start": str_date(start),
            "end": str_date(end),
            "status": st,
            "url": url,
        })
    return out


def _nexprint_parse(page):
    data = page.evaluate("() => (window.__NUXT__ || {})")
    arr = None

    def find(o, key):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == key and isinstance(v, list) and v and isinstance(v[0], dict):
                    return v
                r = find(v, key)
                if r:
                    return r
        elif isinstance(o, list):
            for it in o:
                r = find(it, key)
                if r:
                    return r
        return None

    arr = find(data, "activityModelList")
    if not arr:
        return None
    out = []
    for a in arr:
        name = a.get("title") or a.get("activityName") or ""
        desc = a.get("description") or a.get("desc") or ""
        start = ts_date(a.get("startTime"), "ms") or str_date(a.get("startTime"))
        end = ts_date(a.get("endTime"), "ms") or str_date(a.get("endTime"))
        st = status_from_text(a.get("statusText") or a.get("status") or "")
        if st == "ongoing" and start and end:
            st = status_by_date(start, end)
        out.append({
            "name": name,
            "desc": strip_html(desc),
            "start": start,
            "end": end,
            "status": st,
            "url": "https://www.nexprint.com/contests/" + str(a.get("pathName", "")),
        })
    return out


def _makeronline_parse(page):
    page.wait_for_selector('a[href*="/contest/"]', timeout=30000)
    cards = page.eval_on_selector_all(
        'a[href*="/contest/"]',
        "els => els.map(e => ({url: e.href, text: (e.innerText || '').trim()}))")
    seen = set()
    out = []
    for c in cards:
        url = c.get("url", "")
        if not url or url in seen or "contestList" in url or not url.endswith(".html"):
            continue
        seen.add(url)
        text = c.get("text", "")
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        name = lines[0] if lines else ""
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", text)
        start = dates[0] if dates else ""
        raw_end = dates[1] if len(dates) > 1 else (dates[0] if dates else "")
        end = minus1(raw_end)  # 纵维立方规则：end -1 天
        st = status_from_text(text)
        if st == "ongoing" and start and end:
            st = status_by_date(start, end)
        out.append({
            "name": name,
            "desc": "",
            "start": start,
            "end": end,
            "status": st,
            "url": url,
        })
    return out


# ----------------------------------------------------------------------------
# 合并
# ----------------------------------------------------------------------------

def _key(s):
    """合并去重用的归一化键：去除中间点(·•)两侧空格，避免同场比赛因格式差异被判为两条。"""
    s = (s or "").strip()
    s = re.sub(r"\s*([·•・·])\s*", r"\1", s)
    return s


def merge(existing, fresh_blocks):
    """
    按平台整体替换式合并（增量、不丢历史）：
    - 抓取失败(返回 None)的平台：旧块原样保留；
    - 抓取成功的平台：写入新抓到的全部条目(含已结束)，
      并额外保留"旧数据中已结束、且新抓取未返回"的历史条目（避免丢失已结束历史）。
    归一化键(_key)用于匹配，避免同一比赛因名称里 · 两侧空格不同被判重。
    """
    existing_by_site = {}
    for c in existing:
        existing_by_site.setdefault(c.get("site"), []).append(c)

    result = []
    replaced = set(fresh_blocks.keys())
    # 1) 未被替换的平台：原样保留
    for c in existing:
        if c.get("site") not in replaced:
            result.append(c)
    # 2) 被替换的平台：写新数据(全部) + 旧 ended 历史(未在新数据中出现的)
    for site, items in fresh_blocks.items():
        fresh_keys = {_key(it["name"]) for it in items}
        for it in items:
            it["site"] = site
            result.append(it)  # 写入全部新数据（含 ended）
        for c in existing_by_site.get(site, []):
            if c.get("status") == "ended" and _key(c.get("name")) not in fresh_keys:
                result.append(c)  # 保留旧 ended 历史
    return result


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------

def main():
    SITES = {
        "creality-cn": fetch_creality_cn,
        "creality-intl": fetch_creality_intl,
        "makeroad": fetch_makeroad,
        "joykings3d": fetch_joykings3d,
        "makeronline": fetch_makeronline,
        "snapmaker": lambda: _combine_snapmaker(),
        "makerworld-cn": lambda: _playwright_run(
            "makerworld-cn", "https://makerworld.com.cn/zh/contests", _mw_parse),
        "makerworld-intl": lambda: _playwright_run(
            "makerworld-intl", "https://makerworld.com/zh/contests", _mw_parse),
        "jlc": lambda: _playwright_run(
            "jlc", "https://model.jlc-3dp.cn/race", _jlc_parse),
        "nexprint": lambda: _playwright_run(
            "nexprint", "https://www.nexprint.com/zh/contests", _nexprint_parse),
    }

    data = json.loads(CONTESTS.read_text(encoding="utf-8"))
    existing = data.get("contests", [])

    fresh_blocks = {}
    for site, fn in SITES.items():
        try:
            items = fn()
        except Exception as e:
            print(f"[err] {site}: {e}")
            items = None
        if not items:
            print(f"[skip] {site}: 无新数据，保留旧条目")
            continue
        active = sum(1 for it in items if it.get("status") != "ended")
        print(f"[ok] {site}: 抓到 {len(items)} 条（活跃 {active}）")
        fresh_blocks[site] = items

    merged = merge(existing, fresh_blocks)
    data["contests"] = merged
    data["last_update"] = now_str()
    CONTESTS.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    # 重新生成 HTML
    try:
        subprocess.run([sys.executable, str(PROJECT / "generate_page.py")],
                       cwd=PROJECT, check=True)
    except Exception as e:
        print(f"[warn] 生成 HTML 失败: {e}")

    total = len(merged)
    active = sum(1 for c in merged if c.get("status") != "ended")
    print(f"完成：总计 {total} 条，活跃 {active} 条，last_update={data['last_update']}")


def _combine_snapmaker():
    # 逐 pid 抓取；单个 pid 失败则保留该 pid 的旧数据，不整体丢弃平台。
    existing = []
    try:
        existing = json.loads(CONTESTS.read_text(encoding="utf-8")).get("contests", [])
    except Exception:
        existing = []
    by_pid = {}
    for c in existing:
        if c.get("site") == "snapmaker":
            m = re.search(r"/contest/(\d+)", c.get("url", ""))
            if m:
                by_pid[int(m.group(1))] = c
    out = []
    any_ok = False
    for pid in (1, 2):
        r = fetch_snapmaker_page(pid)
        entry = r[0] if (r and len(r) > 0) else None
        if entry and entry.get("name") and entry["name"] != f"Snapmaker Contest {pid}":
            out.append(entry)
            any_ok = True
        elif pid in by_pid:
            out.append(by_pid[pid])
            any_ok = True
    return out if any_ok else None


if __name__ == "__main__":
    main()
