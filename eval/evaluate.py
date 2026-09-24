#!/usr/bin/env python3
"""Measure ghost-check's error rates.

Human text comes from corpora written before AI chatbots existed, so every
flag on it is a false positive:

  * NPS Chat (2006 chat rooms)           -> chat messages and chat users
  * Brown corpus (1961 published prose)  -> document passages
  * Project Gutenberg (classic books)    -> document passages
  * NLTK webtext (2000s forums, reviews) -> short informal texts

AI text comes from eval/ai_samples.txt (one model's writing, so detection
rates are indicative, not a benchmark).

Corpora are downloaded from the NLTK data repository into eval/.cache/.
Usage:  python eval/evaluate.py [--json results.json]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
import heuristic as gc  # noqa: E402

CACHE = Path(__file__).resolve().parent / ".cache"
NLTK = "https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages/corpora/{}.zip"
random.seed(7)


def corpus(name: str) -> zipfile.ZipFile:
    CACHE.mkdir(exist_ok=True)
    path = CACHE / f"{name}.zip"
    if not path.exists():
        print(f"downloading {name}…", file=sys.stderr)
        path.write_bytes(urllib.request.urlopen(NLTK.format(name), timeout=120).read())
    return zipfile.ZipFile(path)


# ---------------------------------------------------------------- human sets

def nps_chat() -> list[dict]:
    rows = []
    z = corpus("nps_chat")
    for name in z.namelist():
        if not name.endswith(".xml"):
            continue
        root = ET.fromstring(z.read(name))
        for post in root.iter("Post"):
            text = (post.text or "").strip()
            if post.get("class") in ("System",) or not text:
                continue
            rows.append({"set": "human-chat", "sender": f"{name}:{post.get('user')}", "message": text})
    return rows


def _detok(tokens: list[str]) -> str:
    s = " ".join(tokens)
    s = s.replace("``", '"').replace("''", '"')
    s = re.sub(r"([;:!?])\1", r"\1", s)  # corpus artifacts like ";;"
    s = re.sub(r" ([.,;:!?)\]}])", r"\1", s)
    s = re.sub(r"([(\[{]) ", r"\1", s)
    s = re.sub(r" (n't|'s|'re|'ve|'ll|'d|'m)\b", r"\1", s)
    s = re.sub(r'" (.*?) "', r'"\1"', s)
    return s


def brown() -> list[dict]:
    rows = []
    z = corpus("brown")
    for name in sorted(z.namelist()):
        base = name.rsplit("/", 1)[-1]
        if not re.fullmatch(r"c[a-r]\d\d", base):
            continue
        sents = []
        for line in z.read(name).decode("latin-1").splitlines():
            toks = [t.rsplit("/", 1)[0] for t in line.split()]
            if toks:
                sents.append(_detok(toks))
        # One passage from the start of each sample keeps genres balanced.
        text = " ".join(sents)
        for p in gc._paragraphs(text, 180)[:2]:
            rows.append({"set": "human-prose", "sender": f"brown:{base}", "message": p, "genre": base[1]})
    return rows


def gutenberg() -> list[dict]:
    rows = []
    z = corpus("gutenberg")
    for name in sorted(z.namelist()):
        if not name.endswith(".txt") or "README" in name:
            continue
        paras = [p for p in gc._paragraphs(z.read(name).decode("latin-1")) if len(p.split()) >= 40]
        for p in random.sample(paras, min(25, len(paras))):
            rows.append({"set": "human-prose", "sender": f"gutenberg:{name.rsplit('/', 1)[-1]}", "message": p})
    return rows


def webtext() -> list[dict]:
    rows = []
    z = corpus("webtext")
    for name in z.namelist():
        if not name.endswith(".txt") or "README" in name:
            continue
        lines = [l.strip() for l in z.read(name).decode("latin-1").splitlines() if l.strip()]
        for l in random.sample(lines, min(800, len(lines))):
            rows.append({"set": "human-web", "sender": f"web:{name.rsplit('/', 1)[-1]}", "message": l})
    return rows


# ---------------------------------------------------------------- AI set

def ai_samples() -> list[dict]:
    text = (Path(__file__).resolve().parent / "ai_samples.txt").read_text(encoding="utf-8")
    rows, genre, buf = [], None, []
    for line in text.splitlines() + ["=== end"]:
        if line.startswith("#"):
            continue
        if line.startswith("=== "):
            if genre and "".join(buf).strip():
                rows.append({"genre": genre, "message": "\n".join(buf).strip()})
            genre, buf = line[4:].strip(), []
        else:
            buf.append(line)
    out = []
    for i, r in enumerate(rows):
        kind = "ai-chat" if r["genre"] in ("assistant", "casual", "casual-long") else "ai-prose"
        # Chat samples are grouped into "senders" of five messages for user-level stats.
        out.append({"set": kind, "sender": f"ai:{r['genre']}:{i // 5}", "message": r["message"], "genre": r["genre"]})
    return out


# ---------------------------------------------------------------- scoring

def pct(mask) -> str:
    mask = pd.Series(mask)
    return f"{100 * mask.mean():5.1f}%  ({int(mask.sum())}/{len(mask)})" if len(mask) else "   –"


def regroup(df: pd.DataFrame, size: int) -> pd.DataFrame:
    """Split big pseudo-senders (a whole corpus file) into senders of `size` texts."""
    df = df.copy()
    df["sender"] = df["sender"] + ":" + (df.groupby("sender").cumcount() // size).astype(str)
    return df


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, help="write the numbers to this file")
    args = ap.parse_args()

    df = gc.score_frame(pd.DataFrame(nps_chat() + brown() + gutenberg() + webtext() + ai_samples()))
    df["human"] = df["set"].str.startswith("human")
    judged = df[df["verdict"].isin(["ai", "possible", "none"])]
    results: dict = {"texts": {}, "senders": {}}

    print(f"\nTexts with a verdict ({gc.MIN_VERDICT_WORDS}+ words)\n")
    print(f"{'set':<13}{'n':>6}   {'Likely AI':<20}{'Likely AI or Some signs':<24}")
    for name, g in judged.groupby("set"):
        likely, any_ = g["verdict"] == "ai", g["verdict"].isin(["ai", "possible"])
        print(f"{name:<13}{len(g):>6}   {pct(likely):<20}{pct(any_):<24}")
        results["texts"][name] = {"n": len(g), "likely_ai": likely.mean(), "any_signs": any_.mean()}

    print("\nAI samples by genre:")
    for genre, g in judged[~judged["human"]].groupby("genre"):
        print(f"  {genre:<12} Likely AI {pct(g['verdict'] == 'ai'):<18} any signs {pct(g['verdict'].isin(['ai', 'possible']))}")
        results["texts"][f"ai:{genre}"] = {"n": len(g), "likely_ai": (g["verdict"] == "ai").mean(),
                                           "any_signs": g["verdict"].isin(["ai", "possible"]).mean()}

    short = df[df["scored"] & (df["words"] < gc.MIN_VERDICT_WORDS)]
    print(f"\nShorter than {gc.MIN_VERDICT_WORDS} words: {len(short)} texts get 'Too short to judge' (no verdict).")

    # Sender level: real chat users, plus human texts and AI samples grouped into senders of 10 / 5.
    senders = pd.concat([
        df[df["set"] == "human-chat"],
        regroup(df[df["set"].isin(["human-web", "human-prose"])], 10),
        df[df["set"].str.startswith("ai")],
    ])
    summ = gc.summarize(senders)
    source = summ.index.str.split(":").str[0]
    summ["group"] = source.map({"ai": "AI senders", "web": "human web (groups of 10)",
                                "brown": "human prose (groups of 10)", "gutenberg": "human prose (groups of 10)"}
                               ).fillna("human chat users")
    print("\nSenders (verdict from their messages):")
    for name, g in summ.groupby("group"):
        c = g["verdict"].value_counts()
        print(f"  {name:<27} n={len(g):<5} Likely AI {pct(g['verdict'] == 'ai'):<18}"
              f"Some signs {100 * c.get('possible', 0) / len(g):4.1f}%   too few long msgs {100 * c.get('inconclusive', 0) / len(g):5.1f}%")
        results["senders"][name] = {"n": len(g), **{k: c.get(k, 0) / len(g) for k in ["ai", "possible", "none", "inconclusive"]}}

    if args.json:
        args.json.write_text(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
