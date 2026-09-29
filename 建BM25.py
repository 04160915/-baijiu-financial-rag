# -*- coding: utf-8 -*-
r"""
步骤⑤：建 BM25 索引（关键词检索，和向量检索互补）
============================================================
输入：D:\财报库\chunks.jsonl
输出：D:\财报库\bm25_corpus.json   分词后的语料（存下来，免得每次重切）

为什么要 BM25：向量检索擅长「意思相近」，但财报里大量是
  「应交税费」「消费税」「0.5元/500ml」这种精确词，BM25 反而更稳。
  两者一起用（混合检索）是课件的标准做法。

⚠ 分词器在 分词.py 里，检索.py 也 import 同一个。
  千万别在这里另写一份 cut() —— 索引和查询分词不一致的话，
  稀有词会静默失配（这个坑踩过一次，详见 分词.py 开头）。
"""
import os, sys, json, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import 分词

SRC = r"D:\财报库\chunks.jsonl"
DST = r"D:\财报库\bm25_corpus.json"


def main():
    t0 = time.time()
    print("=" * 72)
    print("  步骤⑤ 建 BM25 索引")
    print("=" * 72)

    # 先确认词典生效，否则后面全白干
    if not 分词.verify():
        print("  词典有问题，先修 分词.py 再建索引")
        return

    chunks = [json.loads(l) for l in open(SRC, encoding="utf-8")]
    print("\n  语料 %d 块，财税词典 %d 词" % (len(chunks), len(分词.WORDS)))
    print("\n  分词中 ...")

    corpus = []
    for i, c in enumerate(chunks):
        corpus.append(分词.cut(c["text"]))
        if (i + 1) % 1000 == 0:
            print("      %5d / %d  (%.0f%%)" % (i + 1, len(chunks),
                                                100 * (i + 1) / len(chunks)),
                  flush=True)

    json.dump(corpus, open(DST, "w", encoding="utf-8"), ensure_ascii=False)
    ntok = sum(len(t) for t in corpus)
    print("\n  分词完成")
    print("     语料文件 %s  (%.1f MB)" % (DST, os.path.getsize(DST) / 1e6))
    print("     总词数 %d，平均每块 %.0f 个词" % (ntok, ntok / len(corpus)))
    print("     空块 %d 个" % sum(1 for t in corpus if not t))

    # ---------- 切词自检 ----------
    print("\n  切词自检：")
    for probe in ["复合计征", "从量计征", "消费税", "税金及附加"]:
        hit = next((t for t in corpus if probe in t), None)
        if hit:
            k = hit.index(probe)
            print("     「%s」 -> %s" % (probe, " / ".join(hit[max(0, k - 3):k + 4])))
        else:
            print("     「%s」 -> 语料里没找到（词典没生效？）" % probe)

    # ---------- 试检索 ----------
    from rank_bm25 import BM25Okapi
    print("\n  建索引 ...")
    bm = BM25Okapi(corpus)
    print("     完成，耗时 %.1f 秒" % (time.time() - t0))

    print("\n  试检索（每题看前 3 名）：")
    tests = ["消费税税率是多少", "白酒消费税是复合计征吗", "白酒从量计征0.5元",
             "贵州茅台上半年营业收入", "税金及附加"]
    for q in tests:
        print("\n     Q: %s" % q)
        sc = bm.get_scores(分词.cut(q))
        for i in sc.argsort()[::-1][:3]:
            if sc[i] <= 0:
                break
            c = chunks[i]
            line = [l for l in c["text"].split("\n") if l.strip()][-1][:44]
            print("        %.2f  %s p%-4s | %s" % (sc[i], c["name"], c["page"], line))

    print("\n  总耗时 %.1f 秒" % (time.time() - t0))


if __name__ == "__main__":
    main()
