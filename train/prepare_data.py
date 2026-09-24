#!/usr/bin/env python3
"""Build ghost-check's formal-text dataset.

In-distribution data (train / validation / test), from the Ghostbuster corpus
(Verma et al. 2023, https://github.com/vivek3141/ghostbuster-data):
  * essay/   student essays (human) and essays written by GPT-3.5 and Claude
             for the same prompts
  * reuter/  Reuters news articles (human) and GPT / Claude rewrites of the
             same articles
The split is by prompt/article group, so human and AI texts on the same prompt
always land in the same split and the model cannot learn topics.

Also split by document into train / validation / test (more varied writers):
  human  BAWE (UK university assignments), ETS (TOEFL essays), PELIC (learners)
  AI     ArguGPT essays by text-davinci-002/003 and gpt-3.5-turbo (TOEFL/WECCL/GRE
         prompts, the same exam prompts the ETS human essays answer)
  AI     Claude API samples in train/data/claude_generated.jsonl, if present
         (see generate_claude.py), split by topic

Out-of-distribution test sets (sources never used for training or model selection):
  human  Lang-8 and TOEFL-91 (non-native writers), Brown corpus 1961 prose
  AI     ArguGPT OOD essays (GPT-4, Claude-instant, BLOOMZ, Flan-T5), Ghostbuster
         "undetectable" (AI text passed through a humaniser), eval/ai_samples.txt

Every text is cut into ~200-word chunks on sentence boundaries: the AI essays
in Ghostbuster are ~30% longer than the human ones, so whole-document training
would let a model cheat on length.

Usage:
  python train/prepare_data.py --ghostbuster PATH --argugpt PATH [--out train/data]
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ghost_check as gc  # noqa: E402  (shared normalise/chunk code)

csv.field_size_limit(10**9)
random.seed(1234)
ROOT = Path(__file__).resolve().parent.parent


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="ignore")


def chunk_rows(text: str, base: dict, max_chunks: int | None) -> list[dict]:
    chunks = gc.chunk_text(gc.normalise(text))
    if max_chunks and len(chunks) > max_chunks:
        chunks = random.sample(chunks, max_chunks)
    return [{**base, "chunk": i, "text": c} for i, c in enumerate(chunks)]


# ---------------------------------------------------------------- in-distribution

def ghostbuster_main(gb: Path, max_chunks: int) -> list[dict]:
    rows = []
    for domain in ["essay", "reuter"]:
        for source, label in [("human", 0), ("gpt", 1), ("claude", 1)]:
            for p in sorted((gb / domain / source).rglob("*.txt")):
                rel = p.relative_to(gb / domain / source)
                group = f"{domain}:{rel.with_suffix('')}"  # same prompt/article across sources
                base = {"doc": f"{domain}/{source}/{rel}", "group": group, "domain": domain,
                        "source": f"{domain}-{source}", "label": label}
                rows += chunk_rows(read(p), base, max_chunks)
    return rows


def extra_training_sources(gb: Path, argu: Path, max_chunks: int) -> list[dict]:
    """More varied writers, split by document like the main data (v2 of the dataset).

    v1 trained only on Ghostbuster's polished web essays and news, and wrongly
    flagged 15-36% of real university assignments and non-native writing. These
    sources teach the model that such writing is human too. Held-out documents
    from them are reported separately from the never-seen OOD sets.
    """
    rows = []
    # Limits exceed the file counts (1,444 / 1,000 / 1,000), so every file is used; the
    # sample() call only shuffles. Keep it: changing RNG use would change the split.
    for folder, name, limit in [("bawe", "human-bawe (UK university assignments)", 2600),
                                ("ets", "human-ets (non-native, TOEFL)", 2000),
                                ("pelic", "human-pelic (non-native learners)", 2000)]:
        files = sorted((gb / "other" / folder).glob("*.txt"))
        for p in random.sample(files, min(limit, len(files))):
            base = {"doc": f"other/{folder}/{p.name}", "group": f"{folder}:{p.stem}", "domain": folder,
                    "source": name, "label": 0}
            rows += chunk_rows(read(p), base, max_chunks)
    keep = ("text-davinci-002", "text-davinci-003", "gpt-3.5-turbo")
    for fname in ["machine-train.csv", "machine-dev.csv"]:
        with open(argu / fname, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["model"] in keep:
                    base = {"doc": f"argugpt/{r['id']}", "group": f"argugpt:{r['id']}", "domain": "argugpt",
                            "source": f"ai-argugpt ({r['model']})", "label": 1}
                    rows += chunk_rows(r["text"], base, max_chunks)
    return rows


def split_groups(rows: list[dict]) -> None:
    groups = sorted({r["group"] for r in rows})
    random.shuffle(groups)
    n = len(groups)
    split = {g: ("train" if i < 0.7 * n else "val" if i < 0.85 * n else "test") for i, g in enumerate(groups)}
    for r in rows:
        r["split"] = split[r["group"]]


# ---------------------------------------------------------------- out-of-distribution

def ood_ghostbuster(gb: Path) -> list[dict]:
    rows = []
    sets = [("toefl91", "human-toefl91 (non-native)", 0, None),
            ("lang8", "human-lang8 (non-native learners)", 0, 300),
            ("undetectable", "ai-undetectable (humanised AI)", 1, None)]
    for folder, name, label, limit in sets:
        files = sorted((gb / "other" / folder).glob("*.txt"))
        if limit:
            files = random.sample(files, min(limit, len(files)))
        for p in files:
            base = {"doc": f"other/{folder}/{p.name}", "group": f"ood:{folder}:{p.stem}", "source": name,
                    "label": label, "split": "ood"}
            rows += chunk_rows(read(p), base, 3)
    return rows


def ood_argugpt(argu: Path) -> list[dict]:
    rows = []
    # machine-ood.csv: generators never used for training (GPT-4, Claude-instant, BLOOMZ, Flan-T5, ...)
    for fname, limit in [("machine-ood.csv", 600)]:
        with open(argu / fname, newline="", encoding="utf-8") as f:
            data = [r for r in csv.DictReader(f) if r["model"] not in ("gpt2-xl", "text-babbage-001", "text-curie-001",
                                                                        "text-davinci-002", "text-davinci-003", "gpt-3.5-turbo")]
        for r in random.sample(data, min(limit, len(data))):
            base = {"doc": f"argugpt/{fname}/{r['id']}", "group": f"ood:argugpt:{r['id']}",
                    "source": f"ai-argugpt ({r['model']})", "label": 1, "split": "ood"}
            rows += chunk_rows(r["text"], base, 2)
    return rows


def ood_brown(cache: Path) -> list[dict]:
    """1961 published prose: press, religion, hobbies, lore, belles-lettres, government, learned."""
    z = zipfile.ZipFile(cache / "brown.zip")
    rows = []
    for name in sorted(z.namelist()):
        base_name = name.rsplit("/", 1)[-1]
        if not re.fullmatch(r"c[a-j]\d\d", base_name):
            continue
        toks = []
        for line in z.read(name).decode("latin-1").splitlines():
            toks += [t.rsplit("/", 1)[0] for t in line.split()]
        s = " ".join(toks).replace("``", '"').replace("''", '"')
        s = re.sub(r"([;:!?])\1", r"\1", s)
        s = re.sub(r" ([.,;:!?)\]}])", r"\1", s)
        s = re.sub(r"([(\[{]) ", r"\1", s)
        s = re.sub(r" (n't|'s|'re|'ve|'ll|'d|'m)\b", r"\1", s)
        base = {"doc": f"brown/{base_name}", "group": f"ood:brown:{base_name}",
                "source": "human-brown-1961 (published prose)", "label": 0, "split": "ood"}
        rows += chunk_rows(s, base, 2)
    return rows


def ood_ai_samples() -> list[dict]:
    text = read(ROOT / "eval" / "ai_samples.txt")
    rows, genre, buf = [], None, []
    for line in text.splitlines() + ["=== end"]:
        if line.startswith("#"):
            continue
        if line.startswith("=== "):
            if genre in ("essay", "email") and "".join(buf).strip():
                i = len(rows)
                rows.append({"doc": f"ai_samples/{genre}/{i}", "group": f"ood:ai_samples:{i}",
                             "source": "ai-claude-samples (essays/emails, short)", "label": 1, "split": "ood",
                             "chunk": 0, "text": gc.normalise("\n".join(buf))})
            genre, buf = line[4:].strip(), []
        else:
            buf.append(line)
    return rows


def claude_generated(path: Path) -> list[dict]:
    """Samples from generate_claude.py. Split by topic like the main data."""
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        base = {"doc": f"claude_gen/{r['id']}", "group": f"claude_gen:{r['topic_id']}", "domain": "claude_gen",
                "source": f"claude-gen-{r['genre']}", "label": 1}
        rows += chunk_rows(r["text"], base, 3)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ghostbuster", type=Path, required=True)
    ap.add_argument("--argugpt", type=Path, required=True, help="ArguGPT/data/argugpt")
    ap.add_argument("--corpora-cache", type=Path, default=ROOT / "eval" / ".cache")
    ap.add_argument("--out", type=Path, default=ROOT / "train" / "data")
    ap.add_argument("--max-chunks", type=int, default=4, help="max chunks per in-distribution document")
    args = ap.parse_args()

    main_rows = ghostbuster_main(args.ghostbuster, args.max_chunks)
    main_rows += extra_training_sources(args.ghostbuster, args.argugpt, args.max_chunks)
    gen = claude_generated(args.out / "claude_generated.jsonl")
    main_rows += gen
    split_groups(main_rows)
    ood = ood_ghostbuster(args.ghostbuster) + ood_argugpt(args.argugpt) + ood_brown(args.corpora_cache) + ood_ai_samples()

    args.out.mkdir(parents=True, exist_ok=True)
    for name, rows in [("main.jsonl", main_rows), ("ood.jsonl", ood)]:
        with open(args.out / name, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    c = Counter((r["split"], r["label"]) for r in main_rows)
    print(f"in-distribution chunks: {len(main_rows)} "
          + " ".join(f"{s}:{'ai' if l else 'human'}={n}" for (s, l), n in sorted(c.items())))
    print(f"  Claude API samples included: {len(gen)} chunks")
    print("out-of-distribution chunks:")
    for src, n in sorted(Counter(r["source"] for r in ood).items()):
        print(f"  {src:<48} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
