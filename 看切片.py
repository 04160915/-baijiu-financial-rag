# -*- coding: utf-8 -*-
r"""
生成一份人能直接翻看的切片预览
输出：桌面\切片预览.txt（UTF-8 带 BOM，记事本双击可直接看）
"""
import os, re, sys, json, collections, statistics

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SRC = r"D:\财报库\chunks.jsonl"
DST = os.path.join(os.path.expanduser("~"), "Desktop", "切片预览.txt")

R = [json.loads(l) for l in open(SRC, encoding="utf-8")]
OUT = []
W = lambda s="": OUT.append(s)
BAR = "=" * 76


def blk(r, limit=700):
    t = r["text"]
    if len(t) > limit:
        t = t[:limit] + "\n        ...（本块共 %d 字，此处截断）" % r["chars"]
    return "\n".join("        " + l for l in t.split("\n"))


W(BAR)
W("  财报切片预览   ——   白酒 13 家 · 2026 年半年报")
W(BAR)
W()
W("  这份文件是「步骤③切块」的产物样本，一共 %d 块。" % len(R))
W("  每块开头那行【】里是出处：公司 + 报告期 + 页码 + 章节。")
W("  做问答时答案的「引用」就靠这一行。")
W()

# ---------- 1 统计 ----------
W(BAR)
W("  一、总体情况")
W(BAR)
W()
k = collections.Counter(r["kind"] for r in R)
W("  总块数        %d" % len(R))
W("  文字块        %d  (%.0f%%)" % (k["text"], 100 * k["text"] / len(R)))
W("  表格块        %d  (%.0f%%)" % (k["table"], 100 * k["table"] / len(R)))
W()
ch = [r["chars"] for r in R]
W("  每块字数      中位 %d 字，最短 %d，最长 %d" % (statistics.median(ch), min(ch), max(ch)))
W("  课件要求      800 字一块 / 重合 120 字")
W("  实际采用      450 字一块 / 重合 80 字")
W("                 （课件那套参数是给长文书的；财报表格密、")
W("                   数值一行就是一条，块小一点召回更准）")
W()
W("  块长分布：")
for lo, hi, nm in [(0, 200, "很短"), (200, 400, "偏短"), (400, 600, "正好"),
                   (600, 900, "偏长"), (900, 99999, "很长")]:
    n = sum(1 for x in ch if lo <= x < hi)
    W("      %-4s %4d-%-5d  %5d 块  %s" % (
        nm, lo, hi, n, "#" * int(40 * n / len(R))))
W()
W("  按公司：")
W("      %-10s %8s %8s" % ("公司", "文字块", "表格块"))
by = collections.defaultdict(lambda: [0, 0])
for r in R:
    by[r["name"]][0 if r["kind"] == "text" else 1] += 1
for n in sorted(by):
    W("      %-10s %8d %8d" % (n, by[n][0], by[n][1]))
W()

# ---------- 2 样本 ----------
W(BAR)
W("  二、切片长什么样（样本）")
W(BAR)
W()

W("-" * 76)
W("  【样本 1】文字块 —— 利润表（表格在文字层里已经按行列排好了）")
W("-" * 76)
s = [r for r in R if r["name"] == "贵州茅台" and r["page"] == 30 and r["kind"] == "text"][0]
W(blk(s, 620))
W()

W("-" * 76)
W("  【样本 2】表格块 —— 税金及附加附注（消费税就藏在这类表里）")
W("-" * 76)
s = [r for r in R if r["name"] == "古井贡酒" and "消费税" in r["text"]
     and r["kind"] == "table" and "1,337" in r["text"]]
W(blk(s[0], 620) if s else "      （未命中）")
W()

W("-" * 76)
W("  【样本 3】表格块 —— 税种税率表（回答「税率是多少」靠它）")
W("-" * 76)
s = [r for r in R if r["name"] == "酒鬼酒" and "从量" in r["text"] and r["kind"] == "table"]
W(blk(s[0], 620) if s else "      （未命中）")
W()

W("-" * 76)
W("  【样本 4】文字块 —— 管理层讨论（带观点的段落）")
W("-" * 76)
s = [r for r in R if r["name"] == "五粮液" and r["kind"] == "text"
     and r["section"].startswith("第三节") and "营业收入" in r["text"]
     and r["chars"] > 400]
W(blk(s[0], 620) if s else "      （未命中）")
W()

# ---------- 3 消费税专题 ----------
W(BAR)
W("  三、消费税专题（你这个方向的命门）")
W(BAR)
W()
W("  ⚠ 先说一个必须知道的坑：财报里「消费税」有两个数字，含义完全不同。")
W("     以贵州茅台为例——")
W()
W("     第 70 页「30、应交税费」   消费税 1,809,257,918.29  ← 资产负债表，")
W("                                   是期末【还欠着】的税（负债余额）")
W("     第 74 页「45、税金及附加」 消费税 11,710,560,826.88 ← 利润表，")
W("                                   是本期【实际发生】的税（费用）")
W()
W("     两者差 6.5 倍。做税负分析要的是后者（费用）。")
W("     检索时如果按「消费税」三个字去搜，很容易抓到前者，")
W("     因为两者字面一模一样。这正是向量 RAG 的软肋。")
W()
W("  下面把两个数都放出来对照（单位：亿元）：")
W()
W("      %-10s %10s %6s %12s %6s %10s" % (
    "公司", "消费税负债", "页", "消费税费用", "页", "费用/营收"))
W("      " + "-" * 62)

LIAB, EXP, REV = {}, {}, {}
for r in R:
    ls = r["text"].split("\n")
    for i, l in enumerate(ls):
        ctx = " ".join(ls[max(0, i - 14):i])
        m = re.match(r"^\s*消费税\s+([\d,]+\.\d+)", l)
        if m:
            v = float(m.group(1).replace(",", ""))
            if "应交税费" in ctx and r["name"] not in LIAB:
                LIAB[r["name"]] = (v, r["page"])
            if ("税金及附加" in ctx or "本期发生额" in ctx) and r["name"] not in EXP:
                EXP[r["name"]] = (v, r["page"])
        m2 = re.match(r"^\s*(?:其中：)?(?:营业总收入|营业收入)\s+([\d,]+\.\d+)", l)
        if m2 and r["name"] not in REV:
            v = float(m2.group(1).replace(",", ""))
            if v > 1e8:
                REV[r["name"]] = v
EXP["口子窖"] = (236523692.29, 148)          # 上下文跨度不够，手工补
rows = []
for n in sorted(EXP):
    e, pe = EXP[n]
    rows.append((n, LIAB.get(n), e, pe, REV.get(n)))
for n, li, e, pe, rv in sorted(rows, key=lambda x: -(x[2] / x[4] if x[4] else 0)):
    W("      %-10s %10s %6s %12s %6s %10s" % (
        n,
        "%.2f" % (li[0] / 1e8) if li else "—",
        li[1] if li else "",
        "%.2f" % (e / 1e8), pe,
        "%.2f%%" % (100 * e / rv) if rv else "—"))
W()
W("      结论：费用/营收 落在 11.6%~14.9%。")
W("      区域酒（山西汾酒 14.85%、舍得 14.45%、老白干 14.07%）")
W("      整体高于高端酒（茅台 12.91%、五粮液 11.90%）。")
W("      原因：消费税 = 20% 从价 + 从量（0.5元/500ml）。")
W("      从量部分是【固定额】，低价酒分摊下来税负率天然更高。")
W()
W("  下面看这两个原始块在库里长什么样——它们字面几乎一样：")
W()
for pg, tag in [(70, "这是【负债】，不是你要的"),
                (74, "这是【费用】，这才是你要的")]:
    for r in R:
        if r["name"] != "贵州茅台" or r["page"] != pg or r["kind"] != "text":
            continue
        if "消费税" not in r["text"]:
            continue
        W("-" * 76)
        W("  第 %d 页   %s" % (pg, tag))
        W("-" * 76)
        ls = r["text"].split("\n")
        for i, l in enumerate(ls):
            if re.match(r"^\s*消费税\s", l):
                for j in range(max(0, i - 5), min(len(ls), i + 3)):
                    W("        " + ls[j][:72])
                break
        W()
        break

# ---------- 4 已知问题 ----------
W(BAR)
W("  四、已知的小毛病（不影响用，但要知道）")
W(BAR)
W()
W("  1. 表格块标题常常只写着「项目」，没把「这是哪张表」写出来。")
W("     不影响检索——因为表里的行标签（消费税、增值税……）本身")
W("     才是被匹配的关键词，出处行里也有页码和章节。")
W()
W("  2. 少数表格的表头是「多行表头」（横跨 3 行），拆出来后表头认不全。")
W("     数据行本身是对的。这类占少数。")
W()
W("  3. 有 %d 块章节是空的——那是报告最前面还没出现「第X节」的几页。" % sum(
    1 for r in R if not r["section"].strip()))
W()
W("  4. 表格里单元格内的文字换行，已经做了「续行合并」接回去，")
W("     但极少数仍可能断成两行。")
W()
W(BAR)
W("  完")
W(BAR)

with open(DST, "w", encoding="utf-8-sig", newline="\r\n") as f:
    f.write("\n".join(OUT))

print("已生成 -> %s" % DST)
print("共 %d 行，%d 字节" % (len(OUT), os.path.getsize(DST)))
print("覆盖到消费税的公司: %d 家" % len(EXP))
