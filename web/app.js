/* ghost-check web UI: reads input, scores it with GhostCheck (ghost-check.js), renders results. */
(function () {
  "use strict";
  const GC = window.GhostCheck;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (v, d = 0) => (v == null || Number.isNaN(v) ? "–" : `${(100 * v).toFixed(d)}%`);

  let model = null;
  let tab = "text";
  let files = []; // [{name, text, kind, pages}]

  // ---------------------------------------------------------------- model
  function setStatus(msg, error = false) {
    $("status").textContent = msg;
    $("status").classList.toggle("error", error);
  }

  GC.loadModel("model/")
    .then((m) => {
      model = m;
      $("analyze").disabled = false;
      $("analyze").textContent = "Check for AI";
    })
    .catch((e) => {
      $("analyze").textContent = "Model unavailable";
      setStatus(`Could not load the model: ${e.message}. Reload the page to try again.`, true);
    });

  // ---------------------------------------------------------------- tabs
  function setTab(t) {
    tab = t;
    for (const id of ["text", "file"]) {
      $(`tab-${id}`).setAttribute("aria-selected", String(id === t));
      $(`panel-${id}`).hidden = id !== t;
    }
  }
  $("tab-text").onclick = () => setTab("text");
  $("tab-file").onclick = () => setTab("file");

  // ---------------------------------------------------------------- files
  function decode(buf) {
    const b = new Uint8Array(buf);
    if (b[0] === 0xff && b[1] === 0xfe) return new TextDecoder("utf-16le").decode(b);
    if (b[0] === 0xfe && b[1] === 0xff) return new TextDecoder("utf-16be").decode(b);
    try {
      return new TextDecoder("utf-8", { fatal: true }).decode(b).replace(/^﻿/, "");
    } catch {
      return new TextDecoder("windows-1252").decode(b);
    }
  }

  let pdfjsReady = null;
  function loadPdfjs() {
    return (pdfjsReady ||= new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = "vendor/pdf.min.js";
      s.onload = () => { window.pdfjsLib.GlobalWorkerOptions.workerSrc = "vendor/pdf.worker.min.js"; res(window.pdfjsLib); };
      s.onerror = () => rej(new Error("could not load the PDF reader"));
      document.head.appendChild(s);
    }));
  }

  async function readPdf(buf) {
    const pdfjs = await loadPdfjs();
    const pdf = await pdfjs.getDocument({ data: buf }).promise;
    const pages = [];
    for (let p = 1; p <= pdf.numPages; p++) {
      const content = await (await pdf.getPage(p)).getTextContent();
      let text = "";
      for (const it of content.items) {
        if (!("str" in it)) continue;
        text += it.str;
        if (it.hasEOL) text += "\n";
      }
      pages.push(text);
    }
    const text = pages.join("\n\n");
    if (!text.trim()) throw new Error("no text layer (probably a scanned PDF). Run it through OCR first.");
    return { text, pages: pdf.numPages };
  }

  async function addFiles(list) {
    for (const f of list) {
      setStatus(`Reading ${f.name}…`);
      try {
        const buf = await f.arrayBuffer();
        const isPdf = f.name.toLowerCase().endsWith(".pdf") || f.type === "application/pdf";
        const r = isPdf ? await readPdf(buf) : { text: decode(buf), pages: null };
        files.push({ name: f.name, text: r.text, kind: isPdf ? "PDF" : "Text", pages: r.pages });
        setStatus("");
      } catch (e) {
        setStatus(`${f.name}: ${e.message}`, true);
      }
    }
    renderFiles();
  }

  function renderFiles() {
    $("files").innerHTML = files.map((f, i) =>
      `<li><span>${esc(f.name)} <span class="muted small">· ${f.kind}${f.pages ? `, ${f.pages} page${f.pages > 1 ? "s" : ""}` : ""}</span></span>` +
      `<button class="btn link" data-rm="${i}" aria-label="Remove ${esc(f.name)}">Remove</button></li>`).join("");
  }
  $("files").onclick = (e) => {
    const i = e.target.dataset.rm;
    if (i != null) { files.splice(+i, 1); renderFiles(); }
  };

  const drop = $("drop");
  drop.onclick = () => $("file").click();
  drop.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("file").click(); } };
  $("file").onchange = async (e) => { await addFiles([...e.target.files]); e.target.value = ""; };
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", async (e) => { await addFiles([...e.dataTransfer.files]); if (files.length && model) analyze(); });
  window.addEventListener("dragover", (e) => { if (e.dataTransfer?.types?.includes("Files")) { e.preventDefault(); setTab("file"); } });
  window.addEventListener("drop", (e) => e.preventDefault());

  // ---------------------------------------------------------------- analysis
  function analyze() {
    if (!model) return;
    const docs = tab === "text"
      ? ($("text").value.trim() ? [{ name: "Your text", text: $("text").value }] : [])
      : files.map((f) => ({ name: f.name, text: f.text }));
    if (!docs.length) {
      setStatus(tab === "text" ? "Paste some text first." : "Add a file first.", true);
      $("results").innerHTML = "";
      return;
    }
    setStatus("");
    const results = docs.map((d) => ({ name: d.name, ...GC.scoreText(d.text, model) }));
    render(results);
    $("results").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  $("analyze").onclick = analyze;
  $("text").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) analyze(); });
  for (const [btn, file] of [["ex-ai", "examples/ai-essay.txt"], ["ex-human", "examples/human-speech.txt"]]) {
    $(btn).onclick = async () => {
      try {
        const r = await fetch(file);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        setTab("text");
        $("text").value = await r.text();
        analyze();
      } catch (e) {
        setStatus(`Could not load the example: ${e.message}`, true);
      }
    };
  }

  // ---------------------------------------------------------------- results
  const KEY = { "Likely AI-written": "high", "Possibly AI-written": "mid", "No clear AI signs": "low", "Too short to judge": "na" };
  const COLOR = { high: "var(--highink)", mid: "var(--midink)", low: "var(--lowink)", na: "var(--ink3)" };
  const EXPLAIN = {
    high: "Strong statistical resemblance to AI-written text. On held-out human documents, only about 1% score this high. Still not proof: read the text and talk to the author before concluding anything.",
    mid: "Some resemblance to AI-written text. About 5% of human documents score this high, especially formal or heavily edited ones, so treat this as a weak signal.",
    low: "No strong resemblance to AI-written text. This does not prove a person wrote it: text from newer AI models, or AI text that has been edited or written in a personal or informal voice, often scores low.",
    na: "Not enough text for a meaningful score. Paste at least 80 words; 250 or more gives a more reliable result.",
  };
  const pill = (verdict) => `<span class="pill ${KEY[verdict]}">${esc(verdict)}</span>`;

  function dial(score, key) {
    const r = 56, c = 2 * Math.PI * r, v = score == null ? 0 : score;
    return `<div class="dial" role="img" aria-label="AI-likelihood ${score == null ? "not available" : Math.round(100 * score) + " out of 100"}">
      <svg viewBox="0 0 136 136"><circle cx="68" cy="68" r="${r}" fill="none" stroke="var(--track)" stroke-width="12"/>
      <circle cx="68" cy="68" r="${r}" fill="none" stroke="${COLOR[key]}" stroke-width="12" stroke-linecap="round"
        stroke-dasharray="${(c * v).toFixed(1)} ${c.toFixed(1)}"/></svg>
      <div class="val"><span class="num">${score == null ? "–" : Math.round(100 * score)}</span><span class="of">out of 100</span></div></div>`;
  }

  function scale(score) {
    const p = 100 * model.meta.threshold_possible, l = 100 * model.meta.threshold_likely;
    return `<div class="scale" aria-hidden="true">
        <span class="zone z1" style="width:${p}%"></span><span class="zone z2" style="left:${p}%;width:${l - p}%"></span>
        <span class="zone z3" style="left:${l}%"></span><span class="marker" style="left:${Math.min(100, 100 * score)}%"></span></div>
      <div class="scale-labels"><span style="left:${p / 2}%"><i class="long">No clear signs</i><i class="short">None</i></span>
        <span style="left:${(p + l) / 2}%"><i class="long">Possibly (${Math.round(p)}+)</i><i class="short">${Math.round(p)}+</i></span>
        <span style="left:${(l + 100) / 2}%"><i class="long">Likely AI (${Math.round(l)}+)</i><i class="short">Likely ${Math.round(l)}+</i></span></div>`;
  }

  function docCard(r, i) {
    const key = KEY[r.verdict];
    let html = `<div class="card" id="doc-${i}"><div class="hero">${dial(r.score, key)}<div>
      <p class="muted small" style="margin:0">${esc(r.name)} · ${r.words} words${r.sections ? ` · ${r.sections} section${r.sections > 1 ? "s" : ""}` : ""}</p>
      <p class="verdict">${esc(r.verdict)}</p>
      <p class="reason">${esc(r.reason)}.</p></div></div>`;
    if (r.score != null) {
      html += scale(r.score);
      if (r.sections > 1) {
        html += `<div class="sections">${r.section_scores.map((s, j) =>
          `<div class="sec"><span>Section ${j + 1}</span><div class="bar"><i style="width:${100 * s}%"></i></div><span class="v">${Math.round(100 * s)}</span></div>`).join("")}</div>`;
      }
      html += `<details class="more"><summary>Show the text of each section</summary><ol class="sectext">${r.chunks.map((c, j) =>
        `<li><b>Section ${j + 1} · ${Math.round(100 * r.section_scores[j])}/100</b>${esc(c)}</li>`).join("")}</ol></details>`;
    }
    html += `<p class="hint">${EXPLAIN[key]}</p></div>`;
    return html;
  }

  function render(results) {
    let html = "";
    if (results.length > 1) {
      const order = results.map((r, i) => [r, i]).sort((a, b) => (b[0].score ?? -1) - (a[0].score ?? -1));
      html += `<div class="card"><h2>${results.length} documents compared</h2><div class="scroll"><table>
        <thead><tr><th>Document</th><th class="num">AI-likelihood</th><th>Verdict</th><th class="num">Words</th></tr></thead><tbody>
        ${order.map(([r, i]) => `<tr><td><a href="#doc-${i}">${esc(r.name)}</a></td><td class="num">${r.score == null ? "–" : Math.round(100 * r.score)}</td>
          <td>${pill(r.verdict)}</td><td class="num">${r.words}</td></tr>`).join("")}
        </tbody></table></div></div>`;
    }
    html += results.map(docCard).join("");
    $("results").innerHTML = html;
  }

  // ---------------------------------------------------------------- accuracy panel (from accuracy.json)
  const NAMES = {
    "human-brown-1961 (published prose)": "Published prose from 1961 (Brown corpus)",
    "human-lang8 (non-native learners)": "Learner writing, non-native (Lang-8)",
    "human-toefl91 (non-native)": "TOEFL essays, non-native (TOEFL-91)",
    "ai-argugpt (gpt4)": "GPT-4 essays",
    "ai-argugpt (claude-instant)": "Claude-instant essays",
    "ai-undetectable (humanised AI)": "AI text run through a \"humaniser\"",
    "ai-claude-samples (essays/emails, short)": "Short Claude-written essays and emails",
    "ai-argugpt (flan-t5-11b)": "Flan-T5 essays (small open model)",
    "ai-argugpt (bloomz-7b)": "BLOOMZ essays (small open model)",
  };

  function renderAccuracy(a) {
    const t = a.test.at_likely, tp = a.test.at_possible;
    const ood = Object.entries(a.ood_by_source);
    const row = ([k, v]) => `<tr><td>${esc(NAMES[k] || k)}</td><td class="num">${v.docs}</td><td class="num">${pct(v.flag_rate_likely, 1)}</td><td class="num">${pct(v.flag_rate_possible, 1)}</td></tr>`;
    const s = a.sanity;
    $("accuracy-body").innerHTML = `
      <p><b>No AI detector is 100% accurate</b>, and any tool that claims to be is not telling the truth. These are the measured results
        for the model this page uses. The numbers come straight from the evaluation files, so they always match the model.</p>
      <table class="acc"><thead><tr><th>Held-out test documents (never trained on)</th><th class="num">Result</th></tr></thead><tbody>
        <tr><td>Documents (human / AI)</td><td class="num">${t.n_human.toLocaleString()} / ${t.n_ai.toLocaleString()}</td></tr>
        <tr><td>Accuracy at “Likely AI”</td><td class="num">${pct(t.accuracy, 1)}</td></tr>
        <tr><td>Human documents wrongly called “Likely AI”</td><td class="num">${pct(t.false_positive_rate, 2)}</td></tr>
        <tr><td>Human documents getting “Possibly” or higher</td><td class="num">${pct(tp.false_positive_rate, 1)}</td></tr>
        <tr><td>AI documents missed (below “Likely AI”)</td><td class="num">${pct(t.false_negative_rate, 2)}</td></tr>
      </tbody></table>
      <p>Those documents come from the same kinds of sources as the training data. The table below is a better guide to real use:
        sources and AI models the classifier never saw during training.</p>
      <table class="acc"><thead><tr><th>Never-seen source</th><th class="num">Docs</th><th class="num">“Likely AI”</th><th class="num">“Possibly” or higher</th></tr></thead><tbody>
        <tr><th colspan="4" class="grp">Human writing (every flag is a false alarm)</th></tr>
        ${ood.filter(([, v]) => v.label === 0).map(row).join("")}
        <tr><th colspan="4" class="grp">AI writing (share caught)</th></tr>
        ${ood.filter(([, v]) => v.label === 1).sort((x, y) => y[1].flag_rate_likely - x[1].flag_rate_likely).map(row).join("")}
      </tbody></table>
      ${s ? `<p><b>Real-world check:</b> ${s.documents} assignment-style PDFs (${s.human} human, ${s.ai} written by a current AI model)
        got <b>${s.correct}/${s.documents}</b> right. ${s.human_flagged} human document${s.human_flagged === 1 ? " was" : "s were"} wrongly flagged,
        and ${s.ai_missed} AI document${s.ai_missed === 1 ? " was" : "s were"} missed.</p>` : ""}
      <ul>
        <li><b>Weakest on newer AI models</b> and on AI text written in a personal, informal or non-native voice. It learned mostly from older GPT and Claude text.</li>
        <li><b>“No clear AI signs” is not proof a human wrote something.</b> Edited or paraphrased AI text often passes.</li>
        <li><b>Short texts are unreliable.</b> Under 80 words there is no score, and under ~250 words confidence is lower.</li>
        <li><b>For formal English writing only</b> (essays, reports, assignments, articles), not chats, poems or other languages.</li>
      </ul>
      <p class="muted small">Data, method and code: <a href="https://github.com/shuyash411-create/ghost-check/blob/main/train/RESULTS.md">train/RESULTS.md</a>.</p>`;
  }

  fetch("model/accuracy.json").then((r) => r.json()).then(renderAccuracy)
    .catch(() => { $("accuracy-body").innerHTML = `<p class="muted">Accuracy figures could not be loaded. See train/RESULTS.md in the repository.</p>`; });
  $("acc-link").onclick = () => { $("accuracy").open = true; };
})();
