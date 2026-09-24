#!/usr/bin/env python3
"""Train and evaluate ghost-check's classifier: TF-IDF (word 1-2 grams + character
3-5 grams) + logistic regression.

  * The regularisation strength C is chosen on the validation split (ROC AUC).
  * Verdict thresholds are chosen on validation *human* documents only:
      threshold_likely   -> ~1% of human documents score above it
      threshold_possible -> ~5%
  * The test split and the out-of-distribution sets are scored once, at the end.

Writes model/ghost_check.joblib, train/results.json and train/RESULTS.md.
Usage:  python train/train.py   (after prepare_data.py)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import FeatureUnion

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "train" / "data"


def load(name: str) -> pd.DataFrame:
    return pd.read_json(DATA / name, lines=True)


def vectorizer() -> FeatureUnion:
    return FeatureUnion([
        ("word", TfidfVectorizer(ngram_range=(1, 2), min_df=3, max_df=0.5, max_features=120_000,
                                 sublinear_tf=True, lowercase=True, token_pattern=r"(?u)\b\w+\b|[^\w\s]")),
        ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=5, max_features=150_000,
                                 sublinear_tf=True, lowercase=False)),
    ])


def doc_scores(df: pd.DataFrame, probs: np.ndarray) -> pd.DataFrame:
    """Document score = word-weighted mean of its section scores, as in ghost_check.score_text()."""
    n = df.text.str.split().str.len()
    d = df.assign(pw=probs * n, n=n).groupby("doc").agg(pw=("pw", "sum"), n=("n", "sum"), label=("label", "first"),
                                                         source=("source", "first"))
    d["p"] = d.pw / d.n
    return d.reset_index()


def rates(labels: np.ndarray, scores: np.ndarray, thr: float) -> dict:
    pred = scores >= thr
    human, ai = labels == 0, labels == 1
    out = {"n": int(len(labels)), "n_human": int(human.sum()), "n_ai": int(ai.sum()),
           "accuracy": float((pred == labels.astype(bool)).mean())}
    out["false_positive_rate"] = float(pred[human].mean()) if human.any() else None
    out["false_negative_rate"] = float((~pred[ai]).mean()) if ai.any() else None
    if human.any() and ai.any():
        out["auc"] = float(roc_auc_score(labels, scores))
    return out


def main() -> int:
    t0 = time.time()
    main_df, ood = load("main.jsonl"), load("ood.jsonl")
    train, val, test = (main_df[main_df.split == s] for s in ("train", "val", "test"))
    print(f"train {len(train)}  val {len(val)}  test {len(test)}  ood {len(ood)} chunks")

    vec = vectorizer()
    Xtr = vec.fit_transform(train.text)
    Xva = vec.transform(val.text)
    print(f"features: {Xtr.shape[1]}  ({time.time() - t0:.0f}s)")

    best = None
    for C in [0.5, 2.0, 8.0, 32.0]:
        clf = LogisticRegression(C=C, max_iter=3000, class_weight="balanced", solver="liblinear")
        clf.fit(Xtr, train.label)
        auc = roc_auc_score(val.label, clf.predict_proba(Xva)[:, 1])
        print(f"  C={C:<5} val chunk AUC {auc:.4f}")
        if best is None or auc > best[0]:
            best = (auc, C, clf)
    _, C, clf = best
    print(f"chosen C={C}")

    # Thresholds from validation human documents only.
    vdoc = doc_scores(val, clf.predict_proba(Xva)[:, 1])
    vh = vdoc[vdoc.label == 0].p.values
    thr_likely, thr_possible = float(np.quantile(vh, 0.99)), float(np.quantile(vh, 0.95))
    print(f"thresholds: likely {thr_likely:.3f}  possible {thr_possible:.3f}")

    # --- final evaluation (test + OOD scored once) ---
    Xte = vec.transform(test.text)
    pte = clf.predict_proba(Xte)[:, 1]
    tdoc = doc_scores(test, pte)
    pood = clf.predict_proba(vec.transform(ood.text))[:, 1]
    odoc = doc_scores(ood, pood)

    results = {
        "model": {"type": "TF-IDF (word 1-2g + char 3-5g) + logistic regression", "C": C,
                  "features": int(Xtr.shape[1]), "sklearn": sklearn.__version__,
                  "threshold_likely": thr_likely, "threshold_possible": thr_possible},
        "data": {"train_chunks": len(train), "val_chunks": len(val), "test_chunks": len(test),
                 "train_docs": int(train.doc.nunique()), "test_docs": int(test.doc.nunique()),
                 "test_docs_human": int((tdoc.label == 0).sum()), "test_docs_ai": int((tdoc.label == 1).sum())},
        "test_documents": {
            "at_0.5": rates(tdoc.label.values, tdoc.p.values, 0.5),
            "at_likely": rates(tdoc.label.values, tdoc.p.values, thr_likely),
            "at_possible": rates(tdoc.label.values, tdoc.p.values, thr_possible),
        },
        "test_chunks": {"at_0.5": rates(test.label.values, pte, 0.5),
                        "at_likely": rates(test.label.values, pte, thr_likely)},
        "test_by_source": {},
        "ood_by_source": {},
    }
    for src, g in tdoc.groupby("source"):
        results["test_by_source"][src] = {"docs": len(g), "flag_rate_likely": float((g.p >= thr_likely).mean()),
                                          "flag_rate_possible": float((g.p >= thr_possible).mean()),
                                          "flag_rate_0.5": float((g.p >= 0.5).mean())}
    for src, g in odoc.groupby("source"):
        results["ood_by_source"][src] = {"docs": len(g), "label": int(g.label.iloc[0]),
                                         "flag_rate_likely": float((g.p >= thr_likely).mean()),
                                         "flag_rate_possible": float((g.p >= thr_possible).mean()),
                                         "flag_rate_0.5": float((g.p >= 0.5).mean()),
                                         "median_score": float(g.p.median())}

    # --- save model ---
    names = vec.get_feature_names_out()
    word_mask = np.array([n.startswith("word__") for n in names])
    clean = np.array([n.split("__", 1)[1] for n in names], dtype=object)
    (ROOT / "model").mkdir(exist_ok=True)
    joblib.dump({"vectorizer": vec, "classifier": clf, "coef": clf.coef_[0].astype(np.float32),
                 "feature_names": clean, "word_feature_mask": word_mask,
                 "threshold_likely": thr_likely, "threshold_possible": thr_possible,
                 "sklearn_version": sklearn.__version__, "C": C},
                ROOT / "model" / "ghost_check.joblib", compress=3)
    (ROOT / "train" / "results.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    print(f"done in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
