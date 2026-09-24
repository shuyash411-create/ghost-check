/* ghost-check classifier for the browser.
 *
 * A port of ghost_check.py's scoring: the same text normalisation and ~200-word
 * sections, scikit-learn's word (1-2 gram) and char_wb (3-5 gram) TF-IDF, and
 * the logistic regression, using the weights exported by train/export_web.py.
 * tests/check_parity.py checks that this gives the same scores as the CLI.
 *
 * Works as a browser global (window.GhostCheck) and as a CommonJS module.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.GhostCheck = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ------------------------------------------------------------------ text

  const QUOTES = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "―": "-", "…": "...", " ": " ", " ": " ",
  };
  const QUOTES_RE = new RegExp(`[${Object.keys(QUOTES).join("")}]`, "gu");

  /** Same as ghost_check.normalise(). */
  function normalise(text) {
    text = text.normalize("NFKC").replace(QUOTES_RE, (c) => QUOTES[c]);
    text = text.replace(/-\n(?=[a-z])/g, "");
    text = text.replace(/[ \t\r\f\v]+/g, " ");
    text = text.replace(/\s*\n\s*/g, "\n");
    return text.trim();
  }

  const splitWords = (s) => s.split(/\s+/).filter(Boolean);
  const SENT = /(?<=[.!?])\s+/u; // sentence ends only; PDF line wraps are not boundaries

  /** Same as ghost_check.chunk_text(). */
  function chunkText(text, target = 200, minimum = 60) {
    const chunks = [];
    let buf = [], n = 0;
    for (let sent of text.split(SENT)) {
      sent = sent.trim();
      if (!sent) continue;
      buf.push(sent);
      n += splitWords(sent).length;
      if (n >= target) {
        chunks.push(buf);
        buf = [];
        n = 0;
      }
    }
    if (buf.length) {
      if (chunks.length && n < minimum) chunks[chunks.length - 1].push(...buf);
      else chunks.push(buf);
    }
    return chunks.map((c) => c.join(" "));
  }

  // ------------------------------------------------------------------ features

  // scikit-learn token_pattern r"(?u)\b\w+\b|[^\w\s]". Python's \w is letters, numbers and
  // underscore (not combining marks), so \b\w+\b is just a maximal run of those.
  const TOKEN = /[\p{L}\p{N}_]+|[^\p{L}\p{N}_\s]/gu;

  function wordNgrams(text) {
    const toks = text.toLowerCase().match(TOKEN) || [];
    const out = toks.slice();
    for (let i = 0; i + 1 < toks.length; i++) out.push(toks[i] + " " + toks[i + 1]);
    return out;
  }

  // scikit-learn analyzer="char_wb", ngram_range=(3, 5)
  function charWbNgrams(text, minN = 3, maxN = 5) {
    const out = [];
    for (const word of text.replace(/\s\s+/g, " ").split(/\s+/).filter(Boolean)) {
      const w = [" ", ...word, " "];
      for (let n = minN; n <= maxN; n++) {
        let offset = 0;
        out.push(w.slice(offset, offset + n).join(""));
        while (offset + n < w.length) {
          offset += 1;
          out.push(w.slice(offset, offset + n).join(""));
        }
        if (offset === 0) break;
      }
    }
    return out;
  }

  /** Sparse TF-IDF row (sublinear tf, l2-normalised) as Map<column, value>. */
  function tfidf(grams, vocab, idf) {
    const tf = new Map();
    for (const g of grams) {
      const j = vocab.get(g);
      if (j !== undefined) tf.set(j, (tf.get(j) || 0) + 1);
    }
    let norm = 0;
    for (const [j, c] of tf) {
      const v = (1 + Math.log(c)) * idf[j];
      tf.set(j, v);
      norm += v * v;
    }
    norm = Math.sqrt(norm);
    if (norm > 0) for (const [j, v] of tf) tf.set(j, v / norm);
    return tf;
  }

  // ------------------------------------------------------------------ model

  /** Build a model from model.json (parsed) and weights.bin (ArrayBuffer). */
  function createModel(meta, weightsBuffer) {
    const nw = meta.word_terms.length, nc = meta.char_terms.length;
    const f = new Float32Array(weightsBuffer);
    if (f.length !== 2 * nw + 2 * nc) throw new Error("weights.bin does not match model.json");
    const model = {
      meta,
      wordVocab: new Map(meta.word_terms.map((t, i) => [t, i])),
      charVocab: new Map(meta.char_terms.map((t, i) => [t, i])),
      wordIdf: f.subarray(0, nw),
      wordCoef: f.subarray(nw, 2 * nw),
      charIdf: f.subarray(2 * nw, 2 * nw + nc),
      charCoef: f.subarray(2 * nw + nc),
      readable: new Set(meta.readable),
    };
    return model;
  }

  /** Browser: fetch the exported model files. */
  async function loadModel(base = "model/") {
    const [meta, weights] = await Promise.all([
      fetch(base + "model.json").then((r) => { if (!r.ok) throw new Error(`model.json: HTTP ${r.status}`); return r.json(); }),
      fetch(base + "weights.bin").then((r) => { if (!r.ok) throw new Error(`weights.bin: HTTP ${r.status}`); return r.arrayBuffer(); }),
    ]);
    return createModel(meta, weights);
  }

  function verdict(score, m) {
    if (score >= m.threshold_likely) return "Likely AI-written";
    if (score >= m.threshold_possible) return "Possibly AI-written";
    return "No clear AI signs";
  }

  /** Same result shape and wording as ghost_check.score_text(). */
  function scoreText(rawText, model) {
    const m = model.meta;
    const text = normalise(rawText);
    const words = splitWords(text).length;
    if (words < m.min_doc_words) {
      return { words, score: null, verdict: "Too short to judge",
        reason: `Only ${words} words; at least ${m.min_doc_words} are needed for a meaningful score.` };
    }
    const chunks = chunkText(text, m.chunk_words, m.min_chunk_words);
    const probs = [];
    const contrib = new Map(); // readable word feature -> summed coef * x
    for (const chunk of chunks) {
      const xw = tfidf(wordNgrams(chunk), model.wordVocab, model.wordIdf);
      const xc = tfidf(charWbNgrams(chunk), model.charVocab, model.charIdf);
      let z = m.intercept;
      for (const [j, v] of xw) {
        z += v * model.wordCoef[j];
        if (model.readable.has(j)) contrib.set(j, (contrib.get(j) || 0) + v * model.wordCoef[j]);
      }
      for (const [j, v] of xc) z += v * model.charCoef[j];
      probs.push(1 / (1 + Math.exp(-z)));
    }
    const sizes = chunks.map((c) => splitWords(c).length);
    const score = probs.reduce((a, p, i) => a + p * sizes[i], 0) / sizes.reduce((a, b) => a + b, 0);
    const v = verdict(score, m);
    const aiLike = probs.filter((p) => p >= m.threshold_possible).length;

    const ranked = [...contrib.entries()].map(([j, s]) => [j, s / chunks.length]);
    const aiTerms = ranked.filter(([, s]) => s > 0).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([j]) => m.word_terms[j]);
    const humanTerms = ranked.filter(([, s]) => s < 0).sort((a, b) => a[1] - b[1]).slice(0, 3).map(([j]) => m.word_terms[j]);
    let reason = `${aiLike} of ${chunks.length} section${chunks.length !== 1 ? "s" : ""} read as AI-like`;
    if (aiTerms.length && score >= m.threshold_possible) reason += "; AI-leaning wording: " + aiTerms.map((t) => `'${t}'`).join(", ");
    else if (humanTerms.length) reason += "; human-leaning wording: " + humanTerms.map((t) => `'${t}'`).join(", ");
    if (words < 250) reason += ` (short text, ${words} words: lower confidence)`;
    return { words, sections: chunks.length, score, verdict: v, reason,
      section_scores: probs.map((p) => Math.round(p * 1000) / 1000), chunks };
  }

  return { normalise, chunkText, wordNgrams, charWbNgrams, createModel, loadModel, scoreText, verdict };
});
