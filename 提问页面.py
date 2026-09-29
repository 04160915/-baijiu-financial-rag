# -*- coding: utf-8 -*-
r"""
步骤⑥：提问页面（streamlit）
------------------------------------------------------------
跑法：双击「打开提问页面.bat」，浏览器会自动打开
      （或命令行 streamlit run 提问页面.py）

页面做三件事：
  1. 你提问 -> 混合检索（BM25 + 向量）捞出最相关的若干块
  2. 把答案连同【出处】一起给出来：公司 / 页码 / 章节
  3. 展开能看到原文块，以及它是被哪一路（关键词还是向量）捞到的
     —— 这一栏是作业要的「逐题记录召回了哪些块」

关于「答案」：如果环境里有 DEEPSEEK_API_KEY，会让模型基于
  捞到的原文写一段答案（并强制标注页码）；没有就只列原文，
  也完全能用 —— 判据是课件第 104 页那句：判断交模型，取数交检索。
"""
import os, sys, json
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from 检索 import Retriever

st.set_page_config(page_title="白酒财报问答", page_icon="📊", layout="wide")


@st.cache_resource(show_spinner="正在加载索引 ...")
def get_r():
    return Retriever(verbose=True)


r = get_r()

# ---------------- 侧边栏 ----------------
with st.sidebar:
    st.header("检索设置")
    mode_label = st.radio(
        "检索方式",
        ["混合（关键词 + 向量）", "只看关键词（BM25）", "只看向量（语义）"],
        index=0,
        help="混合用 RRF 把两路结果按名次融合，一般最稳")
    mode = {"混合（关键词 + 向量）": "hybrid",
            "只看关键词（BM25）": "bm25",
            "只看向量（语义）": "vector"}[mode_label]
    topk = st.slider("取回几块", 3, 15, 6)
    pool = st.slider("每路各取多少候选", 10, 60, 30, step=10)

    st.divider()
    st.caption("索引状态")
    st.caption("· 切片 %d 块" % len(r.chunks))
    st.caption("· BM25 %s" % ("✓ 就绪" if r.bm is not None else "✗ 不可用"))
    st.caption("· 向量 %s" % ("✓ 就绪" if r.want_vector else "✗ 未就绪（还在算？）"))

# ---------------- 主区 ----------------
st.title("📊 白酒行业财报问答")
st.caption("13 家白酒上市公司 · 2026 年半年度报告 · "
           "答案均标注出处（公司 / 页码 / 章节）")

with st.form("q"):
    q = st.text_input("问点什么：", placeholder="例：五粮液上半年的消费税是多少？")
    ok = st.form_submit_button("检索", use_container_width=True)

EXAMPLES = [
    "消费税的税率是多少？怎么算的？",
    "贵州茅台上半年营业收入是多少？",
    "哪家公司的消费税占营业收入比重最高？",
    "公司怎么描述白酒消费税政策变化的风险？",
]
if not ok and not q:
    st.write("**试试这几个：**")
    cols = st.columns(2)
    for i, e in enumerate(EXAMPLES):
        if cols[i % 2].button(e, use_container_width=True):
            q, ok = e, True

if ok and q:
    if mode != "bm25" and not r.want_vector:
        st.warning("向量索引还没算完（或没生成），这里会自动退回到只用关键词。"
                   "等 `向量化.py` 跑完再刷新本页即可。")
        mode = "bm25"

    with st.spinner("检索中 ..."):
        hits = r.search(q, topk=topk, pool=pool, mode=mode)

    if not hits:
        st.error("没找到相关内容。换个说法试试？")
        st.stop()

    st.subheader("检索到的原文块")

    # ---- 可选：让模型基于原文写答案 ----
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        with st.expander("让模型基于上面这些原文写一段答案（带页码引用）", expanded=True):
            if st.button("生成答案"):
                ctx = "\n\n".join(
                    "[出处：%s %s 第%s页 %s]\n%s"
                    % (h.chunk["name"], h.chunk["code"], h.chunk["page"],
                       h.chunk["section"], h.chunk["text"])
                    for h in hits)
                prompt = (
                    "你是财务分析助手。只依据下面给出的财报原文回答问题，"
                    "不许编造数字。引用时写明「公司+页码」。"
                    "如果原文里没有答案，就直接说没有找到。\n\n"
                    "问题：%s\n\n原文：\n%s" % (q, ctx))
                try:
                    from openai import OpenAI
                    cl = OpenAI(api_key=key, base_url="https://api.deepseek.com")
                    with st.spinner("模型生成中 ..."):
                        resp = cl.chat.completions.create(
                            model="deepseek-chat",
                            messages=[{"role": "user", "content": prompt}],
                            temperature=0)
                    st.markdown(resp.choices[0].message.content)
                except Exception as e:
                    st.error("调用模型失败：%s" % e)
    else:
        st.caption("（想做「模型写答案」这一步的话，把 DEEPSEEK_API_KEY "
                   "设成环境变量再刷新本页）")

    # ---- 逐块展示，带出处 ----
    for n, h in enumerate(hits, 1):
        c = h.chunk
        way = " + ".join(h.from_) or "—"
        badge = " ".join(filter(None, [
            "关键词#%d" % h.rank_bm25 if h.rank_bm25 else "",
            "向量#%d" % h.rank_vec if h.rank_vec else ""]))
        title = "%d. %s %s · 第%s页 · %s" % (
            n, c["name"], c["code"], c["page"], c.get("section") or "—")
        with st.expander(title, expanded=(n <= 3)):
            st.caption("被哪一路捞到：%s　｜　融合得分 %.4f　｜　块类型 %s　｜　%d 字"
                       % (badge or "—", h.score,
                          "表格" if c["kind"] == "table" else "正文", c["chars"]))
            st.code(c["text"], language=None)
