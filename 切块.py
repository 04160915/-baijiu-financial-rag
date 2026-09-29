# -*- coding: utf-8 -*-
r"""
步骤③：把提取出的 block 切成检索用的 chunk
------------------------------------------------------------
输入：D:\财报库\extracted\*.json
输出：D:\财报库\chunks.jsonl     每行一个 chunk
      {"id","code","name","page","section","kind","text","chars"}

切块要点：
  * 每块带完整出处（公司 + 报告期 + 页码 + 章节）—— 作业要求可引用
  * 表格整张算一块；太长就按行切，每片都重复表头，否则脱离表头看不懂
  * 文字按「段落边界」切，目标 450 字，相邻块重叠 80 字，避免答案被切断
"""
import os, re, sys, json

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = r"D:\财报库\extracted"
DST = r"D:\财报库\chunks.jsonl"

TARGET = 450      # 每块目标字数
OVERLAP = 80      # 相邻块重叠
TABLE_MAX_ROWS = 40   # 表超过这么多行就切片

# 页眉（每页都出现，纯噪声）
NOISE = re.compile(r"(股份有限公司|有限公司)?\s*2026\s*年半年度报告(全文)?\s*$")
# 页脚页码「37/142」「第 37 页 共 142 页」和目录引导线「第一节 ...... 5」
#   这些不剔掉的话，BM25 会把「37」「142」当成词索引，
#   而页码块又特别短 -> BM25 的长度归一化会把它顶到第一名
DROP = re.compile(r"^\s*\d{1,4}\s*/\s*\d{1,4}\s*$"
                  r"|^\s*第\s*\d+\s*页\s*共\s*\d+\s*页\s*$"
                  r"|[.…·]{4,}")


def head(rec, page, section):
    """块首的出处标注 —— 检索和引用都靠它"""
    return "【%s %s · 2026年半年报 · 第%d页 · %s】" % (
        rec["name"], rec["code"], page, section or "—")


# 表名在提取阶段（提取器.py）就按「表格上方最近的附注小标题」配好了，
# 存在 block 的 "note" 字段里，这里直接用，不再自己猜。


def table_to_text(rows):
    return "\n".join(" | ".join((c or "").strip() for c in r) for r in rows if any(r))


def cut_table(rec, b):
    """表格 -> 若干块。一张表一块；太长按行切片并重复表头。"""
    out = []
    rows = b["rows"]
    hdr = rows[0]
    body = rows[1:]
    pages = b.get("pages") or [b.get("page")]
    pg = pages[0]
    tag = "、".join(str(p) for p in pages)
    # 表名用提取阶段按坐标配好的附注小标题（「45、税金及附加」）。
    # 配不到就退回表头第一格（常常只是「项目」）。
    # 这里【故意不做】「取本页最后一个标题」的兜底 —— 一页常有好几张表，
    # 那样会给表配错名（比没有名字更糟）。
    note = b.get("note") or ""
    title = "表：%s" % (note if note else (hdr[0] or "数据"))

    if len(body) <= TABLE_MAX_ROWS:
        pieces = [body]
    else:
        pieces = [body[i:i + TABLE_MAX_ROWS]
                  for i in range(0, len(body), TABLE_MAX_ROWS)]

    for k, piece in enumerate(pieces):
        parts = [head(rec, pg, b.get("section")), title]
        if len(pieces) > 1:
            parts[-1] += "（%d/%d）" % (k + 1, len(pieces))
        if len(pages) > 1:
            parts[1] += "　第%s页（跨页）" % tag
        parts.append(" | ".join((c or "").strip() for c in hdr))
        parts.append(table_to_text(piece))
        out.append("\n".join(parts))
    return out


def cut_text(rec, b):
    """文字块 -> 按段落边界切片"""
    lines = [l.strip() for l in b["content"].split("\n")]
    lines = [l for l in lines if l and not NOISE.search(l) and not DROP.search(l)]
    if not lines:
        return []
    out = []
    buf = []
    n = 0
    for ln in lines:
        buf.append(ln)
        n += len(ln)
        if n >= TARGET:
            out.append("\n".join(buf))
            # 重叠：留住末尾若干行
            keep, kn = [], 0
            for l in reversed(buf):
                keep.insert(0, l)
                kn += len(l)
                if kn >= OVERLAP:
                    break
            buf, n = keep, kn
    if buf and n > 60:            # 太短的尾巴并进上一块
        if out and n < 120:
            out[-1] += "\n" + "\n".join(buf)
        else:
            out.append("\n".join(buf))
    return ["%s\n%s" % (head(rec, b["page"], b.get("section")), t) for t in out]


def main():
    files = sorted(f for f in os.listdir(SRC) if f.endswith(".json"))
    total, stats = 0, []
    with open(DST, "w", encoding="utf-8") as fo:
        for fn in files:
            rec = json.load(open(os.path.join(SRC, fn), encoding="utf-8"))
            n_tab = n_txt = 0
            for b in rec["blocks"]:
                if b["kind"] == "table":
                    texts = cut_table(rec, b)
                    kind = "table"
                    pg = (b.get("pages") or [b.get("page")])[0]
                else:
                    texts = cut_text(rec, b)
                    kind = "text"
                    pg = b["page"]
                for t in texts:
                    fo.write(json.dumps({
                        "id": "%s-%05d" % (rec["code"], total),
                        "code": rec["code"], "name": rec["name"],
                        "page": pg, "section": b.get("section", ""),
                        "kind": kind, "text": t, "chars": len(t),
                    }, ensure_ascii=False) + "\n")
                    total += 1
                    if kind == "table":
                        n_tab += 1
                    else:
                        n_txt += 1
            stats.append((rec["name"], n_txt, n_tab))

    print("=" * 70)
    print("  切块完成 -> %s" % DST)
    print("=" * 70)
    print("%-14s %8s %8s" % ("公司", "文字块", "表格块"))
    print("-" * 70)
    for nm, a, b_ in stats:
        print("%-14s %8d %8d" % (nm, a, b_))
    print("-" * 70)
    print("%-14s %8d %8d   合计 %d 块" % ("合计",
          sum(s[1] for s in stats), sum(s[2] for s in stats), total))
    print("\n  平均块长 %.0f 字" % (
        sum(json.loads(l)["chars"] for l in open(DST, encoding="utf-8")) / total))


if __name__ == "__main__":
    main()
