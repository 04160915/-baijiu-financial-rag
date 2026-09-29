# -*- coding: utf-8 -*-
r"""
混合检索：BM25（关键词）+ 向量（语义）一起排序
------------------------------------------------------------
两块索引：
  * bm25_corpus.json  步骤⑤ 建的（jieba 分词后的语料）
  * vectors.npy       步骤④ 建的（Qwen3-Embedding 向量，已归一化）

为什么要混合：财报里既有「意思相近」的问法（「酒卖得怎么样」），
  也有精确词（「从量计征」「0.5元/500ml」「应交税费」）。
  前者向量强、后者 BM25 强，单一手段都会漏。

怎么融合：用 RRF（Reciprocal Rank Fusion，倒数排名融合）
      score(d) = Σ  1 / (k + rank_i(d))     k 取 60
  RRF 只看【名次】不看【分数】——这正好绕开了一个坑：
  BM25 的分数是无界的（可以到 20），余弦相似度是 0~1，
  两者直接加权得先做归一化，很麻烦而且不稳。RRF 不需要。

用法：
    from 检索 import Retriever
    r = Retriever()                 # 自动加载已有的索引
    hits = r.search("消费税税率是多少", topk=5)
    for h in hits:
        print(h.score, h.chunk["name"], h.chunk["page"])
"""
import os, sys, json, re
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import 分词          # 分词器只此一份，和 建BM25.py 共用

ROOT = r"D:\财报库"
F_CHUNKS = os.path.join(ROOT, "chunks.jsonl")
F_CORPUS = os.path.join(ROOT, "bm25_corpus.json")
F_VECS = os.path.join(ROOT, "vectors.npy")
F_IDS = os.path.join(ROOT, "vectors_ids.json")
MODEL = (r"C:\Users\沈祥暄\.cache\modelscope\models"
         r"\Qwen--Qwen3-Embedding-0.6B\snapshots\master")

RRF_K = 60
# 查询侧前缀（Qwen3-Embedding 官方推荐的检索指令）
INSTRUCT = "Instruct: 给定查询，检索相关的财务报告段落\nQuery: "

class Hit:
    __slots__ = ("score", "chunk", "from_", "rank_bm25", "rank_vec", "s_bm25", "s_vec")

    def __init__(self, chunk, score):
        self.chunk = chunk
        self.score = score
        self.from_ = []
        self.rank_bm25 = self.rank_vec = None
        self.s_bm25 = self.s_vec = None

    def __repr__(self):
        c = self.chunk
        return "<%.4f %s p%s %s>" % (self.score, c["name"], c["page"], c["kind"])


class Retriever:
    def __init__(self, use_vector=True, verbose=True):
        self.chunks = [json.loads(l) for l in open(F_CHUNKS, encoding="utf-8")]
        self.bm = None
        self.corpus = None
        self.vecs = None
        self.tok = self.model = None

        # ---- BM25 ----
        if os.path.exists(F_CORPUS):
            from rank_bm25 import BM25Okapi
            self.corpus = json.load(open(F_CORPUS, encoding="utf-8"))
            if len(self.corpus) == len(self.chunks):
                # 自检：词典必须和建索引时的一样。
                # 不一致的话「复合计征」这类稀有词会被静默切碎，
                # 检索结果变差但【不会报错】——所以这里主动喊一声。
                self.tok_ok = 分词.verify(verbose)
                # 再验一层：索引里真的存了这些 token 吗
                if self.tok_ok:
                    miss = [w for w in 分词.PROBE
                            if not any(w in t for t in self.corpus)]
                    if miss and verbose:
                        print("[检索] !! 索引里没有这些词：%s" % miss)
                        print("[检索]    说明 bm25_corpus.json 是旧分词建的，重跑 建BM25.py")
                self.bm = BM25Okapi(self.corpus)
                if verbose:
                    print("[检索] BM25 就绪：%d 块" % len(self.corpus))
            elif verbose:
                print("[检索] !! bm25_corpus 与 chunks 数量不一致，BM25 跳过")
        elif verbose:
            print("[检索] 没有 bm25_corpus.json，BM25 跳过（先跑 建BM25.py）")

        # 公司名/代码 -> 便于查询里提到公司时收紧范围
        self.names = sorted({c["name"] for c in self.chunks})
        self.code2name = {c["code"]: c["name"] for c in self.chunks}
        # 别名词典：报告里/口语里可能不按简称说
        self.alias = {"茅台": "贵州茅台", "五粮液": "五粮液", "老窖": "泸州老窖",
                      "汾酒": "山西汾酒", "洋河": "洋河股份", "古井": "古井贡酒",
                      "今世缘": "今世缘", "舍得": "舍得酒业", "酒鬼": "酒鬼酒",
                      "水井坊": "水井坊", "口子窖": "口子窖",
                      "迎驾": "迎驾贡酒", "老白干": "老白干酒", "衡水老白干": "老白干酒"}

        # ---- 向量 ----
        self.want_vector = use_vector
        if use_vector:
            if not (os.path.exists(F_VECS) and os.path.exists(F_IDS)):
                if verbose:
                    print("[检索] 还没有 vectors.npy，向量检索暂不可用"
                          "（步骤④ 跑完后自动生效）")
                self.want_vector = False
            else:
                # 坑：向量化是【每若干批落一次盘】的，中途落盘时整个数组已存在，
                # 但没算完的行是【全零】。不检查的话会把「算了 4%」当完整索引用，
                # 静默漏掉 96% 的语料（零向量余弦=0，全排在最后，不会报错）。
                # 所以这里自己看：有零行 = 还没算完。
                nrm = np.linalg.norm(np.load(F_VECS, mmap_mode="r"), axis=1)
                miss = int((nrm == 0).sum())
                if miss:
                    if verbose:
                        print("[检索] !! 向量只算完 %d / %d 行（还差 %d 行）"
                              % (len(nrm) - miss, len(nrm), miss))
                        print("[检索]    向量检索暂时禁用，等 向量化.py 跑完再来")
                    self.want_vector = False

    # ---------------- 查询分词 ----------------
    def cut(self, text):
        # 走 分词.py —— 和建索引用的是同一套词典、同一份停用词。
        return 分词.cut(text)

    # ---------------- 向量模型（懒加载） ----------------
    def _load_model(self):
        if self.tok is not None:
            return
        import torch
        from transformers import AutoModel, AutoTokenizer
        import transformers.utils.logging as L
        L.disable_progress_bar()
        L.set_verbosity_error()
        self._torch = torch
        self.tok = AutoTokenizer.from_pretrained(MODEL, padding_side="left")
        self.model = AutoModel.from_pretrained(MODEL, dtype=torch.bfloat16).eval()
        self.vecs = np.load(F_VECS)
        ids = json.load(open(F_IDS, encoding="utf-8"))
        # 向量是按 chunks 顺序存的，核对一下别错位
        assert ids == [c["id"] for c in self.chunks], "向量与切片顺序不一致！"
        print("[检索] 向量就绪：%s" % (self.vecs.shape,))

    def embed_query(self, q):
        import torch.nn.functional as F
        self._load_model()
        b = self.tok([INSTRUCT + q], padding=True, truncation=True,
                     max_length=512, return_tensors="pt")
        with self._no_grad():
            o = self.model(**b)
        v = F.normalize(o.last_hidden_state[:, -1].float(), p=2, dim=1)
        return v.numpy()[0]

    def _no_grad(self):
        import torch
        return torch.no_grad()

    # ---------------- 三路检索 ----------------
    def search_bm25(self, q, topk, allow=None):
        sc = self.bm.get_scores(self.cut(q))
        if allow is not None:
            sc = np.where(allow, sc, -1e9)     # 没点名的公司压到底
        idx = np.argsort(sc)[::-1]
        out = []
        for i in idx:
            if len(out) >= topk:
                break
            if sc[i] <= 0:
                break
            out.append((int(i), float(sc[i])))
        return out

    def search_vector(self, q, topk, allow=None):
        v = self.embed_query(q)
        sc = self.vecs @ v                     # 已归一化 -> 点积即余弦
        if allow is not None:
            sc = np.where(allow, sc, -1e9)
        idx = np.argsort(sc)[::-1][:topk]
        return [(int(i), float(sc[i])) for i in idx]

    def companies_in(self, q):
        """查询里提到了哪几家公司（认简称、别名、股票代码）"""
        hit = set()
        for n in self.names:
            if n in q:
                hit.add(n)
        for code, n in self.code2name.items():
            if code in q:
                hit.add(n)
        for alias, n in self.alias.items():
            if alias in q:
                hit.add(n)
        return hit

    def search(self, q, topk=5, pool=30, mode="hybrid", filter_company=True):
        """mode: hybrid | bm25 | vector"""
        # 查询里点了名的公司，就只在它们里面搜。
        # 不加这一步的话，BM25 会把「山西汾酒消费税税率」答成口子窖——
        # 因为「消费税」「税率」是 13 家共有的高频词，把公司名压过去了；
        # BM25 本身不懂「山西汾酒」是个限定条件。
        only = self.companies_in(q) if filter_company else set()
        allow = None
        if only:
            allow = np.array([c["name"] in only for c in self.chunks])

        cand = {}

        if mode in ("hybrid", "bm25") and self.bm is not None:
            for rank, (i, s) in enumerate(self.search_bm25(q, pool, allow), 1):
                h = cand.setdefault(i, Hit(self.chunks[i], 0.0))
                h.rank_bm25, h.s_bm25 = rank, s
                h.from_.append("BM25")

        if mode in ("hybrid", "vector") and self.want_vector:
            for rank, (i, s) in enumerate(self.search_vector(q, pool, allow), 1):
                h = cand.setdefault(i, Hit(self.chunks[i], 0.0))
                h.rank_vec, h.s_vec = rank, s
                h.from_.append("向量")

        for h in cand.values():
            sc = 0.0
            if h.rank_bm25:
                sc += 1.0 / (RRF_K + h.rank_bm25)
            if h.rank_vec:
                sc += 1.0 / (RRF_K + h.rank_vec)
            h.score = sc

        out = sorted(cand.values(), key=lambda h: -h.score)[:topk]
        return out


# ---------------- 命令行自测 ----------------
if __name__ == "__main__":
    r = Retriever()
    print()
    qs = sys.argv[1:] or ["消费税税率是多少", "贵州茅台营业收入", "白酒从量计征"]
    for q in qs:
        print("=" * 76)
        print("  Q: %s" % q)
        print("=" * 76)
        for h in r.search(q, topk=5):
            c = h.chunk
            tag = "+".join(h.from_)
            rb = "B%d" % h.rank_bm25 if h.rank_bm25 else "  -"
            rv = "V%d" % h.rank_vec if h.rank_vec else "  -"
            print("  %.4f  %-4s %-8s p%-5s  %s" % (h.score, rb + rv, c["name"], c["page"], tag))
            body = [l for l in c["text"].split("\n")[1:] if l.strip()][:2]
            for l in body:
                print("           %s" % l[:66])
        print()
