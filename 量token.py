# -*- coding: utf-8 -*-
"""量一下真实切片的 token 长度分布，决定 max_length 和批处理策略"""
import sys, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from transformers import AutoTokenizer
import transformers.utils.logging as L

L.disable_progress_bar()
L.set_verbosity_error()

MODEL = (r"C:\Users\沈祥暄\.cache\modelscope\models"
         r"\Qwen--Qwen3-Embedding-0.6B\snapshots\master")
chunks = [json.loads(l) for l in open(r"D:\财报库\chunks.jsonl", encoding="utf-8")]
tok = AutoTokenizer.from_pretrained(MODEL)

print("编码 %d 块 ..." % len(chunks))
tk = [len(tok(c["text"])["input_ids"]) for c in chunks]
tk.sort()
n = len(tk)


def p(q):
    return tk[min(n - 1, int(n * q))]


print()
print("  token 长度分布")
print("  最小 %d   中位 %d   平均 %d   最大 %d" % (tk[0], p(.5), sum(tk) // n, tk[-1]))
print()
for q in (.5, .75, .9, .95, .98, .99, 1.0):
    print("  第 %3.0f 百分位: %5d tokens" % (q * 100, p(q)))
print()
print("  若截断到 512  : 会有 %d 块被截（%.0f%%）"
      % (sum(1 for x in tk if x > 512), 100 * sum(1 for x in tk if x > 512) / n))
print("  若截断到 768  : 会有 %d 块被截（%.0f%%）"
      % (sum(1 for x in tk if x > 768), 100 * sum(1 for x in tk if x > 768) / n))
print()
tot = sum(tk)
print("  全部 token 总量        %d" % tot)
print("  按最长补齐(2048×N)     %d  (浪费 %.0f%%)" % (2048 * n, 100 * (1 - tot / (2048 * n))))
print("  按 512 补齐(16块/批)   约 %d" % (512 * n))
print("  按 768 补齐(16块/批)   约 %d" % (768 * n))
print()
CUT = 768
kept = sum(min(x, CUT) for x in tk)
print("  按长度排序分批 + 截到 %d : 约 %d token（是理论下限量级）" % (CUT, kept))
print("  相比 512×N 省 %.0f%%" % (100 * (1 - kept / (512 * n))))
