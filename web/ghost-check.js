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
        current = { sender: rest.slice(0, i).trim(), timestamp: toDate(m, dayfirst), message: rest.slice(i + 2), source };
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
      for (const p of paragraphs(page)) records.push({ sender, timestamp: null, message: p, source });
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
  // Scoring
  // ------------------------------------------------------------------------

  const STOCK_PHRASES = [
    "it's important to note", "it is important to note", "it's worth noting", "it is worth noting",
    "delve", "in conclusion", "in summary", "additionally,", "furthermore,", "moreover,",
    "i hope this helps", "i hope this message finds you", "hope this message finds you",
    "feel free to", "let me know if you have any", "don't hesitate to", "great question",
    "certainly!", "absolutely!", "as an ai", "as a language model", "here's a", "here are some",
    "key takeaways", "navigate the", "tapestry", "in today's fast-paced", "a testament to",
    "plays a crucial role", "crucial role", "seamless", "leverage", "foster", "embark",
    "overall,", "ultimately,", "that being said", "on the other hand", "not only", "whether you're",
    "i understand your", "thank you for reaching out", "rest assured", "navigating",
    "comprehensive", "streamline", "elevate", "unlock", "empower",
  ];
  const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const STOCK_RE = new RegExp(STOCK_PHRASES.map(escapeRe).join("|"), "g");

  const PUNCT = new Set(".,;:!?—–-()\"'’“”…");
  const STRIP_CHARS = new Set([...PUNCT, ..."*_~`[]{}<>"]);
  const TERMINAL = ".!?…";

  const WEIGHTS = {
    sentence_variance: 0.2,
    burstiness: 0.2,
    punctuation: 0.2,
    word_length: 0.15,
    repetition: 0.25,
  };
  const FEATURE_LABELS = {
    sentence_variance: "Uniform sentences",
    burstiness: "Low burstiness",
    punctuation: "Punctuation",
    word_length: "Word length",
    repetition: "Stock / repeated phrases",
  };
  const FEATURE_HELP = {
    sentence_variance: "AI writes evenly sized sentences; people vary more.",
    burstiness: "People alternate short and long sentences; AI rhythm is flat.",
    punctuation: "Tidy punctuation, capitalised sentences, semicolons and em dashes (—).",
    word_length: "AI vocabulary skews toward longer words.",
    repetition: "Stock AI phrases, list formatting and reused boilerplate.",
  };

  const clamp = (x) => Math.max(0, Math.min(1, x));
  const isAlpha = (ch) => /\p{L}/u.test(ch);
  const isUpper = (ch) => ch !== ch.toLowerCase() && ch === ch.toUpperCase();
  const isLower = (ch) => ch !== ch.toUpperCase() && ch === ch.toLowerCase();
  const cpLen = (s) => [...s].length;
  const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;
  const pstdev = (xs) => {
    const m = mean(xs);
    return Math.sqrt(mean(xs.map((x) => (x - m) ** 2)));
  };

  function stripChars(tok) {
    const cs = [...tok];
    let i = 0, j = cs.length;
    while (i < j && STRIP_CHARS.has(cs[i])) i++;
    while (j > i && STRIP_CHARS.has(cs[j - 1])) j--;
    return cs.slice(i, j).join("");
  }

  function words(text) {
    const out = [];
    for (let tok of pySplit(text)) {
      tok = stripChars(tok);
      if ([...tok].some(isAlpha)) out.push(tok);
    }
    return out;
  }

  const SENT_SPLIT = /(?<=[.!?…])\s+|\n+/u;
  function sentences(text) {
    return text.split(SENT_SPLIT).map(words).filter((w) => w.length);
  }

  function ngrams(ws, n) {
    const lw = ws.map((w) => w.toLowerCase());
    const out = [];
    for (let i = 0; i + n <= lw.length; i++) out.push(lw.slice(i, i + n).join("\u0001"));
    return out;
  }

  function scoreFeatures(text) {
    const ws = words(text);
    const n = ws.length;
    const lengths = sentences(text).map((s) => s.length);
    const f = { words: n, sentences: lengths.length };

    // 1. Sentence length variance
    if (lengths.length >= 2) {
      const m = mean(lengths);
      const cv = m ? pstdev(lengths) / m : 0;
      f.sentence_variance = clamp(1 - cv / 0.8);
    } else f.sentence_variance = NaN;

    // 2. Burstiness
    if (lengths.length >= 3) {
      const m = mean(lengths);
      const jumps = mean(lengths.slice(1).map((b, i) => Math.abs(lengths[i] - b)));
      f.burstiness = clamp(1 - jumps / m / 0.9);
    } else f.burstiness = NaN;

    // 3. Punctuation
    if (n) {
      let collapsed = text.replace(/([^\p{L}\p{N}_\s])\1+/gu, "$1");
      collapsed = collapsed.replace(/(?<=[\p{L}\p{N}_])['’\-](?=[\p{L}\p{N}_])/gu, "");
      const density = [...collapsed].filter((c) => PUNCT.has(c)).length / n;
      let s = clamp((density - 0.05) / 0.15);
      const stripped = text.trim();
      const starts = stripped.split(SENT_SPLIT).map((p) => p.trim()).filter(Boolean).map((p) => [...p][0]);
      const cased = starts.filter((c) => isUpper(c) || isLower(c));
      const capRatio = cased.length ? cased.filter(isUpper).length / cased.length : 0.5;
      if (text.includes("—")) s += 0.3;
      if (text.includes(";")) s += 0.1;
      if (capRatio === 1 && TERMINAL.includes([...stripped].pop() || "\u0000")) s += 0.15;
      else if (capRatio < 0.5) s -= 0.2;
      if (/[!?]{2,}|\.{4,}/.test(text)) s -= 0.25;
      f.punctuation = clamp(s);
    } else f.punctuation = NaN;

    // 4. Average word length
    f.word_length = n ? clamp((mean(ws.map(cpLen)) - 3.6) / 1.6) : NaN;

    // 5. Stock / repeated phrases (cross-message part added in scoreRecords)
    const low = text.toLowerCase().replace(/’/g, "'");
    const hits = (low.match(STOCK_RE) || []).length;
    const tri = ngrams(ws, 3);
    let rep = 0;
    if (tri.length) {
      const c = new Map();
      for (const t of tri) c.set(t, (c.get(t) || 0) + 1);
      let extra = 0;
      for (const v of c.values()) if (v > 1) extra += v - 1;
      rep = extra / tri.length;
    }
    let structure = /^\s*(\d+[.)]|[-•*])\s+\S/m.test(text) ? 0.2 : 0;
    structure += /\*\*[^*]+\*\*/.test(text) ? 0.1 : 0;
    f.repetition = clamp(0.35 * hits + 1.5 * rep + structure);
    f.stock_hits = hits;
    return f;
  }

  function combine(r) {
    let num = 0, den = 0;
    for (const [k, w] of Object.entries(WEIGHTS)) {
      if (!Number.isNaN(r[k])) {
        num += w * r[k];
        den += w;
      }
    }
    return den ? (100 * num) / den : NaN;
  }

  /** Adds features, `scored` and `score` to each record (in place). */
  function scoreRecords(records, minWords = 6) {
    for (const r of records) Object.assign(r, scoreFeatures(r.message));

    const bySender = new Map();
    records.forEach((r) => {
      if (!bySender.has(r.sender)) bySender.set(r.sender, []);
      bySender.get(r.sender).push(r);
    });
    for (const group of bySender.values()) {
      const grams = group.map((r) => new Set(ngrams(words(r.message), 4)));
      const counts = new Map();
      for (const g of grams) for (const x of g) counts.set(x, (counts.get(x) || 0) + 1);
      group.forEach((r, i) => {
        const g = grams[i];
        if (!g.size) return;
        let reused = 0;
        for (const x of g) if (counts.get(x) >= 3) reused++;
        r.repetition = clamp(r.repetition + 0.6 * (reused / g.size));
      });
    }

    for (const r of records) {
      r.scored = r.words >= minWords;
      r.score = r.scored ? combine(r) : NaN;
    }
    return records;
  }

  /** Per-sender summary, sorted by AI-likely % then average score. */
  function summarize(records, threshold = 50) {
    const map = new Map();
    for (const r of records) {
      if (!map.has(r.sender)) map.set(r.sender, { sender: r.sender, total: 0, scored: [] });
      const s = map.get(r.sender);
      s.total++;
      if (r.scored) s.scored.push(r);
    }
    const rows = [...map.values()].map(({ sender, total, scored }) => {
      const row = {
        sender,
        messages_total: total,
        messages_scored: scored.length,
        avg_score: scored.length ? mean(scored.map((r) => r.score)) : NaN,
        ai_likely_pct: scored.length ? (100 * scored.filter((r) => r.score >= threshold).length) / scored.length : NaN,
        avg_words: scored.length ? mean(scored.map((r) => r.words)) : NaN,
      };
      for (const k of Object.keys(WEIGHTS)) {
        const vals = scored.map((r) => r[k]).filter((v) => !Number.isNaN(v));
        row[k] = vals.length ? mean(vals) : NaN;
      }
      return row;
    });
    const key = (v) => (Number.isNaN(v) ? -Infinity : v);
    rows.sort((a, b) => key(b.ai_likely_pct) - key(a.ai_likely_pct) || key(b.avg_score) - key(a.avg_score));
    return rows;
  }

  return {
    parseWhatsApp, parseDocument, paragraphs, load, scoreFeatures, scoreRecords, summarize,
    WEIGHTS, FEATURE_LABELS, FEATURE_HELP,
  };
});
