#!/usr/bin/env python3
"""Export the trained model for the web app (web/ghost-check.js).

Writes
  web/model/model.json   vocabularies (term lists in column order), intercept,
                         thresholds, and which word features are readable
                         (used for the one-line reason)
  web/model/weights.bin  float32 little-endian: word idf, word coef, char idf, char coef
  web/model/accuracy.json  measured error rates for the page's accuracy panel
                         (from train/results.json and train/sanity/results.json)

The browser re-implements the same normalisation, sectioning, tokenisation,
TF-IDF and logistic regression; tests/check_parity.py checks both give the
same scores. Re-run after every train.py (and after sanity/run_sanity.py).
Usage:  python train/export_web.py
"""

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import ghost_check as gc  # noqa: E402


def terms(vectorizer) -> list[str]:
    out = [None] * len(vectorizer.vocabulary_)
    for t, i in vectorizer.vocabulary_.items():
        out[i] = t
    return out


def main() -> int:
    m = gc.load_model()
    parts = dict(m["vectorizer"].transformer_list)
    word, char = parts["word"], parts["char"]
    assert (word.ngram_range, word.sublinear_tf, word.norm, word.lowercase) == ((1, 2), True, "l2", True)
    assert (char.analyzer, char.ngram_range, char.sublinear_tf, char.norm, char.lowercase) == \
        ("char_wb", (3, 5), True, "l2", False)
    nw = len(word.vocabulary_)
    coef = m["classifier"].coef_[0]
    readable = [int(i) for i, name in enumerate(m["feature_names"][:nw]) if gc._readable(name)]

    out = ROOT / "web" / "model"
    out.mkdir(parents=True, exist_ok=True)
    meta = {
        "version": gc.__version__,
        "intercept": float(m["classifier"].intercept_[0]),
        "threshold_likely": m["threshold_likely"],
        "threshold_possible": m["threshold_possible"],
        "chunk_words": gc.CHUNK_WORDS, "min_chunk_words": gc.MIN_CHUNK_WORDS, "min_doc_words": gc.MIN_DOC_WORDS,
        "word_terms": terms(word), "char_terms": terms(char), "readable": readable,
    }
    (out / "model.json").write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    weights = np.concatenate([word.idf_, coef[:nw], char.idf_, coef[nw:]]).astype("<f4")
    (out / "weights.bin").write_bytes(weights.tobytes())
    # Accuracy panel data, straight from the evaluation outputs so the page can't drift from the model.
    results = json.loads((ROOT / "train" / "results.json").read_text())
    sanity_path = ROOT / "train" / "sanity" / "results.json"
    accuracy = {"test": results["test_documents"], "data": results["data"],
                "test_by_source": results["test_by_source"], "ood_by_source": results["ood_by_source"],
                "sanity": json.loads(sanity_path.read_text()) if sanity_path.exists() else None}
    (out / "accuracy.json").write_text(json.dumps(accuracy, indent=1), encoding="utf-8")
    for f in ("model.json", "weights.bin", "accuracy.json"):
        print(f"web/model/{f}: {(out / f).stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
