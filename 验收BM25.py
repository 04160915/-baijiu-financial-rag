# -*- coding: utf-8 -*-
r"""
步骤⑤ 验收：BM25 到底能不能把「答案所在的块」捞回来
============================================================
怎么算「对」：每题预先指定一个**标准答案**，判定客观、不靠我嘴说。

  判定有两种写法：
    1) 一串数字/短语  -> 凡是含这串的块都算答案
       例：消费税在应交税费里是 1,809,257,918.29
    2) (公司, [关键词]) -> 该公司名下、同时含这几个词的块算答案
       例：("山西汾酒", ["消费税","税金及附加"])

  然后看 BM25 的前 10 名里有没有这些块。
  * Recall@K  前 K 名里捞到答案的题占几成
  * MRR       第一个正确答案排在第几名的倒数（越接近 1 越好）

为什么还分「带公司过滤 / 不带」两栏跑：
  之前实测过，问「山西汾酒消费税税率」，BM25 会答成口子窖 ——
  因为消费税/税率是 13 家共有的高频词，把公司名压过去了。
  检索.py 里加了 companies_in() 过滤，这里就是量它到底救回来多少。

跑法：python 验收BM25.py
      python 验收BM25.py 3      # 只看第 3 题的标准答案长什么样
"""
import os, sys, io, json
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\财报库")
from 检索 import Retriever

KS = (1, 3, 5, 10)

# ---------------------------------------------------------------
# 题目表：(问题, 标准答案判定, 这题想考什么)
# ---------------------------------------------------------------
CASES = [
    # ---- A 类：单公司、精确数字。这是 BM25 的主场，应该对 ----
    ("贵州茅台的消费税在应交税费里是多少",
     "1,809,257,918.29", "A 单公司精确数字"),
    ("贵州茅台的消费税在税金及附加里是多少",
     "11,710,560,826.88", "A 单公司精确数字"),
    ("山西汾酒其他流动资产里的应退消费税是多少",
     "8,375,600.67", "A 单公司精确数字"),
    ("洋河股份的待抵扣消费税是多少",
     "362,626.29", "A 单公司精确数字"),

    # ---- B 类：政策条款。词很特别，BM25 应该也能打 ----
    ("白酒消费税从量计征的税额标准是多少",
     "0.5元/500", "B 政策条款"),
    ("白酒消费税是复合计征吗",
     "复合计征", "B 政策条款"),

    # ---- C 类：公司名被高频词淹没。这是过滤该救回来的 ----
    ("山西汾酒的消费税是多少",
     ("山西汾酒", ["消费税"]), "C 公司名干扰"),
    ("泸州老窖的消费税税率",
     ("泸州老窖", ["消费税"]), "C 公司名干扰"),
    # 下面这条是最能看出「公司过滤」价值的写法：没有「的」，
    # 「税率」又是 13 家共有的高频词，不过滤时公司名会被完全压过去。
    ("山西汾酒 消费税税率",
     ("山西汾酒", ["消费税"]), "C 公司名干扰（重）"),
    ("洋河股份 消费税 期末余额",
     ("洋河股份", ["消费税"]), "C 公司名干扰（重）"),

    # ---- D 类：跨公司全景。答案散在多家，看 BM25 能不能覆盖 ----
    ("各家公司的消费税税率一样吗",
     ("贵州茅台", ["消费税", "20%"]), "D 跨公司全景"),
    ("哪些公司提到了应退消费税",
     "应退消费税", "D 跨公司全景"),
    ("公司怎么披露消费税政策变化的风险",
     (None, ["消费税", "风险"]), "D 跨公司全景"),

    # ---- E 类：【故意放进来、预期 BM25 会输】的题 ----
    # 口语化提问，答案块里根本没这些词。用它证明「光有 BM25 不够，
    # 必须有向量那一半」—— 不掺几道该失败的题，验收就成了自夸。
    ("酒卖得怎么样",
     ("贵州茅台", ["营业收入", "变动"]), "E 口语化（预期失败）"),
    ("公司赚钱能力如何",
     ("五粮液", ["净利润", "同比"]), "E 口语化（预期失败）"),
]


def gold_of(r, spec):
    """按判定找出标准答案块的下标。
    判定是 (公司, [词...])，公司写 None 表示不限公司。"""
    if isinstance(spec, str):
        return [i for i, c in enumerate(r.chunks) if spec in c["text"]]
    comp, kws = spec
    return [i for i, c in enumerate(r.chunks)
            if (comp is None or c["name"] == comp)
            and all(k in c["text"] for k in kws)]


def show_gold(r, q, spec, gold):
    print("\n" + "=" * 78)
    print("  Q: %s" % q)
    print("  判定: %s" % (spec if isinstance(spec, str)
                          else "%s 且含 %s" % (spec[0] or "不限公司",
                                             "+".join(spec[1]))))
    print("  标准答案 %d 块：" % len(gold))
    for i in gold[:6]:
        c = r.chunks[i]
        ln = [l for l in c["text"].split("\n") if l.strip()]
        print("      #%-5d %-6s p%-4s %-5s | %s"
              % (i, c["name"], c["page"], c["kind"], (ln[-1] if ln else "")[:52]))
    if len(gold) > 6:
        print("      ... 还有 %d 块" % (len(gold) - 6))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    only = int(args[0]) - 1 if args else None
    r = Retriever(verbose=True)

    # 先挑出判定写坏的题（找不着标准答案 = 这题本身有问题，不是 BM25 的错）
    prepared, broken = [], []
    for q, spec, tag in CASES:
        g = gold_of(r, spec)
        if not g:
            broken.append((q, spec, tag))
        prepared.append((q, spec, tag, g))

    if only is not None:
        q, spec, tag, g = prepared[only]
        show_gold(r, q, spec, g)
        return

    if broken:
        print("\n[!] 有 %d 题的判定找不着标准答案 —— 这几题不算数，先修判定：" % len(broken))
        for q, spec, tag in broken:
            print("      %s  |  %s" % (q, spec))
    prepared = [p for p in prepared if p[3]]

    # ---------------- 跑 ----------------
    rows = []
    for q, spec, tag, gold in prepared:
        gset = set(gold)
        row = {"q": q, "tag": tag, "ngold": len(gold)}
        for flt in (False, True):
            hits = r.search(q, topk=max(KS), mode="bm25", filter_company=flt)
            order = [h.chunk_i for h in hits] if hasattr(hits[0], "chunk_i") else None
            if order is None:        # 检索.py 的 Hit 没存下标，这里按 id 反查
                id2i = {c["id"]: i for i, c in enumerate(r.chunks)}
                order = [id2i[h.chunk["id"]] for h in hits]
            rank = next((k + 1 for k, i in enumerate(order) if i in gset), None)
            key = "flt" if flt else "raw"
            row[key + "_rank"] = rank
            row[key + "_top"] = order[:3]
            for K in KS:
                row["%s@%d" % (key, K)] = bool(rank and rank <= K)
        rows.append(row)

    # ---------------- 明细 ----------------
    print("\n\n" + "=" * 78)
    print("  明细：每题第一个正确答案排在第几名")
    print("  （raw = 不过滤公司名，flt = 加了公司过滤）")
    print("=" * 78)
    print("%-34s %-6s %-6s %-6s" % ("题目", "答案块", "raw", "flt"))
    print("-" * 78)
    for x in rows:
        f = lambda v: ("#%d" % v) if v else "没捞到"
        star = "  <-- 过滤救回来了" if (not x["raw_rank"]) and x["flt_rank"] else ""
        print("%-34s %-6d %-6s %-6s%s"
              % (x["q"][:32], x["ngold"], f(x["raw_rank"]), f(x["flt_rank"]), star))

    # ---------------- 汇总 ----------------
    print("\n" + "=" * 78)
    print("  汇总")
    print("=" * 78)
    print("%-8s %-12s %-12s" % ("", "不过滤公司", "过滤公司"))
    print("-" * 78)
    for K in KS:
        a = sum(x["raw@%d" % K] for x in rows) / len(rows)
        b = sum(x["flt@%d" % K] for x in rows) / len(rows)
        print("  Recall@%-2d   %-12s %-12s" % (K, "%.0f%%" % (a * 100),
                                               "%.0f%%" % (b * 100)))
    mrr = lambda key: np.mean([1.0 / x[key + "_rank"] if x[key + "_rank"] else 0.0
                               for x in rows])
    print("  MRR        %-12s %-12s" % ("%.3f" % mrr("raw"), "%.3f" % mrr("flt")))

    # ---------------- 按类别拆开 ----------------
    print("\n" + "=" * 78)
    print("  按类别（用过滤公司那一栏）")
    print("=" * 78)
    tags = []
    for x in rows:
        if x["tag"] not in tags:
            tags.append(x["tag"])
    for t in tags:
        sub = [x for x in rows if x["tag"] == t]
        r5 = sum(x["flt@5"] for x in sub) / len(sub)
        print("  %-22s %d 题   Recall@5 = %3.0f%%"
              % (t, len(sub), r5 * 100))

    # ---------------- 失败清单 ----------------
    bad = [x for x in rows if not x["flt@5"]]
    if bad:
        print("\n" + "=" * 78)
        print("  没进前 5 名的题（这些就是 BM25 的真实短板）")
        print("=" * 78)
        for x in bad:
            print("\n  Q: %s   [%s]" % (x["q"], x["tag"]))
            print("     标准答案共 %d 块，排名：%s"
                  % (x["ngold"], x["flt_rank"] or "前 10 名之外"))
            id2i = {c["id"]: i for i, c in enumerate(r.chunks)}
            print("     实际返回的前 3 名：")
            for i in x["flt_top"]:
                c = r.chunks[i]
                print("        %-6s p%-4s" % (c["name"], c["page"]))
    else:
        print("\n  前 5 名全中。")


class _Tee:
    """把 print 同时写到屏幕和内存，用来生成报告文件"""
    def __init__(self, *fs):
        self.fs = fs

    def write(self, s):
        for f in self.fs:
            f.write(s)

    def flush(self):
        for f in self.fs:
            try:
                f.flush()
            except Exception:
                pass


DST_TXT = r"C:\Users\沈祥暄\Desktop\第五步BM25验收.txt"

BANNER = """第 ⑤ 步验收：BM25 关键词检索
================================================================================
这份是 验收BM25.py 的输出。做法：每题预先指定「标准答案块」的判定
（一个数字，或「某公司 + 含某几个词」），全自动在 5861 块里找出标准答案，
再看 BM25 的前 10 名里有没有它。判对判错由程序给，不靠我嘴说。

指标：
  Recall@K  前 K 名里出现过标准答案的题占几成
  MRR       第一个正确答案排名的倒数，越接近 1 越好

两栏对比：
  raw = 查询里点名了公司，但不过滤，全库一起排
  flt = 认出查询里的公司名后，只在该公司的块里搜

跑法：python 验收BM25.py            （看全部）
      python 验收BM25.py --report   （顺带生成这份文件）
      python 验收BM25.py 3          （只看第 3 题的标准答案长什么样）

================================================================================

"""

if __name__ == "__main__":
    if "--report" in sys.argv:
        import io, datetime
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = _Tee(old, buf)
        try:
            main()
        finally:
            sys.stdout = old
        head = BANNER + "生成时间：%s\n\n" % datetime.datetime.now().strftime(
            "%Y-%m-%d %H:%M")
        with open(DST_TXT, "w", encoding="utf-8-sig", newline="\r\n") as f:
            f.write(head + buf.getvalue())
        print("\n报告已写到 %s" % DST_TXT)
    else:
        main()

