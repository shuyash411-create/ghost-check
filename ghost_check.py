#!/usr/bin/env python3
"""ghost-check: estimate how "AI-written" each sender's messages look.

Reads a WhatsApp chat export (.txt or the .zip WhatsApp produces) or a PDF,
scores every message with simple stylometric heuristics and writes a
self-contained HTML report with per-sender tables and a bar chart.

The score is a heuristic signal, not proof. Read the README before acting on it.
"""

from __future__ import annotations

import argparse
import base64
import html
import io
import math
import re
import statistics
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

__version__ = "2.0.0"

# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

_DATE = r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})"
_TIME = r"(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\s*([AaPp])\.?\s*[Mm]\.?)?"
# Android: "31/12/23, 9:15 pm - Name: text"
ANDROID_RE = re.compile(rf"^{_DATE},?\s+{_TIME}\s+[-–]\s+(.*)$")
# iOS:     "[31/12/2023, 21:15:03] Name: text"
IOS_RE = re.compile(rf"^\[{_DATE},?\s+{_TIME}\]\s*(.*)$")

INVISIBLE = dict.fromkeys(map(ord, "‎‏‪‫‬‭‮﻿"), None)
SPACES = {0x202F: " ", 0x00A0: " ", 0x2007: " "}

SKIP_BODIES = re.compile(
    r"^(<media omitted>|<attached:.*>|.*\b(image|video|audio|sticker|gif|document|contact card) omitted"
    r"|this message was deleted|you deleted this message|null|missed (voice|video) call"
    r"|waiting for this message.*|poll:.*)$",
    re.IGNORECASE,
)
EDITED_SUFFIX = re.compile(r"\s*<this message was edited>\s*$", re.IGNORECASE)


@dataclass
class Record:
    sender: str
    timestamp: datetime | None
    message: str
    source: str
    kind: str = "chat"  # "chat" or "document"


def _clean(line: str) -> str:
    return line.translate(INVISIBLE).translate(SPACES).rstrip("\r\n")


def _match(line: str):
    return ANDROID_RE.match(line) or IOS_RE.match(line)


def _detect_dayfirst(lines: list[str]) -> bool:
    """Infer dd/mm vs mm/dd from the whole export. Defaults to day-first."""
    for line in lines:
        m = _match(line)
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12:
            return True
        if b > 12:
            return False
    return True


def _to_datetime(m: re.Match, dayfirst: bool) -> datetime | None:
    a, b, y, hh, mm, ss, ampm = m.groups()[:7]
    day, month = (int(a), int(b)) if dayfirst else (int(b), int(a))
    year = int(y) + (2000 if len(y) == 2 else 0)
    hour = int(hh)
    if ampm:
        ampm = ampm.lower()
        if ampm == "p" and hour != 12:
            hour += 12
        elif ampm == "a" and hour == 12:
            hour = 0
    try:
        return datetime(year, month, day, hour, int(mm), int(ss or 0))
    except ValueError:
        return None


def parse_whatsapp(text: str, source: str, dayfirst: bool | None = None) -> list[Record]:
    """Turn a WhatsApp export into records. Continuation lines join the previous
    message; system lines (no "Name: " prefix) and media placeholders are dropped."""
    lines = [_clean(l) for l in text.splitlines()]
    if dayfirst is None:
        dayfirst = _detect_dayfirst(lines)

    records: list[Record] = []
    current: Record | None = None
    for line in lines:
        m = _match(line)
        if m:
            if current:
                records.append(current)
            current = None
            rest = m.group(8)
            if ": " not in rest:  # system message ("X joined", "Messages are end-to-end encrypted")
                continue
            sender, body = rest.split(": ", 1)
            current = Record(sender.strip(), _to_datetime(m, dayfirst), body, source)
        elif current is not None:
            current.message += "\n" + line
    if current:
        records.append(current)

    out = []
    for r in records:
        r.message = EDITED_SUFFIX.sub("", r.message).strip()
        if r.message and not SKIP_BODIES.match(r.message):
            out.append(r)
    return out


def _paragraphs(text: str, max_words: int = 180) -> list[str]:
    """Split free text (e.g. from a PDF) into paragraph-sized chunks."""
    text = re.sub(r"-\n(?=[a-z])", "", text)  # de-hyphenate wrapped words
    chunks = []
    for para in re.split(r"\n\s*\n", text):
        para = re.sub(r"\s*\n\s*", " ", para).strip()
        if not para:
            continue
        words = para.split()
        if len(words) <= max_words:
            chunks.append(para)
            continue
        # Long block (PDFs often have no blank lines): regroup by sentence.
        buf: list[str] = []
        for sent in re.split(r"(?<=[.!?])\s+", para):
            buf.append(sent)
            if sum(len(s.split()) for s in buf) >= max_words * 0.75:
                chunks.append(" ".join(buf))
                buf = []
        if buf:
            chunks.append(" ".join(buf))
    return chunks


def parse_document(pages: list[str], source: str, by_page: bool) -> list[Record]:
    stem = Path(source).stem
    records = []
    for i, page in enumerate(pages, 1):
        sender = f"{stem} · p.{i}" if by_page else stem
        records += [Record(sender, None, p, source, "document") for p in _paragraphs(page)]
    return records


def read_pdf_pages(path: Path) -> list[str]:
    try:
        from pypdf import PdfReader
    except ImportError:
        sys.exit("PDF input needs pypdf:  pip install pypdf")
    reader = PdfReader(str(path))
    pages = [(p.extract_text() or "") for p in reader.pages]
    if not any(p.strip() for p in pages):
        sys.exit(f"{path}: no extractable text (scanned PDF?). OCR it first, e.g. with ocrmypdf.")
    return pages


def read_text_file(path: Path) -> str:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".txt")]
            if not names:
                sys.exit(f"{path}: no .txt chat file inside the zip")
            raw = z.read(names[0])
    else:
        raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def load(path: Path, mode: str, dayfirst: bool | None, by_page: bool) -> tuple[list[Record], str]:
    """Load any supported file. Returns (records, detected mode)."""
    if path.suffix.lower() == ".pdf":
        pages = read_pdf_pages(path)
    else:
        pages = [read_text_file(path)]
    return load_text(pages, path.name, mode, dayfirst, by_page)


def load_text(pages: list[str], source: str, mode: str, dayfirst: bool | None,
              by_page: bool) -> tuple[list[Record], str]:
    """Parse already-extracted text as a chat or a document. Returns (records, mode)."""
    if mode in ("auto", "chat"):
        chat = parse_whatsapp("\n".join(pages), source, dayfirst)
        if mode == "chat" or len(chat) >= 3:
            return chat, "chat"
    return parse_document(pages, source, by_page), "document"


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
#
# Model v2. Five signals whose direction is backed by research on LLM text,
# combined with a logistic model fitted (non-negative weights) by eval/fit.py.
# Verdict thresholds are set so that, on human texts (30+ words) written before AI
# chatbots existed, about 1% get "Likely AI" and about 5% get "Some AI signs".
# See "Accuracy" in README.md for the measured error rates. Keep web/ghost-check.js in sync.

# Words LLMs overuse (Kobak et al. 2024, "Delving into ChatGPT usage in academic
# writing"; Liang et al. 2024). "various" was dropped: it is more common in
# pre-2020 human prose than in AI text.
AI_VOCAB = frozenset("""
delve delves delving delved showcase showcases showcasing underscore underscores underscoring
crucial crucially pivotal intricate intricacies meticulous meticulously comprehensive notably
noteworthy commendable realm realms landscape tapestry foster fosters fostering enhance enhances
enhancing bolster streamline streamlines leverage leveraging seamless seamlessly robust nuanced
multifaceted holistic paramount invaluable unwavering embark navigate navigating elevate empower
empowers empowering unlock unlocking harness vibrant testament profound additionally furthermore
moreover ultimately overall essential vital ensure ensures ensuring potential insights valuable
effectively prioritize resonate dynamic innovative transformative strive facilitate optimal
significant significantly journey thrive
""".split())

# Multi-word stock phrases typical of assistant-style text.
STOCK_PHRASES = re.compile(
    r"it'?s (?:important|worth|essential|crucial) to|it is (?:important|worth noting|essential|crucial)"
    r"|plays? an? (?:crucial|vital|key|pivotal|significant) role|in today'?s|whether you'?re"
    r"|i hope this|hope this (?:message|email) finds you|let me know if|feel free to|happy to help"
    r"|here'?s (?:a|an|some|how|what)\b|here are (?:some|a few)|great question|i understand (?:your|that)"
    r"|on the other hand|a wide range of|when it comes to|not only\b[^.]*\bbut also|by doing so"
    r"|don'?t hesitate|rest assured|thank you for reaching out|i'?d be happy to|as an ai|as a language model"
    r"|can make a (?:big|meaningful|significant|real) difference|in the long run|key (?:factors|takeaways)"
    r"|a testament to|that being said|(?:certainly|absolutely|of course)!"
)

# Sentence-opening transitions LLMs lean on.
TRANSITIONS = re.compile(
    r"(?:additionally|furthermore|moreover|however|overall|ultimately|in conclusion|in summary"
    r"|in addition|firstly|secondly|finally|lastly|on the other hand|as a result|by doing so)\b",
    re.IGNORECASE,
)

# Fitted by eval/fit.py on model features (see _model_inputs).
MODEL = {
    "intercept": -1.249,
    "word_length": 0.590,
    "rhythm": 0.541,
    "ai_vocab": 1.741,
    "stock_phrases": 2.039,
    "transitions": 0.448,
}
LIKELY_AI = 83.8      # score for "Likely AI"     (~1% of human texts at or above)
POSSIBLE_AI = 63.7    # score for "Some AI signs" (~5% of human texts at or above)
MIN_WORDS = 6         # shorter messages are not scored at all
MIN_VERDICT_WORDS = 30  # shorter texts get "Too short to judge"

FEATURES = ["word_length", "rhythm", "ai_vocab", "stock_phrases", "transitions"]
FEATURE_LABELS = {
    "word_length": "Long words",
    "rhythm": "Even sentence rhythm",
    "ai_vocab": "AI-typical words",
    "stock_phrases": "Stock AI phrases",
    "transitions": "Formulaic transitions",
}
VERDICTS = {
    "ai": "Likely AI-written",
    "possible": "Some AI signs",
    "none": "No clear AI signs",
    "inconclusive": "Too short to judge",
}

EDGE_PUNCT = ".,;:!?—–-()\"'’“”…*_~`[]{}<>"


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _words(text: str) -> list[str]:
    """Tokens containing at least one letter, stripped of edge punctuation
    (works for any script, not just Latin)."""
    out = []
    for tok in text.split():
        tok = tok.strip(EDGE_PUNCT)
        if any(ch.isalpha() for ch in tok):
            out.append(tok)
    return out


_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")


def _sentences(text: str) -> list[str]:
    return [p.strip() for p in _SENT_SPLIT.split(text) if _words(p)]


def score_features(text: str) -> dict[str, float]:
    """Raw signals for one message. NaN = not measurable (e.g. rhythm needs 2+ sentences)."""
    nan = float("nan")
    words = _words(text)
    n = len(words)
    sents = _sentences(text)
    lengths = [len(_words(s)) for s in sents]
    f: dict[str, float] = {"words": n, "sentences": len(sents)}

    # Long words: LLM vocabulary skews longer than everyday writing.
    f["word_length"] = _clamp((statistics.fmean(len(w) for w in words) - 3.6) / 1.6) if n else nan

    # Even rhythm: LLMs write evenly sized sentences (low variance) with little
    # jump from one sentence to the next (low burstiness).
    if len(lengths) >= 2:
        mean = statistics.fmean(lengths)
        uniform = _clamp(1 - (statistics.pstdev(lengths) / mean) / 0.8)
        if len(lengths) >= 3:
            jumps = statistics.fmean(abs(a - b) for a, b in zip(lengths, lengths[1:]))
            flat = _clamp(1 - (jumps / mean) / 0.9)
            f["rhythm"] = (uniform + flat) / 2
        else:
            f["rhythm"] = uniform
    else:
        f["rhythm"] = nan

    low = text.lower().replace("’", "'")
    # AI-typical words per 100 words.
    f["ai_vocab"] = 100 * sum(w.lower().replace("’", "'") in AI_VOCAB for w in words) / n if n else nan
    f["stock_phrases"] = len(STOCK_PHRASES.findall(low))
    f["transitions"] = sum(bool(TRANSITIONS.match(s)) for s in sents) / len(sents) if sents else nan
    return f


def _model_inputs(f) -> list[float]:
    """Feature transforms the model is fitted on. NaN rhythm counts as neutral."""
    rhythm = f["rhythm"]
    return [
        f["word_length"] if not math.isnan(f["word_length"]) else 0.0,
        0.5 if math.isnan(rhythm) else rhythm,
        math.log1p(f["ai_vocab"]) if not math.isnan(f["ai_vocab"]) else 0.0,
        min(f["stock_phrases"], 3),
        f["transitions"] if not math.isnan(f["transitions"]) else 0.0,
    ]


def model_score(f) -> float:
    z = MODEL["intercept"] + sum(MODEL[k] * x for k, x in zip(FEATURES, _model_inputs(f)))
    return 100 / (1 + math.exp(-z))


def display_value(key: str, v: float) -> float:
    """Feature on a 0-100 'how AI-like' scale for tables and bars."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return float("nan")
    if key == "ai_vocab":
        return min(100.0, 100 * v / 3)  # 3+ per 100 words = maximum
    if key == "stock_phrases":
        return min(100.0, 100 * v / 3)
    return 100 * v


def verdict(score: float, words: int) -> tuple[str, str]:
    """(key, label) for one text."""
    if words < MIN_VERDICT_WORDS or math.isnan(score):
        key = "inconclusive"
    elif score >= LIKELY_AI:
        key = "ai"
    elif score >= POSSIBLE_AI:
        key = "possible"
    else:
        key = "none"
    return key, VERDICTS[key]


def person_verdict(judged: int, likely: int, possible: int) -> tuple[str, str]:
    """(key, label) for a sender, from their messages long enough to judge.

    Needs two "Likely AI" messages (and at least 20% of what could be judged)
    before calling a person likely AI: with many messages, one false alarm at
    a 1% rate is expected sooner or later.
    """
    if likely >= 2 and likely / judged >= 0.2:
        key = "ai"
    elif likely >= 1 or (judged and possible / judged >= 0.3):
        key = "possible"
    elif judged < 2:
        key = "inconclusive"
    else:
        key = "none"
    label = "Too few long messages to judge" if key == "inconclusive" else VERDICTS[key]
    return key, label


def score_frame(df: pd.DataFrame, min_words: int = MIN_WORDS) -> pd.DataFrame:
    feats = pd.DataFrame([score_features(t) for t in df["message"]], index=df.index)
    df = pd.concat([df, feats], axis=1)
    df["scored"] = df["words"] >= min_words
    df["score"] = [model_score(r) if ok else float("nan") for r, ok in zip(feats.to_dict("records"), df["scored"])]
    v = [verdict(s, w) if ok else ("skipped", "Not scored") for s, w, ok in zip(df["score"], df["words"], df["scored"])]
    df["verdict"] = [k for k, _ in v]
    df["verdict_label"] = [label for _, label in v]
    return df


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for sender, g in df.groupby("sender", sort=False):
        scored = g[g["scored"]]
        judged = g[g["verdict"].isin(["ai", "possible", "none"])]
        likely = int((judged["verdict"] == "ai").sum())
        possible = int((judged["verdict"] == "possible").sum())
        avg = (scored["score"] * scored["words"]).sum() / scored["words"].sum() if len(scored) else float("nan")
        if "kind" in g and (g["kind"] == "document").all():
            key, label = verdict(avg, int(scored["words"].sum()))  # a document is judged as a whole
        else:
            key, label = person_verdict(len(judged), likely, possible)
        row = {
            "sender": sender,
            "messages_total": len(g),
            "messages_scored": len(scored),
            "messages_judged": len(judged),
            "likely_ai": likely,
            "some_signs": possible,
            "ai_likely_pct": 100 * likely / len(judged) if len(judged) else float("nan"),
            # Word-weighted, so a long message counts more than "ok thanks".
            "avg_score": avg,
            "total_words": int(scored["words"].sum()),
            "avg_words": scored["words"].mean() if len(scored) else float("nan"),
            "verdict": key,
            "verdict_label": label,
        }
        for k in FEATURES:
            row[k] = scored[k].mean() if len(scored) else float("nan")
        rows.append(row)
    order = {"ai": 0, "possible": 1, "none": 2, "inconclusive": 3}
    summary = pd.DataFrame(rows).set_index("sender")
    summary["_o"] = summary["verdict"].map(order)
    summary = summary.sort_values(["_o", "ai_likely_pct", "avg_score"], ascending=[True, False, False], na_position="last")
    return summary.drop(columns="_o")

# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

BAR = "#2a78d6"
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"

ACCURACY_NOTE = (
    "Measured on human writing from before AI chatbots existed: about 1% of texts of 30+ words get "
    "“Likely AI-written” by mistake, and none of 970 human writers judged on 10 texts each did. On AI-written test text it "
    "flags about half of typical assistant answers and essays as likely AI and about three in four "
    "with at least some signs, but AI told to write casually is usually missed. "
    "Texts under 30 words are too short to judge."
)


def chart_png(summary: pd.DataFrame) -> str:
    data = summary.dropna(subset=["avg_score"]).iloc[::-1]  # highest at top
    if data.empty:
        return ""
    h = max(1.6, 0.42 * len(data) + 1.0)
    fig, ax = plt.subplots(figsize=(8, h), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    labels = [s if len(s) <= 28 else s[:27] + "…" for s in data.index]
    bars = ax.barh(labels, data["avg_score"], color=BAR, height=0.6, zorder=2)
    for bar, avg, label in zip(bars, data["avg_score"], data["verdict_label"]):
        ax.text(bar.get_width() + 1.2, bar.get_y() + bar.get_height() / 2,
                f"{avg:.0f}  · {label}", va="center", fontsize=8.5, color=INK2)
    for x, name in [(POSSIBLE_AI, "some signs"), (LIKELY_AI, "likely AI")]:
        ax.axvline(x, color=INK2, linewidth=0.8, linestyle=(0, (3, 3)), zorder=1)
        ax.text(x, len(data) - 0.35, f" {name}", fontsize=7.5, color=INK2, va="bottom")
    ax.set_xlim(0, 140)
    ax.set_xticks(range(0, 101, 25))
    ax.set_xlabel("Average AI-likelihood score (word-weighted)", color=INK2, fontsize=9)
    ax.grid(axis="x", color=GRID, linewidth=0.8, zorder=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9, length=0)
    ax.tick_params(axis="y", colors=INK)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=SURFACE)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


PILL = {"ai": "high", "possible": "mid", "none": "low", "inconclusive": "na", "skipped": "na"}


def _fmt(v, digits=0) -> str:
    return "–" if v is None or pd.isna(v) else f"{v:.{digits}f}"


def render_html(df, summary, sources, top) -> str:
    e = html.escape
    png = chart_png(summary)
    feat_heads = "".join(f"<th>{e(FEATURE_LABELS[k])}</th>" for k in FEATURES)
    anchor = {sender: f"s-{i}" for i, sender in enumerate(summary.index)}

    rows = []
    for sender, s in summary.iterrows():
        rows.append(
            f"<tr><td><a href='#{anchor[sender]}'>{e(sender)}</a></td>"
            f"<td><span class='pill {PILL[s.verdict]}'>{e(s.verdict_label)}</span></td>"
            f"<td class='num'>{_fmt(s.avg_score)}</td>"
            f"<td class='num'>{int(s.likely_ai)} / {int(s.messages_judged)}</td>"
            f"<td class='num'>{int(s.messages_total)}</td>"
            + "".join(f"<td class='num'>{_fmt(display_value(k, s[k]))}</td>" for k in FEATURES)
            + "</tr>"
        )

    sections = []
    for sender, s in summary.iterrows():
        msgs = df[(df.sender == sender) & df.scored].sort_values("score", ascending=False)
        shown = msgs.head(top)
        body = []
        for _, m in shown.iterrows():
            ts = m.timestamp.strftime("%Y-%m-%d %H:%M") if pd.notna(m.timestamp) else ""
            body.append(
                f"<tr><td class='num'><span class='pill {PILL[m.verdict]}' title='{e(m.verdict_label)}'>{m.score:.0f}</span></td>"
                f"<td class='ts'>{e(ts)}</td><td class='msg'>{e(m.message)}</td>"
                + "".join(f"<td class='num'>{_fmt(display_value(k, m[k]))}</td>" for k in FEATURES)
                + "</tr>"
            )
        more = (f"<p class='muted'>Showing top {len(shown)} of {len(msgs)} scored messages.</p>"
                if len(msgs) > len(shown) else "")
        sections.append(
            f"<section id='{anchor[sender]}'><h3>{e(sender)}"
            f" <span class='pill {PILL[s.verdict]}'>{e(s.verdict_label)}</span></h3>"
            f"<p class='muted'>{int(s.likely_ai)} of {int(s.messages_judged)} messages long enough to judge look likely AI"
            f" ({int(s.some_signs)} more show some signs) · average score {_fmt(s.avg_score)}"
            f" · {int(s.messages_total)} messages in total.</p>"
            + (f"<div class='scroll'><table><thead><tr><th>Score</th><th>Time</th><th>Message</th>{feat_heads}"
               f"</tr></thead><tbody>{''.join(body)}</tbody></table></div>{more}" if body else "")
            + "</section>"
        )

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    chart = (f"<figure><img alt='Bar chart of average AI-likelihood score per sender' "
             f"src='data:image/png;base64,{png}'></figure>") if png else "<p>No scorable messages.</p>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ghost-check report</title>
<style>
:root {{ --bg:#fcfcfb; --card:#ffffff; --ink:#0b0b0b; --ink2:#52514e; --line:#e4e3df;
  --low:#e7f4e7; --lowink:#1d5e1d; --mid:#fdf1d8; --midink:#7a5200; --high:#fbe3e3; --highink:#9a2323; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#1a1a19; --card:#232322; --ink:#ffffff; --ink2:#c3c2b7;
  --line:#383835; --low:#1f3a1f; --lowink:#9fdc9f; --mid:#3d3218; --midink:#f5cf7a; --high:#4a2222; --highink:#f4a5a5; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }}
main {{ max-width:1100px; margin:0 auto; padding:24px 16px 64px; }}
h1 {{ font-size:24px; margin:0 0 4px; }} h2 {{ font-size:18px; margin:32px 0 8px; }} h3 {{ font-size:16px; margin:0 0 4px; }}
.muted {{ color:var(--ink2); margin:4px 0 12px; }}
.note {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px 14px; color:var(--ink2); }}
figure {{ margin:0; background:#fcfcfb; border:1px solid var(--line); border-radius:8px; padding:8px; }}
figure img {{ width:100%; height:auto; display:block; }}
section {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:16px; margin:16px 0; }}
.scroll {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; background:var(--card); }}
th, td {{ border-bottom:1px solid var(--line); padding:6px 8px; text-align:left; vertical-align:top; }}
th {{ font-size:12px; color:var(--ink2); font-weight:600; white-space:nowrap; }}
td.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
td.ts {{ white-space:nowrap; color:var(--ink2); font-size:12px; }}
td.msg {{ white-space:pre-wrap; min-width:280px; max-width:520px; overflow-wrap:anywhere; }}
a {{ color:inherit; }}
.pill {{ display:inline-block; padding:1px 8px; border-radius:999px; font-weight:600; font-size:12px; white-space:nowrap; }}
.pill.low {{ background:var(--low); color:var(--lowink); }} .pill.mid {{ background:var(--mid); color:var(--midink); }}
.pill.high {{ background:var(--high); color:var(--highink); }} .pill.na {{ color:var(--ink2); border:1px solid var(--line); }}
</style></head><body><main>
<h1>ghost-check report</h1>
<p class="muted">{e(", ".join(sources))} · generated {generated} · ghost-check {__version__}</p>
<p class="note"><b>How to read this.</b> Scores are 0–100 (higher = more AI-like). {e(ACCURACY_NOTE)}
A high score is a reason to look closer, not proof; a low score does not prove a human wrote it.</p>

<h2>Senders compared</h2>
{chart}

<h2>Summary</h2>
<div class="scroll"><table><thead><tr><th>Sender</th><th>Verdict</th><th>Avg score</th>
<th>Likely-AI msgs / judged</th><th>Messages</th>{feat_heads}</tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<p class="muted">Signal columns are 0–100 averages (higher = more AI-like). A person is called likely AI
only when at least two of their messages are, so one false alarm can't label someone.</p>

<h2>Per sender</h2>
{''.join(sections)}
</main></body></html>
"""


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ghost-check",
        description="Score WhatsApp chats, PDFs or text files for AI-written text and write an HTML report.",
    )
    p.add_argument("inputs", nargs="+", type=Path, help="WhatsApp export (.txt/.zip), .pdf or text files")
    p.add_argument("-o", "--output", type=Path, default=Path("ghost-check-report.html"),
                   help="HTML report path (default: %(default)s)")
    p.add_argument("--csv", type=Path, help="also write per-message scores to this CSV")
    p.add_argument("--top", type=int, default=25,
                   help="messages listed per sender in the report (default: %(default)s)")
    p.add_argument("--mode", choices=["auto", "chat", "document"], default="auto",
                   help="auto-detect, force WhatsApp chat parsing, or treat as plain document")
    p.add_argument("--by-page", action="store_true",
                   help="document mode: compare PDF pages instead of whole files")
    dates = p.add_mutually_exclusive_group()
    dates.add_argument("--dayfirst", dest="dayfirst", action="store_true", default=None,
                       help="dates in the export are dd/mm (default: auto-detect)")
    dates.add_argument("--monthfirst", dest="dayfirst", action="store_false",
                       help="dates in the export are mm/dd")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    records: list[Record] = []
    for path in args.inputs:
        if not path.exists():
            sys.exit(f"{path}: file not found")
        recs, mode = load(path, args.mode, args.dayfirst, args.by_page)
        print(f"{path.name}: {len(recs)} {'messages' if mode == 'chat' else 'passages'} ({mode} mode)")
        records += recs
    if not records:
        sys.exit("Nothing to analyse: no messages or text found.")

    df = pd.DataFrame([r.__dict__ for r in records])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = score_frame(df)
    summary = summarize(df)

    args.output.write_text(render_html(df, summary, [p.name for p in args.inputs], args.top), encoding="utf-8")
    if args.csv:
        df.to_csv(args.csv, index=False)

    print()
    table = summary[["verdict_label", "avg_score", "likely_ai", "messages_judged"]].copy()
    table["avg_score"] = table["avg_score"].round(1)
    table.columns = ["verdict", "avg score", "likely-AI msgs", "judged"]
    print(table.to_string())
    print(f"\nReport written to {args.output}")
    print("Heuristic signal, not proof. See the report for measured error rates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
