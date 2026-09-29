# -*- coding: utf-8 -*-
r"""
步骤⑦：10 道测试题 + 逐题召回记录
------------------------------------------------------------
作业要求：设计 10 道测试题（含 ≥2 道跨公司全景题），记录
        「召回了哪些块 / 答得对不对 / 错在哪」。

这个脚本一次做三件事：
  1. 拿这 10 道题去跑检索（三种模式任选）
  2. 对着【程序判定的标准答案块】自动判对错 —— 不靠我主观打分
  3. 把每一题捞到的东西、名次、命中/落空，写成一份记录

关键设计：标准答案不是「一句参考答案」，而是
    「哪一个块里含有能推出答案的数字/表述」
所以判对错完全客观：那个块进没进 top-k。

用法：
    python 测试题.py                  # 混合检索（最终交付用这个）
    python 测试题.py --mode bm25      # 只用关键词（对照用）
    python 测试题.py --mode vector    # 只看向量（对照用）
    python 测试题.py --topk 5         # 取前几块，默认 5

输出：
    D:\财报库\测试题结果_<模式>.json   机器可读，用来跟另一次跑对比
    桌面\第七步测试题记录_<模式>.txt   给你看/给老师看的
"""
import os, sys, json, argparse, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from 检索 import Retriever

DESK = os.path.join(os.path.expanduser("~"), "Desktop")
ROOT = r"D:\财报库"


# ============================================================
#  10 道题
# ------------------------------------------------------------
#  gold 的写法：[(公司, [关键词...]), ...]
#     公司写 None 表示不限公司
#     判「对」的条件 = 每一条 spec 都有块进了 top-k
#     —— 一条 spec 对应一个「必须拿到的块」。
#     比如对比题要两个块都拿到才算答得出来。
#
#  gold 写 None = 不判对错（口语化题，答不好是预期内的，
#     留着是为了在结论里说清楚「哪类问题会翻车、为什么」）
# ============================================================
QUESTIONS = [
    # ---------- 跨公司全景题（作业要求 ≥2 道）----------
    dict(n=1, kind="跨公司·极值",
         q="这 13 家白酒公司里，2026 年上半年消费税金额最高的是哪家？大约多少钱？",
         gold=[("贵州茅台", ["11,710,560,826.88"])],
         why="要横着比 13 家的『税金及附加→消费税』，只捞一家答不出来"),

    dict(n=2, kind="跨公司·比值",
         q="哪家公司的消费税占营业总收入的比重最高？",
         gold=[("山西汾酒", ["3,124,342,384.02"]),
               ("山西汾酒", ["21,044,387,808.34"])],
         why="每家都要取两个数相除。茅台绝对值最高但占比不是最高——"
             "只看绝对额会答错。且『营业总收入』和『营业收入』口径有别"),

    dict(n=3, kind="跨公司·对比",
         q="山西汾酒和古井贡酒，2026 年上半年谁的消费税额更高？",
         gold=[("山西汾酒", ["3,124,342,384.02"]),
               ("古井贡酒", ["1,337,530,510.62"])],
         why="两个块都得拿到才做得成对比；公司名都带『酒』，容易串"),

    # ---------- 单公司精确数字 ----------
    dict(n=4, kind="单公司·数字",
         q="泸州老窖 2026 年上半年消费税是多少？",
         gold=[("泸州老窖", ["1,430,850,214.02"])],
         why="要在泸州老窖自己的『税金及附加』明细里找，防止捞到别家"),

    dict(n=5, kind="单公司·数字",
         q="贵州茅台 2026 年上半年营业总收入是多少？",
         gold=[("贵州茅台", ["92,278,072,083"])],
         why="利润表首行，页码靠前"),

    dict(n=6, kind="单公司·数字",
         q="口子窖 2026 年上半年消费税是多少？",
         gold=[("口子窖", ["236,523,692.29"])],
         why="这家的税金及附加表跨页了：表头在 147 页、数字在 148 页，"
             "切块没切好就会漏"),

    # ---------- 政策条款 / 定性 ----------
    dict(n=7, kind="政策条款",
         q="白酒消费税的从量计征部分，按什么标准计缴？",
         gold=[[(None, ["0.5元/500ml"]), (None, ["0.50 元/500ml"]),
                (None, ["0.5元/500克"]), (None, ["0.5元/市斤"]),
                (None, ["0.50元/斤"]), (None, ["1元/公斤"]),
                (None, ["每吨人民币1,000.00"])]],
         why="各家写法不一样但都对：口子窖『0.5元/500ml』、"
             "今世缘『0.50元/斤』、山西汾酒『0.5元/500克』、"
             "老白干『每吨1,000元』、泸州老窖『1元/公斤』。"
             "这题考的是能不能捞到任意一条『从量』标准"),

    dict(n=8, kind="政策条款·术语",
         q="山西汾酒的消费税是怎么规定的？",
         gold=[("山西汾酒", ["复合计征"])],
         why="『复合计征』这四个字全库只有 1 个块有——"
             "分词没把词典加对的话这个词会被切碎，静默捞不到"),

    dict(n=9, kind="定性·风险",
         q="口子窖在风险提示里提到的「税收政策变化风险」具体指什么？",
         gold=[("口子窖", ["消费税政策", "从严"])],
         why="定性题，答案是一段话不是数字；考的是能不能定位到风险章节"),

    # ---------- 口语化（预期失败，用来做诚实的失败分析）----------
    dict(n=10, kind="口语化·预期失败",
         q="白酒企业的税负重不重？",
         gold=None,
         why="全库 168 处『税负』全是『递延所得税负债』的连读，"
             "没有一处是『税负重』的意思。要答这题得取多家的"
             "消费税+营业收入再自己算比率——检索做不了这步合成。"
             "留着，是要在结论里说实话"),
]


def spec_hit(spec, chunk):
    """块是否满足这一条标准
    spec = (公司, [关键词...])          -> 都要满足（且）
    spec = [(公司,[词]), (公司,[词])]    -> 任一满足即可（或）
        「或」是给那种【同一件事有好几种正确写法】的题用的：
        比如从量税标准，有人写 0.5元/500ml、有人写 0.50元/斤、
        有人写 每吨1000元 —— 都是对的，只认一种就是我在给自己
        放水（或者给自己下绊子）。
    """
    if isinstance(spec, list):                 # 或
        return any(spec_hit(s, chunk) for s in spec)
    comp, kws = spec
    return ((comp is None or chunk["name"] == comp)
            and all(k in chunk["text"] for k in kws))


def rank_of(spec, hits):
    """这条标准最早在第几名被满足；没进 top-k 就返回 None"""
    for i, h in enumerate(hits, 1):
        if spec_hit(spec, h.chunk):
            return i
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="hybrid",
                    choices=["hybrid", "bm25", "vector"])
    ap.add_argument("--topk", type=int, default=5)
    ap.add_argument("--pool", type=int, default=30)
    a = ap.parse_args()

    print("=" * 74)
    print("  步骤⑦ 测试题 —— 模式 %s，取前 %d 块" % (a.mode, a.topk))
    print("=" * 74)

    # 只用关键词时【不加载向量模型】，免得和还在跑的向量化抢 CPU
    r = Retriever(use_vector=(a.mode != "bm25"), verbose=True)
    if a.mode != "bm25" and not r.want_vector:
        print("\n[!] 向量索引还不可用。这轮跑出来的不是真正的 %s 结果。" % a.mode)
        print("    等 向量化.py 跑完再重跑本脚本。\n")

    rows = []
    for item in QUESTIONS:
        q = item["q"]
        hits = r.search(q, topk=a.topk, pool=a.pool, mode=a.mode)
        gold = item["gold"]

        if gold is None:
            ok, ranks = None, []
        else:
            ranks = [rank_of(s, hits) for s in gold]
            ok = all(x is not None for x in ranks)

        rows.append(dict(n=item["n"], kind=item["kind"], q=q,
                         ok=ok, ranks=ranks, why=item["why"],
                         hits=[dict(name=h.chunk["name"], page=h.chunk["page"],
                                    kind=h.chunk["kind"], score=round(h.score, 4),
                                    from_=h.from_,
                                    rank_bm25=h.rank_bm25, rank_vec=h.rank_vec,
                                    preview=h.chunk["text"][:90])
                               for h in hits]))

    # ---------- 汇总 ----------
    scored = [x for x in rows if x["ok"] is not None]
    n_ok = sum(1 for x in scored if x["ok"])
    rr = []
    for x in scored:
        got = [v for v in x["ranks"] if v]
        rr.append(1.0 / min(got) if got else 0.0)
    mrr = sum(rr) / len(rr) if rr else 0

    head = ("模式 %s   topk=%d   %s"
            % (a.mode, a.topk,
               datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))
    print("\n" + "=" * 74)
    print("  %s" % head)
    print("=" * 74)
    print("\n  Recall@%d：%d / %d = %.0f%%    MRR %.3f"
          % (a.topk, n_ok, len(scored), 100.0 * n_ok / max(1, len(scored)), mrr))

    # ---------- 写报告 ----------
    L = []
    L.append("=" * 74)
    L.append("  步骤⑦  10 道测试题 · 逐题召回记录")
    L.append("  %s" % head)
    L.append("=" * 74)
    L.append("")
    L.append("  判对错的方式：不看模型写了什么，只看【含答案的那个块】")
    L.append("  有没有进前 %d 名。块进没进是程序判的，不是我说了算。" % a.topk)
    L.append("")
    L.append("  Recall@%d（含答案的块进前 %d 名的比例）：%d / %d = %.0f%%"
             % (a.topk, a.topk, n_ok, len(scored), 100.0 * n_ok / max(1, len(scored))))
    L.append("  MRR（名次倒数的平均，越接近 1 说明答案排得越靠前）：%.3f" % mrr)
    L.append("")
    L.append("-" * 74)
    L.append("  一、总表")
    L.append("-" * 74)
    L.append("")
    L.append("  题号  类别                  结果     名次")
    for x in rows:
        if x["ok"] is None:
            res, rk = "不判分", "—"
        else:
            res = "✓ 命中" if x["ok"] else "✗ 落空"
            rk = " / ".join(str(v) if v else "未进" for v in x["ranks"])
        L.append("   %-4d %-20s %-8s %s" % (x["n"], x["kind"], res, rk))
    L.append("")
    L.append("-" * 74)
    L.append("  二、逐题明细")
    L.append("-" * 74)

    for x in rows:
        L.append("")
        L.append("┌" + "─" * 72)
        L.append("│ 第 %d 题 [%s]" % (x["n"], x["kind"]))
        L.append("│ 问：%s" % x["q"])
        if x["ok"] is None:
            L.append("│ 结果：不判分（这类题本来就不指望检索答对）")
        else:
            L.append("│ 结果：%s   标准块名次：%s"
                     % ("✓ 命中" if x["ok"] else "✗ 落空",
                        " / ".join(str(v) if v else "未进前%d" % a.topk
                                   for v in x["ranks"])))
        L.append("│ 这题在考什么：%s" % x["why"])
        L.append("│")
        L.append("│ 召回的块：")
        for i, h in enumerate(x["hits"], 1):
            way = []
            if h["rank_bm25"]:
                way.append("关键词#%d" % h["rank_bm25"])
            if h["rank_vec"]:
                way.append("向量#%d" % h["rank_vec"])
            L.append("│   %2d. %-8s 第%-4s页 %-5s 分%.4f  %s"
                     % (i, h["name"], h["page"],
                        "表格" if h["kind"] == "table" else "正文",
                        h["score"], " + ".join(way) or "—"))
            L.append("│       %s" % h["preview"].replace("\n", " "))
        L.append("└" + "─" * 72)

    L.append("")
    L.append("=" * 74)

    txt = "\n".join(L)
    print(txt)

    out_txt = os.path.join(DESK, "第七步测试题记录_%s.txt" % a.mode)
    with open(out_txt, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(txt + "\n")
    out_json = os.path.join(ROOT, "测试题结果_%s.json" % a.mode)
    json.dump(dict(mode=a.mode, topk=a.topk, recall=100.0 * n_ok / max(1, len(scored)),
                   mrr=mrr, rows=rows),
              open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n记录已写到：\n  %s\n  %s" % (out_txt, out_json))


if __name__ == "__main__":
    main()
