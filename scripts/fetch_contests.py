#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
3D 打印比赛聚合 - 家庭/云端独立更新脚本（不依赖 WorkBuddy，可无人值守）
替代 WorkBuddy WebFetch 依赖，在家用电脑 / GitHub Actions(无头环境) 中独立运行。

抓取策略：
- 纯 HTTP/API（无需浏览器）：creality-cn, creality-intl, makeroad, joykings3d(jhx3d),
  snapmaker(Next.js RSC 转义JSON正则提取)
- 需 Playwright 无头浏览器：makerworld-cn, makerworld-intl, jlc, nexprint, makeronline(客户端水合)

合并原则（增量、逐平台、不删旧数据）：
- 抓取失败(返回 None)的平台：该平台旧条目原样保留，绝不删除。
- 抓取成功的平台：旧条目与新抓列表两轮匹配（name→url，每个新条目只消费一次），
  匹配成功更新 desc/start/end/status（url 仅 name 匹配成功时覆盖）；
  旧条目未匹配且已过 end → 移除；end 未到 → 保留（防新列表不完整误删）。
- status=ended 的新条目不写入（已结束比赛不展示，与页面过滤逻辑一致）。

注意：
- 纵维立方(makeronline) 截止时间页面标注 "YYYY-MM-DD 00:00:00"，实际最后参赛日为前一天，
  该平台 end 统一 -1 天。
- 几何芯(joykings3d) 2026-09-05 起中文站迁移到 www.jhx3d.com（SSR），见 fetch_joykings3d()。
- 无 Playwright 时 makeronline/makerworld/jlc/nexprint 会跳过并保留旧数据（脚本仍可用）。

用法：
    python scripts/fetch_contests.py            # 抓取+合并+生成 HTML
    python scripts/fetch_contests.py --no-html  # 只更新 contests.json
"""

import json
import re
import sys
import os
import argparse
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


def ts_date(ts, unit="s", tz="local"):
    """秒级/毫秒级时间戳转 YYYY-MM-DD。
    tz: 'local'=按本机时区(东八区家用电脑)转换；'utc'=按 UTC 转换。
    创想云国内 API 时间戳为「中国时区整点」(start=00:00:00/end=23:59:59)，应传 local；
    创想云国际 API 时间戳为 UTC 整点，应传 utc（用 local 会把日期 +1 天）。"""
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return ""
    if unit == "ms":
        ts /= 1000.0
    try:
        if tz == "utc":
            dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        else:
            dt = datetime.datetime.fromtimestamp(ts)
        return dt.strftime("%Y-%m-%d")
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
        return "judging"
    if "报名" in t or "upcoming" in t or "未开始" in t or "即将" in t or "预告" in t:
        return "upcoming"
    return "ongoing"


# ----------------------------------------------------------------------------
# 各平台抓取器（返回 list[dict]，或 None 表示抓取失败）
# ----------------------------------------------------------------------------

def _creality_fetch(url, headers, body_base, tz="local"):
    """创想云列表通用抓取（带分页，activityType=9 模型设计比赛）。
    tz: 国内 API 传 local（中国时区整点），国际 API 传 utc。"""
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
            st = {1: "upcoming", 2: "ongoing", 6: "judging", 3: "ended"}.get(ac, "ongoing")
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
                "start": ts_date(a.get("startTime"), tz=tz),
                "end": ts_date(a.get("endTime"), tz=tz),
                "status": st,
                "url": u,
            })
        seen += len(lst)
        if not lst or (total is not None and seen >= total):
            break
        page += 1
    return out


def fetch_creality_cn():
    # 国内站：startTime/endTime 为中国时区整点 → 本地时区转换
    return _creality_fetch(
        "https://api.crealitycloud.cn/api/cxy/v2/allActivity/list",
        {"Origin": "https://m.crealitycloud.cn"},
        {"pageSize": 100, "lang": "zh"},
        tz="local")


def fetch_creality_intl():
    # 国际站：时间戳为 UTC 整点 → UTC 转换（用本地会 +1 天）
    return _creality_fetch(
        "https://www.crealitycloud.com/api/cxy/v2/allActivity/list",
        {
            "Origin": "https://www.crealitycloud.com",
            "Referer": "https://www.crealitycloud.com/zh/contest",
            "__CXY_PLATFORM_": "2",
            "__CXY_APP_ID_": "cxy-gen2",
            "__CXY_APP_VER_": "7.3.20",
        },
        {"pageSize": 100, "platforms": [2, 3], "status": 0},
        tz="utc")


def fetch_makeroad():
    h = {"Referer": "https://www.makeroad.com/zh/contests",
         "Accept": "application/json"}
    d = json.loads(http_get("https://www.makeroad.com/api/contest/list", h))
    out = []
    for r in d["data"]["list"]:
        # status: 3=进行中, 4=评审中(judging, 投稿截止未出结果), 5=已结束
        st = {3: "ongoing", 4: "judging", 5: "ended"}.get(r.get("status"), "ongoing")
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


def _extract_next_records(html):
    """从 Next.js RSC payload(self.__next_f.push) 中提取 records 数组。
    返回 list[dict] 或 None。"""
    m = None
    for pm in re.finditer(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.S):
        if '"competitionId"' in pm.group(1) or 'records' in pm.group(1):
            m = pm
            break
    if not m:
        return None
    raw = m.group(1)
    # 还原 JS 字符串字面量的最外层转义
    out_chars = []
    i = 0
    while i < len(raw):
        c = raw[i]
        if c == "\\" and i + 1 < len(raw):
            n = raw[i + 1]
            if n == '"':
                out_chars.append('"'); i += 2; continue
            elif n == "\\":
                out_chars.append("\\"); i += 2; continue
            elif n == "n":
                out_chars.append("\n"); i += 2; continue
            elif n == "t":
                out_chars.append("\t"); i += 2; continue
            elif n == "/":
                out_chars.append("/"); i += 2; continue
            elif n == "u":
                out_chars.append(raw[i:i + 6]); i += 6; continue
            else:
                out_chars.append(c); i += 1; continue
        out_chars.append(c); i += 1
    inner = "".join(out_chars)
    idx = inner.find('"records":[')
    if idx < 0:
        return None
    start = inner.find("[", idx)
    depth = 0
    end = None
    for i in range(start, len(inner)):
        if inner[i] == "[":
            depth += 1
        elif inner[i] == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        return None
    seg = inner[start:end]
    seg = re.sub(r'\\u([0-9a-fA-F]{4})',
                 lambda x: chr(int(x.group(1), 16)), seg)
    try:
        return json.loads(seg)
    except Exception:
        return None


def fetch_joykings3d():
    """几何芯中文站 2026-09-05 起迁移至 www.jhx3d.com（joykings3d.com 只剩英文站，旧 API 全 404）。
    列表页为 Next.js SSR，比赛数据嵌在 self.__next_f.push 的 records 数组中。"""
    html = http_get(
        "https://www.jhx3d.com/activitys/list",
        {"Accept-Language": "zh-CN,zh;q=0.9"})
    records = _extract_next_records(html)
    if records is None:
        return None
    out = []
    for r in records:
        ms = r.get("miniStatus")
        if ms in ("ended", "finished"):
            continue  # 已结束不写入
        st = {
            "in_progress": "ongoing",
            "not_started": "upcoming",
            "upcoming": "upcoming",
            "ended": "ended",
            "finished": "ended",
            "review": "judging",
            "reviewing": "judging",
        }.get(ms)
        if st is None:
            st = status_by_date(str_date(r.get("regStartTime")),
                                str_date(r.get("regEndTime")))
        cid = r.get("competitionId")
        out.append({
            "name": " ".join((r.get("title") or "").replace("\\n", " ").replace("\n", " ").split()),
            "desc": " ".join((r.get("subtitle") or "").split()),
            "start": str_date(r.get("regStartTime")),
            "end": str_date(r.get("regEndTime")),
            "status": st,
            "url": f"https://www.jhx3d.com/activitys/detail/{cid}",
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
    # 状态优先用页面时间线判断：比赛提交已截止但处于评审期 → judging
    st = _snapmaker_status(html, start, end)
    return [{
        "name": name,
        "desc": strip_html(pairs.get("contestDesc", "")),
        "start": start,
        "end": end,
        "status": st,
        "url": f"https://models.snapmaker.com/contest/{pid}",
    }]


def _snapmaker_status(html, sub_start, sub_end):
    """按页面 Timeline 文本判断快造比赛状态：
    Submission 截止后进入 Review 期(judging)，Winners 公布后才是 ended。
    返回 ongoing / judging / upcoming / ended 之一。"""
    def to_date(mon, day, year):
        try:
            return datetime.datetime.strptime(f"{mon} {day} {year}", "%b %d %Y").date()
        except ValueError:
            return None

    today = datetime.date.today()
    win_date = None
    m_win = re.search(r"Winners Announced:\s*([A-Z][a-z]{2})\s+(\d{1,2})"
                      r"(?:st|nd|rd|th)?,?\s+(\d{4})", html)
    if m_win:
        win_date = to_date(m_win.group(1), m_win.group(2), m_win.group(3))
        if win_date and today > win_date:
            return "ended"  # 已公布结果 → 已结束
    # 评审窗口判定：找到 Review 段起止
    m_rev = re.search(r"Review:\s*([A-Z][a-z]{2})\s+(\d{1,2})"
                      r"(?:st|nd|rd|th)?\s+to\s+([A-Z][a-z]{2})\s+(\d{1,2}),?\s+(\d{4})", html)
    if m_rev:
        d1 = to_date(m_rev.group(1), m_rev.group(2), m_rev.group(5))
        d2 = to_date(m_rev.group(3), m_rev.group(4), m_rev.group(5))
        if d1 and d2 and d1 <= today <= d2:
            return "judging"
    # 提交已截止且结果未公布 → 评审中
    try:
        e = datetime.date.fromisoformat(sub_end) if sub_end else None
        s = datetime.date.fromisoformat(sub_start) if sub_start else None
    except ValueError:
        e = s = None
    if e and today > e and not (win_date and today > win_date):
        return "judging"
    if s and today < s:
        return "upcoming"
    return "ongoing"


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

def _norm_name(s):
    """归一化比赛名用于匹配：去除空白、统一中间点（·•）两侧空格。"""
    s = (s or "").strip()
    s = re.sub(r"\s*([·•・·])\s*", r"\1", s)
    return "".join(s.split()).lower()


def _match_and_consume(oc, fresh, used):
    """两轮匹配：第一轮按 name、第二轮按 url；每个新条目只消费一次。
    返回 (fresh_item, matched_by) 或 (None, None)。"""
    for i, fc in enumerate(fresh):
        if (not used[i] and oc.get("name") and fc.get("name")
                and _norm_name(oc["name"]) == _norm_name(fc["name"])):
            used[i] = True
            return fc, "name"
    for i, fc in enumerate(fresh):
        if (not used[i] and oc.get("url") and fc.get("url")
                and _norm_name(oc["url"]) == _norm_name(fc["url"])):
            used[i] = True
            return fc, "url"
    return None, None


def merge(existing, fresh_blocks, today=None):
    """
    逐平台增量合并（与 WorkBuddy AI 版 update_contests_*.py 语义一致）：
    - 抓取失败(返回 None/空)的平台：旧条目原样保留（防 WebFetch/网络不完整误删）；
    - 抓取成功的平台：旧条目与新列表两轮匹配(name→url，每个新条目只消费一次)，
      匹配成功则更新字段；旧条目未匹配且 end < today → 移除（已结束/下架）；
      end 未到但未匹配 → 保留（防新抓列表不完整误删）；
      新列表未被消费的条目 → 追加。
    - url 仅在 name 匹配成功时允许覆盖（预告转正式/域名迁移场景）；
      url 兜底匹配不覆盖 url（防同 URL 平台串改，如 nexprint 共用主站 URL）。
    - status=ended 的新条目不写入（已结束比赛不显示）。
    """
    today = today or datetime.date.today()
    existing_by_site = {}
    for c in existing:
        existing_by_site.setdefault(c.get("site"), []).append(c)

    result = []
    replaced = set(fresh_blocks.keys())
    # 1) 未被抓取/抓取失败平台：原样保留
    for c in existing:
        if c.get("site") not in replaced:
            result.append(c)
    # 2) 抓取成功的平台：增量合并
    for site, fresh in fresh_blocks.items():
        # 过滤已结束的新条目
        fresh_active = [f for f in fresh if f.get("status") != "ended"]
        old_items = existing_by_site.get(site, [])
        used = [False] * len(fresh_active)
        site_out = []
        for oc in old_items:
            fc, matched_by = _match_and_consume(oc, fresh_active, used)
            if fc:
                # 仅当新值非空才覆盖；url 只在 name 匹配成功时覆盖
                for k, v in fc.items():
                    if k in ("name", "site"):
                        continue
                    if k == "url":
                        if matched_by == "name" and v and oc.get("url") != v:
                            oc[k] = v
                        continue
                    if v is not None and oc.get(k) != v:
                        oc[k] = v
                site_out.append(oc)
            else:
                end = oc.get("end") or ""
                try:
                    ended = bool(end) and datetime.date.fromisoformat(end) < today
                except ValueError:
                    ended = False
                # 旧条目在完整列表中找不到 → 仅当确实已过截止才移除，否则保守保留
                if oc.get("status") == "ended" or ended:
                    continue
                site_out.append(oc)
        for i, fc in enumerate(fresh_active):
            if not used[i]:
                item = dict(fc)
                item["site"] = site
                site_out.append(item)
        result.extend(site_out)
    return result


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-html", action="store_true", help="只更新 contests.json，不重新生成 HTML")
    args = ap.parse_args()

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

    # 重新生成 HTML（除非 --no-html）
    if not args.no_html:
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
