#!/usr/bin/env python3
"""Real-world sanity check: 20 assignment/essay-style documents, none in training.

  human/  6 UK university assignments (BAWE) + 2 TOEFL essays (ETS), both from
          the held-out test split (never trained on, not used for thresholds),
          and 2 US State of the Union addresses (1965, 1994; never used at all)
  ai/     10 assignment-style texts written by Claude (a newer model than any in
          the training data) across subjects, levels and voices

The BAWE and ETS texts are licensed, so they are read from a Ghostbuster
checkout (paths in human_from_ghostbuster.txt) instead of being stored here.

Each text is rendered to a real PDF, then run through the CLI's PDF path
(pypdf extraction -> normalise -> sections -> classifier).
Usage:  python train/sanity/run_sanity.py --ghostbuster ../ghostbuster-data   (needs reportlab)
"""

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
import ghost_check as gc  # noqa: E402


def to_pdf(txt: Path, pdf: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    from xml.sax.saxutils import escape

    style = getSampleStyleSheet()["BodyText"]
    story = []
    for para in txt.read_text(encoding="utf-8").split("\n\n"):
        if para.strip():
            story += [Paragraph(escape(para.strip()).replace("\n", "<br/>"), style), Spacer(1, 6)]
    SimpleDocTemplate(str(pdf), pagesize=A4).build(story)


def first_words(text: str, max_words: int = 900) -> str:
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    out, n = [], 0
    for p in paras:
        out.append(p)
        n += len(p.split())
        if n >= max_words:
            break
    return "\n\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ghostbuster", type=Path, required=True, help="path to a ghostbuster-data checkout")
    args = ap.parse_args()
    model = gc.load_model()
    pdf_dir = HERE / "pdf"
    pdf_dir.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    for line in (HERE / "human_from_ghostbuster.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            src = args.ghostbuster / line.strip()
            name = f"{src.parent.name}_{src.stem}.txt"
            (tmp / name).write_text(first_words(src.read_text(encoding="utf-8", errors="ignore")), encoding="utf-8")
    rows = []
    for label in ("human", "ai"):
        files = sorted((HERE / label).glob("*.txt")) + (sorted(tmp.glob("*.txt")) if label == "human" else [])
        for txt in sorted(files, key=lambda p: p.name):
            pdf = pdf_dir / f"{label}_{txt.stem}.pdf"
            to_pdf(txt, pdf)
            r = gc.score_text(gc.extract_text(pdf), model)
            rows.append((label, txt.stem, r))
    lines = ["| True author | File | Words | AI-likelihood | Verdict | Correct |", "|---|---|---:|---:|---|:-:|"]
    fp = fn = 0
    for label, name, r in rows:
        flagged = r["verdict"] in ("Likely AI-written", "Possibly AI-written")
        ok = flagged == (label == "ai")
        fp += label == "human" and flagged
        fn += label == "ai" and not flagged
        score = "–" if r["score"] is None else f"{100 * r['score']:.0f}%"
        lines.append(f"| {label} | {name} | {r['words']} | {score} | {r['verdict']} | {'✓' if ok else '✗'} |")
        print(f"{label:<6} {name:<28} {score:>5}  {r['verdict']:<20} {r['reason']}")
    n_h = sum(1 for l, *_ in rows if l == "human")
    n_a = len(rows) - n_h
    summary = (f"\nAccuracy {len(rows) - fp - fn}/{len(rows)} · human wrongly flagged (Likely or Possibly AI) "
               f"{fp}/{n_h} · AI missed {fn}/{n_a}")
    print(summary)
    (HERE / "results.md").write_text("\n".join(lines) + "\n" + summary.strip() + "\n", encoding="utf-8")
    (HERE / "results.json").write_text(json.dumps({
        "documents": len(rows), "correct": len(rows) - fp - fn, "human": n_h, "ai": n_a,
        "human_flagged": int(fp), "ai_missed": int(fn),
        "rows": [{"author": l, "file": n, "score": r["score"], "verdict": r["verdict"]} for l, n, r in rows],
    }, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
