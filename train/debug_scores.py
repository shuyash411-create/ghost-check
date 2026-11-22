#!/usr/bin/env python3
"""Debug script: raw classifier output before any formatting, the training label
convention, and raw section-level probabilities on known Claude text vs known
human text.

Usage:  python train/debug_scores.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ghost_check as gc  # noqa: E402


def raw_predict(text: str, model: dict):
    """Section-level raw probabilities, no rounding/formatting, straight from
    classifier.predict_proba()."""
    text_n = gc.normalise(text)
    chunks = gc.chunk_text(text_n)
    X = model["vectorizer"].transform(chunks)
    probs = model["classifier"].predict_proba(X)  # shape (n_chunks, 2): [P(class=0), P(class=1)]
    return chunks, probs


def main() -> int:
    model = gc.load_model()

    print("=" * 70)
    print("1. Training label convention")
    print("=" * 70)
    print(f"classifier.classes_ = {model['classifier'].classes_}")
    print("  -> predict_proba(X)[:, 0] = P(label==0) = P(HUMAN)")
    print("  -> predict_proba(X)[:, 1] = P(label==1) = P(AI-GENERATED)")
    print("This is set in train/prepare_data.py: label=0 for every human source,")
    print("label=1 for every AI source (Ghostbuster GPT/Claude, ArguGPT, etc).")
    print("ghost_check.score_text() reports the AI-likelihood score as column 1,")
    print("i.e. probability the text is AI-written, on a 0-100 scale.")

    print()
    print("=" * 70)
    print("2. Raw model output for one full test document (Claude-written)")
    print("=" * 70)
    ai_doc = Path("train/sanity/ai/03_lit_frankenstein.txt")
    text = ai_doc.read_text(encoding="utf-8")
    chunks, probs = raw_predict(text, model)
    print(f"file: {ai_doc}")
    print(f"words: {len(gc.normalise(text).split())}, sections: {len(chunks)}")
    for i, (c, p) in enumerate(zip(chunks, probs)):
        print(f"  section {i+1}: raw predict_proba = [P(human)={p[0]:.6f}, P(AI)={p[1]:.6f}]  "
              f"({len(c.split())} words)")
    sizes = [len(c.split()) for c in chunks]
    doc_score = sum(p[1] * n for p, n in zip(probs, sizes)) / sum(sizes)
    print(f"document score (word-weighted mean of P(AI) across sections) = {doc_score:.6f}  "
          f"= {100*doc_score:.1f}/100")
    print(f"(cross-check against ghost_check.score_text(): "
          f"{gc.score_text(text, model)['score']:.6f})")

    print()
    print("=" * 70)
    print("3. Raw scores: 3 unedited Claude-generated texts vs 3 real human texts")
    print("=" * 70)
    print("(No API key was available in this session, so these are the existing")
    print(" Claude-written sanity-check texts already in the repo, and 3 real")
    print(" human documents also from the sanity-check set - not synthesized now.)")
    print()

    ai_files = [
        "train/sanity/ai/03_lit_frankenstein.txt",
        "train/sanity/ai/05_hist_printing_press.txt",
        "train/sanity/ai/10_psych_sleep_informal.txt",
    ]
    human_files = [
        "train/sanity/human/sotu_1994-Clinton.txt",
        "train/sanity/human/sotu_1965-Johnson-1.txt",
    ]
    # Third human file lives in a ghostbuster checkout that may not be present here;
    # fall back gracefully if so.
    manifest = Path("train/sanity/human_from_ghostbuster.txt")
    extra_human = None
    if manifest.exists():
        for line in manifest.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                extra_human = line
                break

    print(f"{'label':<8}{'file':<48}{'raw P(AI)':>12}{'0-100':>10}")
    for f in ai_files:
        t = Path(f).read_text(encoding="utf-8")
        r = gc.score_text(t, model)
        print(f"{'AI':<8}{f:<48}{r['score']:>12.6f}{100*r['score']:>10.1f}")
    for f in human_files:
        t = Path(f).read_text(encoding="utf-8")
        r = gc.score_text(t, model)
        print(f"{'human':<8}{f:<48}{r['score']:>12.6f}{100*r['score']:>10.1f}")
    if extra_human:
        gb_root = Path("../ghostbuster-data")
        p = gb_root / extra_human
        if p.exists():
            t = p.read_text(encoding="utf-8", errors="ignore")
            r = gc.score_text(t, model)
            print(f"{'human':<8}{extra_human:<48}{r['score']:>12.6f}{100*r['score']:>10.1f}")
        else:
            print(f"(third human file {extra_human} not found locally - "
                  f"ghostbuster-data checkout not present in this session)")

    print()
    print("Note: 'raw P(AI)' above IS the raw classifier.predict_proba()[:,1] output")
    print("(word-weighted mean across sections for multi-section docs) - nothing is")
    print("clipped, thresholded, or rescaled before this number. The 0-100 verdict")
    print("shown in the UI is just this number * 100, rounded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
