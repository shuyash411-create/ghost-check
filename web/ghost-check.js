/* ghost-check scoring engine for the browser.
 *
 * A line-for-line port of the parsing and scoring in ghost_check.py, so the
 * web app and the CLI give the same numbers. tests/check_parity.py compares
 * the two; keep them in sync when changing either.
 *
 * Works as a browser global (window.GhostCheck) and as a CommonJS module.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.GhostCheck = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ------------------------------------------------------------------------
  // Parsing
  // ------------------------------------------------------------------------

  const DATE = String.raw`(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})`;
  const TIME = String.raw`(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\s*([AaPp])\.?\s*[Mm]\.?)?`;
  // Android: "31/12/23, 9:15 pm - Name: text"
  const ANDROID_RE = new RegExp(String.raw`^${DATE},?\s+${TIME}\s+[-–]\s+(.*)$`, "u");
  // iOS:     "[31/12/2023, 21:15:03] Name: text"
  const IOS_RE = new RegExp(String.raw`^\[${DATE},?\s+${TIME}\]\s*(.*)$`, "u");

  const INVISIBLE_RE = /[\u200e\u200f\u202a-\u202e\ufeff]/g;
  const SPACES_RE = /[\u202f\u00a0\u2007]/g;
  const LINE_SPLIT_RE = /\r\n|[\n\r\u000b\u000c\u001c-\u001e\u0085\u2028\u2029]/;

  const SKIP_BODIES = new RegExp(
    String.raw`^(<media omitted>|<attached:.*>|.*\b(image|video|audio|sticker|gif|document|contact card) omitted` +
      String.raw`|this message was deleted|you deleted this message|null|missed (voice|video) call` +
      String.raw`|waiting for this message.*|poll:.*)$`,
    "is"
  );
  const EDITED_SUFFIX = /\s*<this message was edited>\s*$/i;

  const clean = (line) => line.replace(INVISIBLE_RE, "").replace(SPACES_RE, " ");
  const match = (line) => ANDROID_RE.exec(line) || IOS_RE.exec(line);

  function detectDayfirst(lines) {
    for (const line of lines) {
      const m = match(line);
      if (!m) continue;
      const a = +m[1], b = +m[2];
      if (a > 12) return true;
      if (b > 12) return false;
    }
    return true;
  }

  function toDate(m, dayfirst) {
    const [a, b, y, hh, mm, ss, ampm] = m.slice(1, 8);
    const day = dayfirst ? +a : +b;
    const month = dayfirst ? +b : +a;
    const year = +y + (y.length === 2 ? 2000 : 0);
    let hour = +hh;
    if (ampm) {
      const p = ampm.toLowerCase();
      if (p === "p" && hour !== 12) hour += 12;
      else if (p === "a" && hour === 12) hour = 0;
    }
    const d = new Date(year, month - 1, day, hour, +mm, +(ss || 0));
    const valid = d.getFullYear() === year && d.getMonth() === month - 1 && d.getDate() === day &&
      hour < 24 && +mm < 60 && +(ss || 0) < 60;
    return valid ? d : null;
  }

  function splitLines(text) {
    const lines = text.split(LINE_SPLIT_RE);
    if (lines.length && lines[lines.length - 1] === "") lines.pop();
    return lines;
  }

  /** WhatsApp export -> [{sender, timestamp, message, source}] */
  function parseWhatsApp(text, source, dayfirst) {
    const lines = splitLines(text).map(clean);
    if (dayfirst == null) dayfirst = detectDayfirst(lines);

    const records = [];
    let current = null;
    for (const line of lines) {
      const m = match(line);
      if (m) {
        if (current) records.push(current);
        current = null;
        const rest = m[8];
        const i = rest.indexOf(": ");
        if (i < 0) continue; // system message
        current = { sender: rest.slice(0, i).trim(), timestamp: toDate(m, dayfirst), message: rest.slice(i + 2), source, kind: "chat" };
      } else if (current) {
        current.message += "\n" + line;
      }
    }
    if (current) records.push(current);

    return records.filter((r) => {
      r.message = r.message.replace(EDITED_SUFFIX, "").trim();
      return r.message && !SKIP_BODIES.test(r.message);
    });
  }

  const pySplit = (s) => s.split(/\s+/).filter(Boolean);

  /** Split free text into paragraph-sized chunks. */
  function paragraphs(text, maxWords = 180) {
    text = text.replace(/-\n(?=[a-z])/g, "");
    const chunks = [];
    for (let para of text.split(/\n\s*\n/)) {
      para = para.replace(/\s*\n\s*/g, " ").trim();
      if (!para) continue;
      if (pySplit(para).length <= maxWords) {
        chunks.push(para);
        continue;
      }
      let buf = [];
      for (const sent of para.split(/(?<=[.!?])\s+/)) {
        buf.push(sent);
        if (buf.reduce((n, s) => n + pySplit(s).length, 0) >= maxWords * 0.75) {
          chunks.push(buf.join(" "));
          buf = [];
        }
      }
      if (buf.length) chunks.push(buf.join(" "));
    }
    return chunks;
  }

  function stem(name) {
    const base = name.split(/[\\/]/).pop();
    const i = base.lastIndexOf(".");
    return i > 0 ? base.slice(0, i) : base;
  }

  /** pages: array of page texts */
  function parseDocument(pages, source, byPage, label) {
    const name = label || stem(source);
    const records = [];
    pages.forEach((page, i) => {
      const sender = byPage ? `${name} · p.${i + 1}` : name;
      for (const p of paragraphs(page)) records.push({ sender, timestamp: null, message: p, source, kind: "document" });
    });
    return records;
  }

  /** Auto-detect chat vs document, as the CLI does. */
  function load(pages, source, { mode = "auto", dayfirst = null, byPage = false, label } = {}) {
    const text = pages.join("\n");
    if (mode === "auto" || mode === "chat") {
      const chat = parseWhatsApp(text, source, dayfirst);
      if (mode === "chat" || chat.length >= 3) return { records: chat, mode: "chat" };
    }
    return { records: parseDocument(pages, source, byPage, label), mode: "document" };
  }

  // ------------------------------------------------------------------------
  // Scoring (model v2, same as ghost_check.py; see "Accuracy" in README.md)
  // ------------------------------------------------------------------------

  const AI_VOCAB = new Set(`
delve delves delving delved showcase showcases showcasing underscore underscores underscoring
crucial crucially pivotal intricate intricacies meticulous meticulously comprehensive notably
noteworthy commendable realm realms landscape tapestry foster fosters fostering enhance enhances
enhancing bolster streamline streamlines leverage leveraging seamless seamlessly robust nuanced
multifaceted holistic paramount invaluable unwavering embark navigate navigating elevate empower
empowers empowering unlock unlocking harness vibrant testament profound additionally furthermore
moreover ultimately overall essential vital ensure ensures ensuring potential insights valuable
effectively prioritize resonate dynamic innovative transformative strive facilitate optimal
significant significantly journey thrive`.split(/\s+/).filter(Boolean));

  const STOCK_PHRASES = new RegExp(
    String.raw`it'?s (?:important|worth|essential|crucial) to|it is (?:important|worth noting|essential|crucial)` +
      String.raw`|plays? an? (?:crucial|vital|key|pivotal|significant) role|in today'?s|whether you'?re` +
      String.raw`|i hope this|hope this (?:message|email) finds you|let me know if|feel free to|happy to help` +
      String.raw`|here'?s (?:a|an|some|how|what)\b|here are (?:some|a few)|great question|i understand (?:your|that)` +
      String.raw`|on the other hand|a wide range of|when it comes to|not only\b[^.]*\bbut also|by doing so` +
      String.raw`|don'?t hesitate|rest assured|thank you for reaching out|i'?d be happy to|as an ai|as a language model` +
      String.raw`|can make a (?:big|meaningful|significant|real) difference|in the long run|key (?:factors|takeaways)` +
      String.raw`|a testament to|that being said|(?:certainly|absolutely|of course)!`,
    "g"
  );

  const TRANSITIONS = new RegExp(
    String.raw`^(?:additionally|furthermore|moreover|however|overall|ultimately|in conclusion|in summary` +
      String.raw`|in addition|firstly|secondly|finally|lastly|on the other hand|as a result|by doing so)\b`,
    "i"
  );

  // Fitted by eval/fit.py.
  const MODEL = {
    intercept: -1.249,
    word_length: 0.59,
    rhythm: 0.541,
    ai_vocab: 1.741,
    stock_phrases: 2.039,
    transitions: 0.448,
  };
  const LIKELY_AI = 83.8; // ~1% of human texts at or above
  const POSSIBLE_AI = 63.7; // ~5% of human texts at or above
  const MIN_WORDS = 6;
  const MIN_VERDICT_WORDS = 30;

  const FEATURES = ["word_length", "rhythm", "ai_vocab", "stock_phrases", "transitions"];
  const FEATURE_LABELS = {
    word_length: "Long words",
    rhythm: "Even sentence rhythm",
    ai_vocab: "AI-typical words",
    stock_phrases: "Stock AI phrases",
    transitions: "Formulaic transitions",
  };
  const FEATURE_HELP = {
    word_length: "AI vocabulary skews toward longer words.",
    rhythm: "AI writes evenly sized sentences with little variation (low variance and burstiness).",
    ai_vocab: "Words LLMs overuse, like “delve”, “crucial”, “foster”, “additionally”.",
    stock_phrases: "Phrases like “it's worth noting”, “feel free to”, “plays a crucial role”.",
    transitions: "Sentences opening with “Furthermore”, “Additionally”, “Ultimately”…",
  };
  const VERDICTS = {
    ai: "Likely AI-written",
    possible: "Some AI signs",
    none: "No clear AI signs",
    inconclusive: "Too short to judge",
  };

  const EDGE = new Set([...".,;:!?—–-()\"'’“”…*_~`[]{}<>"]);
  const clamp = (x) => Math.max(0, Math.min(1, x));
  const isAlpha = (ch) => /\p{L}/u.test(ch);
  const cpLen = (s) => [...s].length;
  const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;
  const pstdev = (xs) => {
    const m = mean(xs);
    return Math.sqrt(mean(xs.map((x) => (x - m) ** 2)));
  };

  function stripEdge(tok) {
    const cs = [...tok];
    let i = 0, j = cs.length;
    while (i < j && EDGE.has(cs[i])) i++;
    while (j > i && EDGE.has(cs[j - 1])) j--;
    return cs.slice(i, j).join("");
  }

  function words(text) {
    const out = [];
    for (let tok of pySplit(text)) {
      tok = stripEdge(tok);
      if ([...tok].some(isAlpha)) out.push(tok);
    }
    return out;
  }

  const SENT_SPLIT = /(?<=[.!?…])\s+|\n+/u;
  const sentences = (text) => text.split(SENT_SPLIT).map((p) => p.trim()).filter((p) => words(p).length);

  function scoreFeatures(text) {
    const ws = words(text);
    const n = ws.length;
    const sents = sentences(text);
    const lengths = sents.map((s) => words(s).length);
    const f = { words: n, sentences: sents.length };

    f.word_length = n ? clamp((mean(ws.map(cpLen)) - 3.6) / 1.6) : NaN;

    if (lengths.length >= 2) {
      const m = mean(lengths);
      const uniform = clamp(1 - pstdev(lengths) / m / 0.8);
      if (lengths.length >= 3) {
        const jumps = mean(lengths.slice(1).map((b, i) => Math.abs(lengths[i] - b)));
        f.rhythm = (uniform + clamp(1 - jumps / m / 0.9)) / 2;
      } else f.rhythm = uniform;
    } else f.rhythm = NaN;

    const low = text.toLowerCase().replace(/’/g, "'");
    f.ai_vocab = n ? (100 * ws.filter((w) => AI_VOCAB.has(w.toLowerCase().replace(/’/g, "'"))).length) / n : NaN;
    f.stock_phrases = (low.match(STOCK_PHRASES) || []).length;
    f.transitions = sents.length ? sents.filter((s) => TRANSITIONS.test(s)).length / sents.length : NaN;
    return f;
  }

  function modelInputs(f) {
    const z = (v) => (Number.isNaN(v) ? 0 : v);
    return [
      z(f.word_length),
      Number.isNaN(f.rhythm) ? 0.5 : f.rhythm,
      Number.isNaN(f.ai_vocab) ? 0 : Math.log1p(f.ai_vocab),
      Math.min(f.stock_phrases, 3),
      z(f.transitions),
    ];
  }

  function modelScore(f) {
    const x = modelInputs(f);
    const z = FEATURES.reduce((acc, k, i) => acc + MODEL[k] * x[i], MODEL.intercept);
    return 100 / (1 + Math.exp(-z));
  }

  /** Feature on a 0-100 "how AI-like" scale for bars and tables. */
  function displayValue(key, v) {
    if (v == null || Number.isNaN(v)) return NaN;
    if (key === "ai_vocab" || key === "stock_phrases") return Math.min(100, (100 * v) / 3);
    return 100 * v;
  }

  function verdict(score, nWords) {
    let key;
    if (nWords < MIN_VERDICT_WORDS || Number.isNaN(score)) key = "inconclusive";
    else if (score >= LIKELY_AI) key = "ai";
    else if (score >= POSSIBLE_AI) key = "possible";
    else key = "none";
    return { key, label: VERDICTS[key] };
  }

  /** A sender needs two "Likely AI" messages (and 20% of those judged) to be called likely AI. */
  function personVerdict(judged, likely, possible) {
    let key;
    if (likely >= 2 && likely / judged >= 0.2) key = "ai";
    else if (likely >= 1 || (judged && possible / judged >= 0.3)) key = "possible";
    else if (judged < 2) key = "inconclusive";
    else key = "none";
    return { key, label: key === "inconclusive" ? "Too few long messages to judge" : VERDICTS[key] };
  }

  /** Adds features, score and verdict to each record (in place). */
  function scoreRecords(records, minWords = MIN_WORDS) {
    for (const r of records) {
      Object.assign(r, scoreFeatures(r.message));
      r.scored = r.words >= minWords;
      r.score = r.scored ? modelScore(r) : NaN;
      const v = r.scored ? verdict(r.score, r.words) : { key: "skipped", label: "Not scored" };
      r.verdict = v.key;
      r.verdict_label = v.label;
    }
    return records;
  }

  const ORDER = { ai: 0, possible: 1, none: 2, inconclusive: 3 };

  /** Per-sender summary, most AI-like first. */
  function summarize(records) {
    const map = new Map();
    for (const r of records) {
      if (!map.has(r.sender)) map.set(r.sender, []);
      map.get(r.sender).push(r);
    }
    const rows = [...map.entries()].map(([sender, g]) => {
      const scored = g.filter((r) => r.scored);
      const judged = g.filter((r) => r.verdict in ORDER && r.verdict !== "inconclusive");
      const likely = judged.filter((r) => r.verdict === "ai").length;
      const possible = judged.filter((r) => r.verdict === "possible").length;
      const wsum = scored.reduce((a, r) => a + r.words, 0);
      const avg = wsum ? scored.reduce((a, r) => a + r.score * r.words, 0) / wsum : NaN;
      // A document is judged as a whole; a chat sender by their individual messages.
      const v = g.every((r) => r.kind === "document") ? verdict(avg, wsum) : personVerdict(judged.length, likely, possible);
      const row = {
        sender,
        messages_total: g.length,
        messages_scored: scored.length,
        messages_judged: judged.length,
        likely_ai: likely,
        some_signs: possible,
        ai_likely_pct: judged.length ? (100 * likely) / judged.length : NaN,
        avg_score: avg,
        avg_words: scored.length ? mean(scored.map((r) => r.words)) : NaN,
        total_words: wsum,
        verdict: v.key,
        verdict_label: v.label,
      };
      for (const k of FEATURES) {
        const vals = scored.map((r) => r[k]).filter((x) => !Number.isNaN(x));
        row[k] = vals.length ? mean(vals) : NaN;
      }
      return row;
    });
    const key = (v) => (Number.isNaN(v) ? -Infinity : v);
    rows.sort((a, b) => ORDER[a.verdict] - ORDER[b.verdict] || key(b.ai_likely_pct) - key(a.ai_likely_pct) || key(b.avg_score) - key(a.avg_score));
    return rows;
  }

  return {
    parseWhatsApp, parseDocument, paragraphs, load, scoreFeatures, scoreRecords, summarize,
    verdict, personVerdict, displayValue,
    FEATURES, FEATURE_LABELS, FEATURE_HELP, VERDICTS, LIKELY_AI, POSSIBLE_AI, MIN_VERDICT_WORDS,
  };
});
