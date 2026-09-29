# -*- coding: utf-8 -*-
"""对照测速：fp32 vs bf16、不同 max_length 下每秒能编几块"""
import sys, json, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoTokenizer
import transformers.utils.logging as L

L.disable_progress_bar()
L.set_verbosity_error()

MODEL = (r"C:\Users\沈祥暄\.cache\modelscope\models"
         r"\Qwen--Qwen3-Embedding-0.6B\snapshots\master")
SRC = r"D:\财报库\chunks.jsonl"

chunks = [json.loads(l) for l in open(SRC, encoding="utf-8")]
sample = [c["text"] for c in chunks[1000:1032]]
lens = [len(t) for t in sample]
print("样本 %d 块，字数 %d~%d（中位 %d）"
      % (len(sample), min(lens), max(lens), sorted(lens)[len(lens) // 2]))
print()
tok = AutoTokenizer.from_pretrained(MODEL, padding_side="left")
torch.set_num_threads(14)

print("%-6s %-8s %-6s %9s %11s" % ("精度", "maxlen", "batch", "耗时秒", "块/秒"))
print("-" * 46)
for dt, dname in [(torch.float32, "fp32"), (torch.bfloat16, "bf16")]:
    m = AutoModel.from_pretrained(MODEL, dtype=dt).eval()
    for ml in (512, 1024):
        t = time.time()
        for i in range(0, len(sample), 16):
            b = tok(sample[i:i + 16], padding=True, truncation=True,
                    max_length=ml, return_tensors="pt")
            with torch.no_grad():
                o = m(**b)
            F.normalize(o.last_hidden_state[:, -1].float(), p=2, dim=1).numpy()
        el = time.time() - t
        print("%-6s %-8d %-6d %9.1f %11.2f" % (dname, ml, 16, el, len(sample) / el))
    del m
