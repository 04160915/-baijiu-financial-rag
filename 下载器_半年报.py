# -*- coding: utf-8 -*-
"""从巨潮下载白酒行业 2026 年半年报全文 PDF"""
import os, re, sys, time
import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUT = r"D:\财报库"
os.makedirs(OUT, exist_ok=True)

COMPANIES = [
    ("600519", "贵州茅台"), ("000858", "五粮液"),   ("000568", "泸州老窖"),
    ("600809", "山西汾酒"), ("002304", "洋河股份"), ("000596", "古井贡酒"),
    ("603369", "今世缘"),   ("600702", "舍得酒业"), ("000799", "酒鬼酒"),
    ("600779", "水井坊"),   ("603589", "口子窖"),   ("603198", "迎驾贡酒"),
    ("600559", "老白干酒"),
]

API = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
HEAD = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Referer": "https://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
    "Origin": "https://www.cninfo.com.cn",
}

TAGS = re.compile(r"<[^>]+>")


def strip_tags(s):
    return TAGS.sub("", s or "")


def query(code):
    """按股票代码查半年报，沪市/深市都试一遍"""
    mk = ("sse", "sh") if code.startswith("6") else ("szse", "sz")
    for column, plate in [mk, ("szse", "sz") if mk[0] == "sse" else ("sse", "sh")]:
        data = {
            "pageNum": 1, "pageSize": 30, "column": column, "tabName": "fulltext",
            "plate": plate, "searchkey": code,
            "category": "category_bndbg_szsh", "seDate": "",
            "sortName": "", "sortType": "", "isHLtitle": "true",
        }
        try:
            j = requests.post(API, headers=HEAD, data=data, timeout=25).json()
        except Exception as e:
            print("   查询异常:", type(e).__name__, e)
            time.sleep(2)
            continue
        anns = j.get("announcements") or []
        if anns:
            return anns
        time.sleep(1.5)
    return []


def pick(anns, code, year="2026"):
    """挑出正文那条，排除摘要/英文版/更正版"""
    want = "%s年半年度报告" % year
    hits = []
    for a in anns:
        if a.get("secCode") != code:
            continue
        t = strip_tags(a.get("announcementTitle"))
        if want not in t:
            continue
        if any(k in t for k in ("摘要", "英文", "更正", "取消", "补充")):
            continue
        hits.append((t, a.get("adjunctUrl")))
    return hits[0] if hits else None


print("=" * 62)
print("  巨潮下载 · 白酒行业 2026 年半年报")
print("  存到:", OUT)
print("=" * 62)
print()

ok, fail = [], []

for i, (code, name) in enumerate(COMPANIES, 1):
    tag = "[%2d/%d] %s %s" % (i, len(COMPANIES), code, name)
    print(tag, "查询中...", end=" ", flush=True)

    anns = query(code)
    hit = pick(anns, code)

    if not hit:
        print("✗ 没找到 2026 年半年度报告（可能未披露）")
        fail.append((code, name, "未找到"))
        time.sleep(1.5)
        continue

    title, url = hit
    pdf_url = "https://static.cninfo.com.cn/" + url.lstrip("/")
    dst = os.path.join(OUT, "%s_%s_2026半年报.pdf" % (code, name))

    try:
        r = requests.get(pdf_url, headers=HEAD, timeout=60)
        head = r.content[:4]
        if r.status_code != 200 or head != b"%PDF":
            print("✗ 下载失败 (状态 %s, 头 %r)" % (r.status_code, head))
            fail.append((code, name, "下载失败"))
            time.sleep(1.5)
            continue
        with open(dst, "wb") as f:
            f.write(r.content)
        mb = len(r.content) / 1024 / 1024
        print("✓ %.1f MB" % mb)
        ok.append((code, name, mb))
    except Exception as e:
        print("✗ 异常:", type(e).__name__, e)
        fail.append((code, name, type(e).__name__))

    time.sleep(1.8)          # 限流：约 30 次/分钟以下，巨潮要求 ≤10 次/分钟更稳

print()
print("=" * 62)
print("  成功 %d 家，失败 %d 家" % (len(ok), len(fail)))
print("=" * 62)
for code, name, mb in ok:
    print("  ✓ %s %s  %.1f MB" % (code, name, mb))
for code, name, why in fail:
    print("  ✗ %s %s  (%s)" % (code, name, why))
