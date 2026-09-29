# -*- coding: utf-8 -*-
r"""
步骤④：把 chunks.jsonl 里的每一块转成向量（Qwen3-Embedding-0.6B）
------------------------------------------------------------
输入：D:\财报库\chunks.jsonl
输出：D:\财报库\vectors.npy       (N, 1024) float32，已 L2 归一化，按原顺序
      D:\财报库\vectors_ids.json 与 .npy 行号一一对应的 id 列表

要点：
  * Qwen3-Embedding 用【最后一个 token】的隐状态做句向量（不是 [CLS]）
  * tokenizer 必须 padding_side="left"，否则最后一位是 padding 不是正文
  * 文档侧不加前缀；【查询侧】才加 "Instruct: ...\nQuery: "
  * 已归一化 -> 余弦相似度 = 点积，检索时直接 @ 就完事

性能上的三个决定（实测过的）：
  * bf16 比 fp32 略快且省一半内存（这台机器 CPU 跑 0.6B 是慢活，
    实测约 0.6 块/秒，全量要 2 小时上下）
  * max_length 用 768 —— token 中位数 351，切到 768 只损失 3% 的块
    （用 512 会切掉 9%，用 2048 则 82% 的算力花在补零上）
  * 按 token 长度【排序后分批】—— 同批长度接近，补零几乎为零，
    比乱序分批省一半以上算力。算完再按原下标写回。
  * 断点续传：每 SAVE_EVERY 批落一次盘，中断了重跑接着算
"""
import os, sys, json, time
import numpy as np
import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoTokenizer
import transformers.utils.logging as hf_logging

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
hf_logging.disable_progress_bar()      # tqdm 的方块字符 GBK 编不出会崩
hf_logging.set_verbosity_error()

MODEL = (r"C:\Users\沈祥暄\.cache\modelscope\models"
         r"\Qwen--Qwen3-Embedding-0.6B\snapshots\master")
SRC = r"D:\财报库\chunks.jsonl"
DST = r"D:\财报库\vectors.npy"
IDF = r"D:\财报库\vectors_ids.json"
PRG = r"D:\财报库\vectors_progress.json"

BATCH = 32           # 每批几块（排序后长度接近，批大一点更省）
MAXLEN = 768         # 最长 token 数
SAVE_EVERY = 10      # 每多少批落一次盘

print("=" * 72)
print("  步骤④ 向量化 —— Qwen3-Embedding-0.6B")
print("=" * 72)
t0 = time.time()

print("\n[1/4] 读切片 ...")
chunks = [json.loads(l) for l in open(SRC, encoding="utf-8")]
N = len(chunks)
print("      %d 块" % N)

print("\n[2/4] 载入模型 ...")
tok = AutoTokenizer.from_pretrained(MODEL, padding_side="left")
model = AutoModel.from_pretrained(MODEL, dtype=torch.bfloat16)
model.eval()
torch.set_num_threads(os.cpu_count() or 4)
print("      CPU 线程 %d，bf16" % torch.get_num_threads())

print("\n      量 token 长度并排序（这步只用 tokenizer，很快）...")
tl = [len(tok(c["text"])["input_ids"]) for c in chunks]
order = np.argsort(np.array(tl))          # 短 -> 长
print("      token：中位 %d，最大 %d；超过 %d 的有 %d 块（会截断）"
      % (sorted(tl)[N // 2], max(tl), MAXLEN, sum(1 for x in tl if x > MAXLEN)))
print("      载入+排序耗时 %.1f 秒" % (time.time() - t0))

# ---------- 断点续传 ----------
done = 0
vecs = np.zeros((N, 1024), dtype=np.float32)
if os.path.exists(DST) and os.path.exists(PRG):
    try:
        old = np.load(DST)
        prg = json.load(open(PRG, encoding="utf-8"))
        if old.shape[0] == N and prg.get("maxlen") == MAXLEN \
                and prg.get("batch") == BATCH and 0 < prg.get("done", 0) <= N:
            vecs = old
            done = prg["done"]
            print("\n      >> 发现已有进度 %d / %d，接着算" % (done, N))
    except Exception as e:
        print("\n      >> 旧进度读不了（%s），从头来" % str(e)[:60])


def embed_docs(texts):
    """文档侧：不加 Instruct 前缀"""
    b = tok(texts, padding=True, truncation=True, max_length=MAXLEN,
            return_tensors="pt")
    with torch.no_grad():
        out = model(**b)
    # 最后一个 token 的隐状态；bf16 不能直接 .numpy()，先 .float()
    v = F.normalize(out.last_hidden_state[:, -1].float(), p=2, dim=1)
    return v.numpy()


def save(upto):
    np.save(DST, vecs)
    json.dump([c["id"] for c in chunks], open(IDF, "w", encoding="utf-8"),
              ensure_ascii=False)
    json.dump({"done": upto, "maxlen": MAXLEN, "batch": BATCH},
              open(PRG, "w", encoding="utf-8"))


print("\n[3/4] 开始编码 ...")
nb = (N - done + BATCH - 1) // BATCH
print("      共 %d 批，每批 %d 块" % (nb, BATCH))
# 进度必须按 token 算，不能按块数：排序后前几批全是短块，
# 按块数算会得出「还剩 33 分钟」这种假象（实际要 1.7 小时）
tok_of = [min(x, MAXLEN) for x in tl]
tok_total = sum(tok_of)
tok_done = sum(tok_of[i] for i in order[:done])
# ⚠ 续传时 t0 是【本次】开始的时间，而 tok_done 里含上次已经算完的部分。
#   直接 tok_done/elapsed 当速度会严重虚高 —— 实测显示「1732 token/秒、
#   剩约 15 分」，而真实还剩 85 分钟。速度只能按【本次新算的】量算。
tok_new = 0
print("     总 token %d（截断后），预计 %.0f 分钟"
      % (tok_total, tok_total / 330 / 60))
print()
for k in range(nb):
    s = done + k * BATCH
    e = min(s + BATCH, N)
    idx = order[s:e]                       # 原下标
    vecs[idx] = embed_docs([chunks[i]["text"] for i in idx])
    dt = sum(tok_of[i] for i in idx)
    tok_done += dt
    tok_new += dt

    if (k + 1) % SAVE_EVERY == 0 or k == nb - 1:
        save(e)
        el = time.time() - t0
        rate = tok_new / el if el else 0
        eta = (tok_total - tok_done) / rate if rate else 0
        print("      块 %5d/%d   token %7d/%d (%4.1f%%)   已用 %4.0f分   剩约 %4.0f分   %.0f token/秒"
              % (e, N, tok_done, tok_total, 100 * tok_done / tok_total,
                 el / 60, eta / 60, rate), flush=True)

print("\n[4/4] 收尾 ...")
save(N)
n = np.linalg.norm(vecs, axis=1)
print("      向量矩阵 %s   %.1f MB" % (vecs.shape, vecs.nbytes / 1e6))
print("      归一化检查：模长 最小 %.4f / 最大 %.4f（都该是 1.0000）"
      % (n.min(), n.max()))
print("      NaN 个数：%d" % int(np.isnan(vecs).sum()))
print("\n  总耗时 %.1f 分钟" % ((time.time() - t0) / 60))
