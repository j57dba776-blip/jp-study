"""把 YouTube json3 自動字幕攤平成「每個詞 + 時間」的串流，並輸出可閱讀的逐段文字。
用法: python flatten.py work/<id>
產出: <dir>/stream.json (詞級時間), <dir>/raw.txt (給人/AI 閱讀的逐段字幕)"""
import json, sys, pathlib
d = pathlib.Path(sys.argv[1])
data = json.loads((d / "raw.json3").read_text(encoding="utf-8"))
stream, lines = [], []
for ev in data.get("events", []):
    segs = ev.get("segs")
    if not segs:
        continue
    t0 = ev["tStartMs"]
    text = "".join(s.get("utf8", "") for s in segs).replace("\n", "")
    if not text.strip():
        continue
    for s in segs:
        w = s.get("utf8", "").replace("\n", "")
        if w:
            stream.append([t0 + s.get("tOffsetMs", 0), w])
    lines.append(f"[{t0/1000:7.1f}] {text}")
(d / "stream.json").write_text(json.dumps(stream, ensure_ascii=False), encoding="utf-8")
(d / "raw.txt").write_text("\n".join(lines), encoding="utf-8")
print(len(stream), "words,", len(lines), "lines,", sum(len(w) for _, w in stream), "chars")
