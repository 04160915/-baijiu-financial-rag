# -*- coding: utf-8 -*-
r"""
财报 PDF -> 结构化文本 + 表格（行列还原）
------------------------------------------------------------
输出：D:\财报库\extracted\{代码}_{简称}.json
每个文件包含若干 block：
  {"page": 页码, "section": "第十节 财务报告", "kind": "text"|"table", "content": ...}

表格归一化规则（针对中文财报的两类通病）：
  1) 合并表头导致「表头文字比数据右移一列」-> 表头标签与数值列按顺序对齐
  2) 表格跨页断开 -> 相邻页的同构表格合并，去掉重复表头
"""
import os, re, sys, json, time
import pdfplumber

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = r"D:\财报库"
DST = os.path.join(SRC, "extracted")

NUM = re.compile(r"^-?[\d,]+(\.\d+)?$")
# 章节标题：第一节 / 第二节 ...（也可能写成「一、」）
SEC = re.compile(r"^第[一二三四五六七八九十百]+节\s*(.{0,30})")
# 附注小标题：「45、税金及附加」「六、税项」「（3）其他」
#   要求标题里不含数字，否则会把「1、货币资金 119,088,869,922.03」误当标题
NOTE = re.compile(r"^\s*(?:\d{1,3}[、.]|[一二三四五六七八九十]{1,3}[、.]"
                  r"|（\d{1,2}）)\s*([^\d]{2,24})$")


def page_notes(page):
    """
    扫本页所有文字行，返回 [(纵坐标 top, 附注标题)]，按从上到下排序。
    给表格配表名用 —— 财报的附注表表头常常只写「项目」，
    真名（「45、税金及附加」）在表上方那行文字里。
    """
    out = []
    try:
        lines = page.extract_text_lines()
    except Exception:
        return out
    for ln in lines:
        t = (ln.get("text") or "").strip()
        if "..." in t or "…" in t:
            continue
        m = NOTE.match(t)
        if m:
            out.append((ln.get("top", 0), m.group(1).strip()))
    out.sort(key=lambda x: x[0])
    return out
# 像数字（含千分位、百分号、括号负数）
def is_num(s):
    t = (s or "").strip().replace(",", "").replace(" ", "")
    if not t:
        return False
    if t in ("-", "—", "－", "/"):
        return False
    return bool(re.fullmatch(r"-?\d+(\.\d+)?%?", t))


def norm_table(raw):
    """把 pdfplumber 的原始表格归一化成 [表头行, 数据行...]"""
    if not raw or len(raw) < 2:
        return None
    ncol = max(len(r) for r in raw)
    rows = [[(r[c] or "").replace("\n", " ").strip() if c < len(r) else ""
             for c in range(ncol)] for r in raw]

    # 表头行：第一个「有 >=2 个非空单元格」的行
    hi = None
    for i, r in enumerate(rows):
        if sum(1 for c in r if c) >= 2:
            hi = i
            break
    if hi is None:
        return None
    # 「项目」列 = 文字（非数字）内容最多的那一列
    #   注意：不能用「非空最多」——数值列往往比标签列填得更满（子项目留空），会认错
    text_cnt = [sum(1 for r in rows if r[c] and not is_num(r[c])) for c in range(ncol)]
    if max(text_cnt) < 2:
        return None
    label_col = text_cnt.index(max(text_cnt))

    header, body = rows[hi], rows[hi + 1:]
    if not body:
        return None

    # 数值列 = 正文里出现过数字的列（排除项目列）
    val_cols = [c for c in range(ncol)
                if c != label_col and any(is_num(r[c]) for r in body)]
    if not val_cols:
        return None

    # 表头标签：表头行里非空的、非项目列的，按左到右顺序（位置映射）
    #   合并表头单元格会被 pdfplumber 放到右边一格，所以先按位置取，
    #   取不到再往右一格、往左一格找。
    labels = [header[c] for c in range(ncol) if c != label_col and header[c]]
    if len(labels) > len(val_cols):          # 多余标签（合并单元格重复）
        labels = labels[-len(val_cols):]
    labels += [""] * (len(val_cols) - len(labels))
    for i, c in enumerate(val_cols):
        if not labels[i]:
            for cc in (c + 1, c - 1):
                if 0 <= cc < ncol and header[cc]:
                    labels[i] = header[cc]
                    break

    # 续行合并：单元格里文字换行时，pdfplumber 会把一行拆成 3 个物理行
    #     归属于上市公司股东的扣除非经常性损 |      |      |      <- 标签前半
    #                                       | 2,150,029,442.57 |..|  <- 数值
    #     益的净利润（元）                    |      |      |      <- 标签后半
    #   规则：有标签没数值的行，接到上一行标签后面；没标签有数值的行，取挂着的标签。
    TAIL = re.compile(r"^\s*(（?\d+[）)、.]|[一二三四五六七八九十]+[、.]|其中|减|加|其中)")
    out = [[header[label_col]] + labels]
    pend = ""
    for r in body:
        lab = r[label_col]
        vals = [r[c] for c in val_cols]
        has_val = any(v for v in vals)
        if lab and not has_val:
            if out and any(out[-1][1:]) and not TAIL.match(lab):
                out[-1][0] += lab          # 上一行标签的续行
            else:
                pend += lab
        elif has_val:
            out.append([pend + lab] + vals)
            pend = ""
        # 全空行直接丢弃
    return out


def same_header(a, b):
    """判断两个表头是否同一张表（跨页续表）"""
    if not a or not b or len(a) != len(b):
        return False
    return sum(1 for x, y in zip(a, b) if x and y and (x in y or y in x)) >= len(a) - 1


def looks_like_table_text(page):
    """预筛：这一页有没有表格的样子"""
    t = page.extract_text() or ""
    if t.count(",") < 6:
        return False
    hits = sum(1 for line in t.split("\n") if sum(1 for w in line.split() if is_num(w)) >= 2)
    return hits >= 4


def extract(path, code, name):
    blocks = []
    section = ""
    with pdfplumber.open(path) as pdf:
        npages = len(pdf.pages)
        pending = None          # 上一页没合并完的表

        for i, page in enumerate(pdf.pages):
            pno = i + 1
            text = page.extract_text() or ""

            # 章节：扫全页找「第X节 ...」标题
            #   ① 取本页最后一个 —— 新章节常从页中间开始，之后的内容都属新章节
            #   ② 跳过目录行（带「......」引导线）
            for line in text.split("\n"):
                s = line.strip()
                if SEC.match(s) and "..." not in s and "…" not in s:
                    section = s[:40]

            if text.strip():
                blocks.append({"page": pno, "section": section,
                               "kind": "text", "content": text.strip()})

            if not looks_like_table_text(page):
                continue

            try:
                tabs = page.find_tables()
            except Exception:
                continue
            notes = page_notes(page)

            for t in tabs:
                nt = norm_table(t.extract())
                if not nt or len(nt) < 3:
                    continue

                # 给这张表配一个表名：表上方【最近】的那个附注小标题
                ttop = t.bbox[1] if getattr(t, "bbox", None) else 0
                tname = ""
                for ntop, nname in notes:
                    if ntop < ttop:
                        tname = nname       # 一直取到表格上方的最后一个
                    else:
                        break

                # 跨页合并
                if pending is not None and same_header(pending["rows"][0], nt[0]):
                    pending["rows"].extend(nt[1:])
                    pending["pages"][-1] = pno
                    pending = None
                    continue

                if pending is not None:
                    blocks.append(pending)
                    pending = None
                pending = {"page": pno, "section": section, "kind": "table",
                           "pages": [pno], "rows": nt, "note": tname}

        if pending is not None:
            blocks.append(pending)

    return {"code": code, "name": name, "file": os.path.basename(path),
            "pages": npages, "blocks": blocks}


def main(files):
    os.makedirs(DST, exist_ok=True)
    print("=" * 72)
    print("  提取表格 + 文字")
    print("=" * 72)
    for fn in files:
        code, name = fn.split("_")[0], fn.split("_")[1]
        t0 = time.time()
        r = extract(os.path.join(SRC, fn), code, name)
        ntab = sum(1 for b in r["blocks"] if b["kind"] == "table")
        nrow = sum(len(b["rows"]) for b in r["blocks"] if b["kind"] == "table")
        out = os.path.join(DST, "%s_%s.json" % (code, name))
        with open(out, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
        print("  %-14s %3d页  block %3d (表 %2d, 共 %4d 行)  %4.1f秒"
              % (name, r["pages"], len(r["blocks"]), ntab, nrow, time.time() - t0))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main(sys.argv[1:])
    else:
        # 不带参数就跑【全部】PDF。
        # 以前这里写死了三个文件名（当时是拿来试的），
        # 结果不带参数跑只会处理 3 家，剩下 10 家悄没声地漏掉 ——
        # 而输出看起来完全正常。改成扫目录，省得再踩。
        main(sorted(f for f in os.listdir(SRC) if f.endswith(".pdf")))
