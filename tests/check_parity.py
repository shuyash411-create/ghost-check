#!/usr/bin/env python3
"""Check that the web app's classifier (web/ghost-check.js + web/model/) scores
exactly like the CLI (ghost_check.py + model/ghost_check.joblib).

Compares, on a set of texts:
  1. the word and char_wb n-grams against scikit-learn's own analyzers
  2. words, sections, every section score, the document score (to 1e-5),
     verdict and reason text

Needs Node.js on PATH. Run train/export_web.py first if the model changed.
Usage:  python tests/check_parity.py
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import ghost_check as gc  # noqa: E402

TRICKY = (
    "“Smart quotes” and ‘single ones’ — em dashes – en dashes… ellipses.\n"
    "A hyphen-\nated word from a PDF, tabs\there, NBSP and narrow ones, café, naïve, ﬁ ligature.\n\n"
    "Numbers like 3.14, 1,000 and 2nd; under_scores; e-mail; C++ and #hashtags; emoji 😀 too! "
    "मैं कल बाज़ार गया था। Ünïcödé ÅNGSTRÖM straße. "
) * 12


def samples() -> dict[str, str]:
    s = {"tricky.txt": TRICKY, "short.txt": "Only a handful of words here."}
    for folder in ("ai", "human"):
        for p in sorted((ROOT / "train" / "sanity" / folder).glob("*.txt")):
            s[f"{folder}/{p.name}"] = p.read_text(encoding="utf-8")
    s["ai_samples.txt"] = (ROOT / "train" / "ai_samples.txt").read_text(encoding="utf-8")
    return s


JS = r"""
const fs = require("fs");
const GC = require(process.argv[1]);
const dir = process.argv[2];
const meta = JSON.parse(fs.readFileSync(dir + "/model.json", "utf8"));
const buf = fs.readFileSync(dir + "/weights.bin");
const model = GC.createModel(meta, buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const out = {};
for (const [name, text] of Object.entries(input)) {
  const r = GC.scoreText(text, model);
  const chunks = r.chunks || [];
  out[name] = { words: r.words, score: r.score, verdict: r.verdict, reason: r.reason,
    section_scores: r.section_scores || [], chunks,
    word: chunks.map((c) => GC.wordNgrams(c)), char: chunks.map((c) => GC.charWbNgrams(c)) };
}
process.stdout.write(JSON.stringify(out));
"""


def main() -> int:
    model = gc.load_model()
    parts = dict(model["vectorizer"].transformer_list)
    word_an, char_an = parts["word"].build_analyzer(), parts["char"].build_analyzer()
    texts = samples()
    js = json.loads(subprocess.run(
        ["node", "-e", JS, str(ROOT / "web" / "ghost-check.js"), str(ROOT / "web" / "model")],
        input=json.dumps(texts), capture_output=True, text=True, check=True).stdout)

    failures = 0
    for name, text in texts.items():
        py = gc.score_text(text, model)
        j = js[name]
        problems = []
        py_chunks = gc.chunk_text(gc.normalise(text)) if py["score"] is not None else []
        if py_chunks != j["chunks"]:
            problems.append("sections differ")
        else:
            for i, c in enumerate(py_chunks):
                if word_an(c) != j["word"][i]:
                    problems.append(f"word n-grams differ in section {i + 1}")
                if char_an(c) != j["char"][i]:
                    problems.append(f"char n-grams differ in section {i + 1}")
        for k in ("words", "verdict", "reason"):
            if py.get(k) != j[k]:
                problems.append(f"{k}: py={py.get(k)!r} js={j[k]!r}")
        if py["score"] is not None:
            if abs(py["score"] - j["score"]) > 1e-5:
                problems.append(f"score: py={py['score']:.7f} js={j['score']:.7f}")
            if len(py["section_scores"]) != len(j["section_scores"]) or \
                    any(abs(a - b) > 1e-3 for a, b in zip(py["section_scores"], j["section_scores"])):
                problems.append("section scores differ")
        score = "    -" if py["score"] is None else f"{100 * py['score']:5.1f}"
        print(f"{'FAIL' if problems else 'ok  '} {name:<40} {score}  {py['verdict']}")
        for p in problems:
            print(f"       {p}")
        failures += bool(problems)
    print("PARITY OK" if not failures else f"{failures} texts differ")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
