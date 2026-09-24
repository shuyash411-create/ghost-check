#!/usr/bin/env python3
"""Fit ghost-check's model weights and verdict thresholds.

Weights: logistic regression with non-negative weights (every signal may only
push towards "AI", so the model can't learn quirks of the AI sample set, such
as its casual samples using lowercase), class-balanced, lightly regularised.

Thresholds: set from HUMAN text only, among texts long enough to get a verdict:
  LIKELY_AI   = 99th percentile of human scores  -> ~1% false "Likely AI"
  POSSIBLE_AI = 95th percentile                  -> ~5% get at least "Some AI signs"

Prints the values to paste into ghost_check.py and web/ghost-check.js.
Usage:  python eval/fit.py
"""

import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))
import evaluate as ev  # noqa: E402
import heuristic as gc  # noqa: E402


def fit(X, y, w, l2=0.002):
    def loss(t):
        z = t[0] + X @ t[1:]
        p = 1 / (1 + np.exp(-z))
        ll = w * (y * np.log(p + 1e-12) + (1 - y) * np.log(1 - p + 1e-12))
        return -ll.sum() / w.sum() + l2 * (t[1:] @ t[1:])
    bounds = [(None, None)] + [(0, None)] * X.shape[1]
    return minimize(loss, np.zeros(X.shape[1] + 1), bounds=bounds, method="L-BFGS-B").x


def main() -> int:
    rows = ev.nps_chat() + ev.brown() + ev.gutenberg() + ev.webtext() + ev.ai_samples()
    feats = [gc.score_features(r["message"]) for r in rows]
    keep = [i for i, f in enumerate(feats) if f["words"] >= 15]
    X = np.array([gc._model_inputs(feats[i]) for i in keep])
    y = np.array([rows[i]["set"].startswith("ai") for i in keep], dtype=float)
    words = np.array([feats[i]["words"] for i in keep])
    w = np.where(y == 1, 0.5 / y.mean(), 0.5 / (1 - y.mean()))

    oof = np.zeros(len(y))
    for tr, te in StratifiedKFold(5, shuffle=True, random_state=0).split(X, y):
        t = fit(X[tr], y[tr], w[tr])
        oof[te] = t[0] + X[te] @ t[1:]
    print(f"cross-validated AUC: {roc_auc_score(y, oof):.3f}  (texts with 15+ words, n={len(y)})")

    t = fit(X, y, w)
    score = 100 / (1 + np.exp(-(t[0] + X @ t[1:])))
    human = score[(y == 0) & (words >= gc.MIN_VERDICT_WORDS)]
    likely, possible = np.quantile(human, 0.99), np.quantile(human, 0.95)

    print("\nMODEL = {")
    print(f'    "intercept": {t[0]:.3f},')
    for k, v in zip(gc.FEATURES, t[1:]):
        print(f'    "{k}": {v:.3f},')
    print("}")
    print(f"LIKELY_AI = {likely:.1f}")
    print(f"POSSIBLE_AI = {possible:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
