"""把整理好的字幕 (sentences.tsv) 加工成網頁用的資料檔。

用法: python tools/build_video.py <video_id> "<title>" "<channel>"
輸入: work/<id>/sentences.tsv  (秒數 \t 日文 \t 中文)
      work/<id>/stream.json    (原始字幕的詞級時間，用來校正每句的開始時間)
      data/jlpt/n1~n5.csv      (JLPT 單字表)
      data/glossary_zh.tsv     (單字中文意思，所有影片共用)
輸出: docs/videos/<id>.json、更新 docs/videos/index.json
      work/<id>/missing_zh.tsv (還沒有中文意思的單字)
"""
import warnings; warnings.filterwarnings("ignore")
import csv, json, re, sys, pathlib, unicodedata
from sudachipy import dictionary, tokenizer

ROOT = pathlib.Path(__file__).resolve().parent.parent
vid, title, channel = sys.argv[1], sys.argv[2], sys.argv[3]
work = ROOT / "work" / vid

# ---------- 工具 ----------
def kata2hira(s):
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)

def has_kanji(s):
    return any("一" <= c <= "鿿" or c == "々" for c in s)

def is_kana(s):
    return all("ぁ" <= c <= "ゖ" or "ァ" <= c <= "ヺ" or c == "ー" for c in s)

def norm(s):
    s = re.sub(r"\[[^\]]*\]", "", s)
    return "".join(c for c in unicodedata.normalize("NFKC", s)
                   if unicodedata.category(c)[0] in "LN")

def kanji_of(s):
    return {c for c in s if has_kanji(c)}

# ---------- JLPT 表 ----------
# entries: (level, expression, reading, english)
jlpt, by_reading = {}, {}
for n in (5, 4, 3, 2, 1):  # 簡單的等級優先
    with open(ROOT / "data/jlpt" / f"n{n}.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            exprs = [e.strip() for e in re.split(r"[;、,]", row["expression"]) if e.strip()]
            reads = [kata2hira(re.sub(r"\s*\(.*?\)\s*", "", r)).strip() for r in re.split(r"[;、,]", row["reading"]) if r.strip()]
            for k, expr in enumerate(exprs):
                rd = reads[min(k, len(reads) - 1)] if reads else ""
                if expr.endswith("する") is False and rd.endswith("する") and len(rd) > 2:
                    rd = rd[:-2]
                e = (n, expr, rd, row["meaning"])
                jlpt.setdefault(expr, []).append(e)
                by_reading.setdefault(rd, []).append(e)

# 人工修正表：全域 data/overrides.tsv + 每支影片 work/<id>/overrides.tsv
# 格式: lemma \t skip | N1~N5 | N0 | =另一個lemma
overrides = {}
for p in (ROOT / "data/overrides.tsv", work / "overrides.tsv"):
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if "\t" in line and not line.startswith("#"):
                k, v = line.split("\t", 1)
                overrides[k.strip()] = v.strip()

glossary = {}
gpath = ROOT / "data/glossary_zh.tsv"
if gpath.exists():
    for line in gpath.read_text(encoding="utf-8").splitlines():
        if "\t" in line:
            k, v = line.split("\t", 1)
            glossary[k] = v

# ---------- 斷詞 ----------
tok = dictionary.Dictionary(dict="core").create()
MODE = tokenizer.Tokenizer.SplitMode.C
KEEP = {"名詞", "動詞", "形容詞", "形状詞", "副詞", "連体詞", "接続詞", "代名詞", "感動詞"}
AUX_VERBS = {"いる", "ある", "する", "なる", "くる", "いく", "おる", "しまう", "もらう",
             "いただく", "くれる", "やる", "みる", "おく", "ござる"}

def reading_of(text):
    return kata2hira("".join(x.reading_form() for x in tok.tokenize(text, MODE)))

def match(lemma, lr):
    """用 lemma 和讀音在 JLPT 表找最合適的一筆"""
    by_r = by_reading.get(lr, [])
    # 1. 寫法和讀音都一樣
    for e in by_r:
        if e[1] == lemma:
            return e
    # 2. 讀音一樣、漢字相同（送り仮名不同，如 終る/終わる）
    if kanji_of(lemma):
        for e in by_r:
            if kanji_of(e[1]) and kanji_of(e[1]) == kanji_of(lemma):
                return e
    # 3. 純假名的詞：讀音只對到一個等級才採用（避免同音字誤判）
    if is_kana(lemma) and len(lemma) >= 2:
        if by_r and len({e[0] for e in by_r}) == 1:
            return by_r[0]
        return None
    # 4. 寫法一樣但讀音不同（多音字），採用寫法
    if kanji_of(lemma) and lemma in jlpt:
        return jlpt[lemma][0]
    return None

def lookup(m):
    """回傳 (lemma, reading, level, en) 或 None"""
    pos = m.part_of_speech()
    lemma = m.dictionary_form()
    ov = overrides.get(lemma) or overrides.get(m.surface())
    if ov == "skip":
        return None
    if ov and ov.startswith("="):
        lemma = ov[1:]
    elif pos[0] not in KEEP or pos[1] in ("固有名詞", "数詞") or pos[2] == "フィラー":
        return None
    elif lemma in AUX_VERBS and not has_kanji(m.surface()):
        return None
    if len(lemma) < 2 and not has_kanji(lemma):
        return None
    lr = kata2hira(m.reading_form()) if m.surface() == lemma else reading_of(lemma)
    e = match(lemma, lr)
    if ov and re.fullmatch(r"N[0-5]", ov):
        return lemma, (e[2] if e else lr), int(ov[1]), (e[3] if e else "")
    if e:
        return lemma, lr, e[0], e[3]
    return lemma, lr, 0, ""

# ---------- 讀字幕 ----------
rows = []
for line in (work / "sentences.tsv").read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    t, ja, zh = line.split("\t")
    rows.append([float(t), ja.strip(), zh.strip()])

# ---------- 用原始字幕校正開始時間 ----------
stream = json.loads((work / "stream.json").read_text(encoding="utf-8"))
chars, times = [], []
for ms, w in stream:
    for c in norm(w):
        chars.append(c); times.append(ms / 1000)
timeline = "".join(chars)
prev_t, prev_pos = 0.0, 0
for r in rows:
    hint, key = r[0], norm(r[1])[:3]
    best = None
    if len(key) >= 2:
        pos = timeline.find(key, prev_pos)
        while pos != -1 and times[pos] <= hint + 20:
            if times[pos] >= hint - 1 and times[pos] >= prev_t:
                best = pos; break
            pos = timeline.find(key, pos + 1)
    if best is not None:
        r[0] = times[best]; prev_pos = best + 1
    r[0] = max(r[0], prev_t)
    prev_t = r[0]

# 幾句擠在同一時間點時，依字數比例把時間分開
i = 0
while i < len(rows):
    j = i
    while j + 1 < len(rows) and rows[j + 1][0] - rows[i][0] < 0.5:
        j += 1
    if j > i:
        t0 = rows[i][0]
        t1 = rows[j + 1][0] if j + 1 < len(rows) else t0 + 3 * (j - i + 1)
        span = min(t1 - t0, 0.2 * sum(len(rows[k][1]) for k in range(i, j + 1)) + 1.5 * (j - i + 1))
        total = sum(len(rows[k][1]) for k in range(i, j + 1))
        acc = 0
        for k in range(i, j + 1):
            rows[k][0] = t0 + span * acc / total
            acc += len(rows[k][1])
    i = j + 1

# ---------- 組資料 ----------
vocab, vindex, sentences = [], {}, []
for i, (t, ja, zh) in enumerate(rows):
    nxt = rows[i + 1][0] if i + 1 < len(rows) else t + 8
    end = min(nxt, t + 2.5 + 0.25 * len(ja))
    toks = []
    for m in tok.tokenize(ja, MODE):
        s = m.surface()
        furi = kata2hira(m.reading_form()) if has_kanji(s) else ""
        hit = lookup(m)
        vi = -1
        if hit:
            lemma, rd, lv, en = hit
            if lemma not in vindex:
                vindex[lemma] = len(vocab)
                vocab.append({"w": lemma, "r": rd, "lv": lv, "en": en,
                              "zh": glossary.get(lemma, ""), "n": 0, "first": i})
            vi = vindex[lemma]
            vocab[vi]["n"] += 1
        toks.append([s, furi, vi])
    sentences.append({"s": round(t, 2), "e": round(end, 2), "ja": ja, "zh": zh, "t": toks})

out = {"id": vid, "title": title, "channel": channel, "sentences": sentences, "vocab": vocab}
(ROOT / "docs/videos").mkdir(parents=True, exist_ok=True)
(ROOT / "docs/videos" / f"{vid}.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

idx_path = ROOT / "docs/videos/index.json"
idx = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else []
idx = [v for v in idx if v["id"] != vid] + [{"id": vid, "title": title, "channel": channel,
                                              "count": len(sentences), "words": len(vocab)}]
idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")

missing = [v for v in vocab if not v["zh"]]
(work / "missing_zh.tsv").write_text("\n".join(f'{v["w"]}\t{v["r"]}\tN{v["lv"] or "-"}\t{v["en"]}' for v in missing), encoding="utf-8")
from collections import Counter
print(len(sentences), "sentences,", len(vocab), "words,", len(missing), "missing zh")
print("levels:", sorted(Counter(v["lv"] for v in vocab).items()))
