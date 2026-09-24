#!/usr/bin/env python3
"""ghost-check: estimate how likely a formal document (essay, report, assignment)
was written by AI.

Give it a PDF or a text file. It extracts the text, splits it into ~200-word
sections, scores each section with a trained classifier (TF-IDF + logistic
regression, see train/) and prints an AI-likelihood score with a one-line reason.

    python ghost_check.py assignment.pdf
    python ghost_check.py essay.txt report.pdf --details

The score is a statistical estimate, not proof. See train/RESULTS.md for the
measured error rates, including where it fails.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

__version__ = "3.0.0"

MODEL_PATH = Path(__file__).resolve().parent / "model" / "ghost_check.joblib"
CHUNK_WORDS = 200        # target section length
MIN_CHUNK_WORDS = 60     # shorter tails are merged into the previous section
MIN_DOC_WORDS = 80       # below this there is too little evidence for a verdict

# --------------------------------------------------------------------------
# Text handling (shared with train/prepare_data.py, so training and inference
# see text the same way)
# --------------------------------------------------------------------------

_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"',
                         "–": "-", "—": "-", "―": "-", "…": "...", " ": " ", " ": " "})


def normalise(text: str) -> str:
    """Unicode-normalise and flatten typography, so the model learns wording, not
    which quote marks or dashes a website or word processor happened to use."""
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES)
    text = re.sub(r"-\n(?=[a-z])", "", text)  # PDF hyphenation
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()


# Sentence ends only: line breaks are not boundaries, because in PDFs they are just
# where the layout wrapped a line, and they would move section boundaries around.
_SENT = re.compile(r"(?<=[.!?])\s+")


def chunk_text(text: str, target: int = CHUNK_WORDS, minimum: int = MIN_CHUNK_WORDS) -> list[str]:
    """Split into ~target-word sections on sentence boundaries."""
    chunks: list[list[str]] = []
    buf: list[str] = []
    n = 0
    for sent in _SENT.split(text):
        sent = sent.strip()
        if not sent:
            continue
        buf.append(sent)
        n += len(sent.split())
        if n >= target:
            chunks.append(buf)
            buf, n = [], 0
    if buf:
        if chunks and n < minimum:
            chunks[-1].extend(buf)
        else:
            chunks.append(buf)
    return [" ".join(c) for c in chunks]


def extract_text(path: Path) -> str:
    """Text from a .pdf (via pypdf) or any text file (.txt, .md, ...)."""
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            sys.exit("PDF input needs pypdf:  pip install pypdf")
        reader = PdfReader(str(path))
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        if not text.strip():
            raise ValueError("no extractable text (probably a scanned PDF - run OCR first, e.g. ocrmypdf)")
        return text
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def load_model(path: Path = MODEL_PATH) -> dict:
    if not path.exists():
        sys.exit(f"Model not found at {path}. Train it with: python train/train.py")
    import joblib

    return joblib.load(path)


def verdict(score: float, model: dict) -> str:
    if score >= model["threshold_likely"]:
        return "Likely AI-written"
    if score >= model["threshold_possible"]:
        return "Possibly AI-written"
    return "No clear AI signs"


_STOP = set("""a an the and or but of to in on at by for with from as is are was were be been being it its
this that these those i me my we our you your he she they them his her their there here not no so if then than
which who whom what when where while do does did has have had will would can could may might shall should""".split())


def _readable(term: str) -> bool:
    """A word or phrase a person can recognise: letters only, not just filler words."""
    return bool(re.fullmatch(r"[a-z][a-z']*( [a-z][a-z']*)?", term)) and len(term) > 3 \
        and not all(w in _STOP for w in term.split())


def _top_terms(model: dict, X, k: int = 3) -> tuple[list[str], list[str]]:
    """Words/phrases contributing most to this document's score, in each direction."""
    import numpy as np

    contrib = np.asarray(X.mean(axis=0)).ravel() * model["coef"]
    names = model["feature_names"]
    if "readable_mask" not in model:  # character n-grams and punctuation mean nothing to people
        model["readable_mask"] = model["word_feature_mask"] & np.array([_readable(n) for n in names])
    readable = model["readable_mask"]
    order = np.argsort(contrib)
    ai = [names[i] for i in order[::-1] if readable[i] and contrib[i] > 0][:k]
    human = [names[i] for i in order if readable[i] and contrib[i] < 0][:k]
    return ai, human


def score_text(text: str, model: dict) -> dict:
    text = normalise(text)
    words = len(text.split())
    if words < MIN_DOC_WORDS:
        return {"words": words, "score": None, "verdict": "Too short to judge",
                "reason": f"Only {words} words; at least {MIN_DOC_WORDS} are needed for a meaningful score."}
    chunks = chunk_text(text)
    X = model["vectorizer"].transform(chunks)
    probs = model["classifier"].predict_proba(X)[:, 1]
    sizes = [len(c.split()) for c in chunks]
    score = float(sum(p * n for p, n in zip(probs, sizes)) / sum(sizes))  # longer sections count more
    v = verdict(score, model)
    ai_like = int((probs >= model["threshold_possible"]).sum())
    ai_terms, human_terms = _top_terms(model, X)
    reason = f"{ai_like} of {len(chunks)} section{'s' if len(chunks) != 1 else ''} read as AI-like"
    if ai_terms and score >= model["threshold_possible"]:
        reason += "; AI-leaning wording: " + ", ".join(f"'{t}'" for t in ai_terms)
    elif human_terms:
        reason += "; human-leaning wording: " + ", ".join(f"'{t}'" for t in human_terms)
    if words < 250:
        reason += f" (short text, {words} words: lower confidence)"
    return {"words": words, "sections": len(chunks), "score": score, "verdict": v, "reason": reason,
            "section_scores": [round(float(p), 3) for p in probs]}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="ghost-check",
        description="Estimate how likely a PDF or text document was written by AI.",
    )
    ap.add_argument("inputs", nargs="+", type=Path, help=".pdf or text files (.txt, .md, ...)")
    ap.add_argument("--details", action="store_true", help="also print the score of every ~200-word section")
    ap.add_argument("--json", action="store_true", help="print results as JSON")
    ap.add_argument("--model", type=Path, default=MODEL_PATH, help="model file (default: %(default)s)")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = ap.parse_args(argv)

    model = load_model(args.model)
    results, failed = [], False
    for path in args.inputs:
        try:
            r = {"file": str(path), **score_text(extract_text(path), model)}
        except (OSError, ValueError) as e:
            r = {"file": str(path), "error": str(e)}
            failed = True
        results.append(r)
        if args.json:
            continue
        if "error" in r:
            print(f"{path.name}: error: {r['error']}")
        elif r["score"] is None:
            print(f"{path.name}: {r['verdict']}. {r['reason']}")
        else:
            print(f"{path.name}: AI-likelihood {100 * r['score']:.0f}/100 - {r['verdict']} "
                  f"(Likely AI at {100 * model['threshold_likely']:.0f}+, Possibly at "
                  f"{100 * model['threshold_possible']:.0f}+). {r['reason']}.")
            if args.details:
                for i, p in enumerate(r["section_scores"], 1):
                    print(f"    section {i}: {100 * p:.0f}")
    if args.json:
        print(json.dumps(results, indent=2))
    elif any(r.get("score") is not None for r in results):
        print("\nStatistical estimate, not proof. Measured error rates: train/RESULTS.md")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
