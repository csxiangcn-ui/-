#!/usr/bin/env python3
"""
3D 模型比赛页面生成器
- 读取 contests.json
- 自动判断比赛状态（基于当前日期 vs 开始/结束日期）
- 生成 3d-contests.html（含完整样式与脚本）

用法:
    python generate_page.py              # 生成 3d-contests.html
    python generate_page.py --open       # 生成后自动在浏览器打开
    python generate_page.py --status     # 仅显示统计

数据更新方式:
    1) 直接编辑 contests.json 后运行本脚本
    2) 或运行 update_data.py 自动从各平台抓取最新数据
"""
import json
import argparse
import datetime
import sys
import webbrowser
from pathlib import Path

HERE = Path(__file__).parent
JSON_PATH = HERE / "contests.json"
HTML_PATH = HERE / "3d-contests.html"

STATUS_LABEL = {
    "ongoing":  "进行中",
    "upcoming": "待开始",
    "judging":  "评审中",
    "ended":    "已结束",
}

SITES = {
    "creality-cn":      {"name": "创想云国内",          "url": "https://www.crealitycloud.cn/contest",      "color": "#FF6B35"},
    "creality-intl":    {"name": "创想云国际",          "url": "https://www.crealitycloud.com/zh/contest",   "color": "#FF8C42"},
    "makeronline":      {"name": "纵维立方",            "url": "https://www.makeronline.com/zh/contestList", "color": "#4ECDC4"},
    "makerworld-cn":    {"name": "拓竹国内",            "url": "https://makerworld.com.cn/zh/contests",       "color": "#FF6F61"},
    "makerworld-intl":  {"name": "拓竹国际",            "url": "https://makerworld.com/zh/contests",          "color": "#E55B5B"},
    "jlc":              {"name": "嘉立创",              "url": "https://model.jlc-3dp.cn/race",               "color": "#5B9BD5"},
    "makeroad":         {"name": "三绿 (MakerRoad)",    "url": "https://www.makeroad.com/zh/contests",         "color": "#52C41A"},
    "snapmaker":        {"name": "快造 (Snapmaker)",    "url": "https://models.snapmaker.com/contest",         "color": "#722ED1"},
    "nexprint":         {"name": "爱乐酷 (Nexprint)",   "url": "https://www.nexprint.com/zh/contests",        "color": "#EB2F96"},
    "joykings3d":       {"name": "几何芯 (JoyKings3D)", "url": "https://www.jhx3d.com/activitys/list",   "color": "#00B8A9"},
}


def auto_status(c: dict, today: datetime.date) -> str:
    """根据当前日期自动判断 status；保留用户已标注的 judging/ended"""
    if c.get("status") in ("judging", "ended"):
        return c["status"]
    try:
        start = datetime.date.fromisoformat(c["start"])
        end = datetime.date.fromisoformat(c["end"])
    except Exception:
        return c.get("status", "ongoing")
    if today < start:
        return "upcoming"
    if today > end:
        return "ended"
    return "ongoing"


TEMPLATE_PATH = HERE / "template.html"


def build_html(data: dict) -> str:
    today = datetime.date.today()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    contests = []
    for c in data["contests"]:
        c2 = dict(c)
        c2["status"] = auto_status(c, today)
        if c2["status"] != "ended":  # skip ended contests
            contests.append(c2)

    sites_json = json.dumps(SITES, ensure_ascii=False)
    contests_json = json.dumps(contests, ensure_ascii=False)
    label_json = json.dumps(STATUS_LABEL, ensure_ascii=False)

    if not TEMPLATE_PATH.exists():
        print(f"找不到模板文件 {TEMPLATE_PATH}")
        sys.exit(1)

    html = TEMPLATE_PATH.read_text(encoding="utf-8")
    html = html.replace("/*__SITES__*/", sites_json)
    html = html.replace("/*__CONTESTS__*/", contests_json)
    html = html.replace("/*__LABEL__*/", label_json)
    html = html.replace("/*__UPDATE__*/", now_str)
    return html


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--open", action="store_true", help="生成后自动打开")
    parser.add_argument("--status", action="store_true", help="仅显示统计")
    args = parser.parse_args()

    if not JSON_PATH.exists():
        print(f"找不到 {JSON_PATH}")
        sys.exit(1)

    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    today = datetime.date.today()

    # 统计
    counts = {"ongoing": 0, "upcoming": 0, "judging": 0, "ended": 0}
    for c in data["contests"]:
        s = auto_status(c, today)
        if s in counts: counts[s] += 1

    if args.status:
        print(f"总计: {len(data['contests'])}  平台: {len({c['site'] for c in data['contests']})}")
        for k, v in counts.items():
            print(f"  {STATUS_LABEL[k]}: {v}")
        return

    html = build_html(data)
    HTML_PATH.write_text(html, encoding="utf-8")
    print(f"已生成 {HTML_PATH} ({len(html):,} bytes)")
    print(f"  总计 {len(data['contests'])} 场比赛，覆盖 {len({c['site'] for c in data['contests']})} 个平台")
    for k, v in counts.items():
        print(f"  {STATUS_LABEL[k]}: {v}")

    if args.open:
        webbrowser.open(HTML_PATH.as_uri())


if __name__ == "__main__":
    main()