/* ghost-check web UI: reads input, runs GhostCheck (ghost-check.js), renders results. */
(function () {
  "use strict";
  const GC = window.GhostCheck;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v, d = 0) => (v == null || Number.isNaN(v) ? "–" : v.toFixed(d));

  // ---------------------------------------------------------------- examples
  const EXAMPLES = {
    chat: `12/03/2024, 9:02 am - Riya created group "Weekend trip"
12/03/2024, 9:05 am - Riya: guys are we still on for saturday??
12/03/2024, 9:06 am - Arjun: yes!! i already asked my mom for the car lol
12/03/2024, 9:07 am - Meera: Absolutely! I've put together a quick plan for the trip. First, we should leave by 7 AM to avoid the traffic. Additionally, it's worth noting that the weather forecast looks clear for the entire weekend. Let me know if you have any questions or suggestions.
12/03/2024, 9:08 am - Riya: omg meera thats so organised 😂
12/03/2024, 9:08 am - Riya: ok 7 is early but fine i guess
12/03/2024, 9:11 am - Arjun: wait who is bringing snacks. i can get chips but not the drinks, my bag is already full with the speaker and stuff
12/03/2024, 9:14 am - Meera: Great question! Here's a suggested breakdown of responsibilities:
- Arjun: snacks and the speaker
- Riya: drinks and the first-aid kit
- Me: navigation and accommodation bookings
This ensures that everyone contributes equally and nothing important is overlooked. Feel free to suggest any changes.
12/03/2024, 9:15 am - Riya: fine i'll get drinks. coke and some juice? or should i also get water, it gets super hot there in the afternoon and last time we ran out
12/03/2024, 9:20 am - Arjun: haha meera did you chatgpt that
12/03/2024, 9:21 am - Meera: Not at all — I simply wanted to make sure the planning process is as seamless as possible for everyone involved. Ultimately, a well-organized trip leads to a more enjoyable experience. Let me know if you have any questions or suggestions.
12/03/2024, 9:22 am - Arjun: sure sure 😂😂 anyway i'll pick everyone up. riya first then meera. dont be late this time!!!
12/03/2024, 9:24 am - Riya: i was late ONE time. and it was because of the metro
not my fault
12/03/2024, 9:30 am - Meera: That's completely understandable. Public transportation delays can be unpredictable and frustrating. To avoid any issues on Saturday, I recommend setting two alarms and preparing your bag the night before.`,
    ai: `In today's fast-paced world, effective communication plays a crucial role in organizational success. It is important to note that clear messaging fosters collaboration and builds trust across teams. Additionally, organizations that leverage modern tools can streamline their workflows and elevate their overall performance.

Furthermore, leaders should embrace a comprehensive approach to feedback. This ensures that every voice is heard and valued. Ultimately, a culture of openness empowers employees to unlock their full potential and navigate challenges with confidence.`,
    human: `So I tried the new bus route today. Terrible idea. It took forty minutes, the driver missed my stop, and I had to walk back in the rain with a broken umbrella.

Honestly? Never again. Tomorrow I'm cycling, even if it means getting up at six. My legs will hate me but whatever, at least I'll be on time for once. Also I found a tiny cafe near the station that does amazing chai, so the day wasn't a total loss.`,
  };

  // ---------------------------------------------------------------- state
  let tab = "text";
  let files = []; // [{name, pages: [text], kind}]
  let last = null; // {records, modes}

  // ---------------------------------------------------------------- tabs
  function setTab(t) {
    tab = t;
    for (const [id, name] of [["text", "text"], ["file", "file"]]) {
      $(`tab-${id}`).setAttribute("aria-selected", String(name === t));
      $(`panel-${id}`).hidden = name !== t;
    }
  }
  $("tab-text").onclick = () => setTab("text");
  $("tab-file").onclick = () => setTab("file");

  // ---------------------------------------------------------------- file reading
  function setStatus(msg, error = false) {
    $("status").textContent = msg;
    $("status").classList.toggle("error", error);
  }

  function decode(buf) {
    const b = new Uint8Array(buf);
    if (b[0] === 0xff && b[1] === 0xfe) return new TextDecoder("utf-16le").decode(b);
    if (b[0] === 0xfe && b[1] === 0xff) return new TextDecoder("utf-16be").decode(b);
    try {
      return new TextDecoder("utf-8", { fatal: true }).decode(b).replace(/^\ufeff/, "");
    } catch {
      return new TextDecoder("windows-1252").decode(b);
    }
  }

  const scripts = {};
  function loadScript(src) {
    return (scripts[src] ||= new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = res;
      s.onerror = () => rej(new Error(`Could not load ${src}`));
      document.head.appendChild(s);
    }));
  }

  async function readPdf(buf) {
    await loadScript("vendor/pdf.min.js");
    const pdfjs = window.pdfjsLib;
    pdfjs.GlobalWorkerOptions.workerSrc = "vendor/pdf.worker.min.js";
    const pdf = await pdfjs.getDocument({ data: buf }).promise;
    const pages = [];
    for (let p = 1; p <= pdf.numPages; p++) {
      const page = await pdf.getPage(p);
      const content = await page.getTextContent();
      let text = "", lastY = null, lastH = 0, lineStart = true;
      for (const it of content.items) {
        if (!("str" in it)) continue;
        const y = it.transform[5];
        const h = Math.abs(it.transform[3]) || it.height || 10;
        // A vertical gap much larger than the font size starts a new paragraph.
        if (lineStart && lastY !== null && lastY - y > 1.8 * Math.max(h, lastH)) text += "\n";
        text += it.str;
        if (it.str.trim()) { lastY = y; lastH = h; lineStart = false; }
        if (it.hasEOL) { text += "\n"; lineStart = true; }
      }
      pages.push(text);
    }
    if (!pages.some((t) => t.trim())) {
      throw new Error("This PDF has no text layer (it's probably a scan). Run it through OCR first.");
    }
    return pages;
  }

  async function readZip(buf) {
    await loadScript("vendor/jszip.min.js");
    const zip = await window.JSZip.loadAsync(buf);
    const entry = Object.values(zip.files).find((f) => !f.dir && /\.txt$/i.test(f.name));
    if (!entry) throw new Error("No .txt chat file inside the zip.");
    return [decode(await entry.async("arraybuffer"))];
  }

  async function addFiles(list) {
    for (const f of list) {
      setStatus(`Reading ${f.name}…`);
      try {
        const buf = await f.arrayBuffer();
        const ext = f.name.toLowerCase().split(".").pop();
        let pages, kind;
        if (ext === "pdf" || f.type === "application/pdf") [pages, kind] = [await readPdf(buf), "PDF"];
        else if (ext === "zip") [pages, kind] = [await readZip(buf), "ZIP"];
        else [pages, kind] = [[decode(buf)], "Text"];
        files.push({ name: f.name, pages, kind });
        setStatus("");
      } catch (e) {
        setStatus(`${f.name}: ${e.message}`, true);
      }
    }
    renderFiles();
  }

  function renderFiles() {
    $("files").innerHTML = files.map((f, i) =>
      `<li><span>${esc(f.name)} <span class="muted small">· ${f.kind}${f.pages.length > 1 ? `, ${f.pages.length} pages` : ""}</span></span>` +
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
  drop.addEventListener("drop", async (e) => { await addFiles([...e.dataTransfer.files]); runIfReady(); });
  // Dropping a file anywhere on the page switches to the upload tab.
  window.addEventListener("dragover", (e) => { if (e.dataTransfer?.types?.includes("Files")) { e.preventDefault(); setTab("file"); } });
  window.addEventListener("drop", (e) => e.preventDefault());

  // ---------------------------------------------------------------- analysis
  const opts = () => ({
    threshold: +$("threshold").value,
    minWords: Math.max(1, +$("minwords").value || 6),
    mode: $("mode").value,
    byPage: $("bypage").checked,
  });

  function collect() {
    const o = opts();
    const out = [], modes = [];
    const sources = tab === "text"
      ? ($("text").value.trim() ? [{ name: "Pasted text", pages: [$("text").value], label: "Your text" }] : [])
      : files;
    for (const s of sources) {
      const { records, mode } = GC.load(s.pages, s.name, { mode: o.mode, byPage: o.byPage, label: s.label });
      out.push(...records);
      modes.push({ name: s.name, mode, n: records.length, senders: new Set(records.map((r) => r.sender)).size });
    }
    return { records: out, modes };
  }

  function analyze() {
    const { records, modes } = collect();
    if (!modes.length) {
      setStatus(tab === "text" ? "Paste some text first." : "Add a file first.", true);
      $("results").innerHTML = "";
      return;
    }
    last = { records, modes };
    render();
    $("results").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function runIfReady() { if (files.length) analyze(); }

  $("analyze").onclick = analyze;
  $("text").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) analyze(); });
  for (const [btn, key] of [["ex-chat", "chat"], ["ex-ai", "ai"], ["ex-human", "human"]]) {
    $(btn).onclick = () => { setTab("text"); $("text").value = EXAMPLES[key]; analyze(); };
  }
  $("threshold").oninput = () => { $("threshold-v").textContent = $("threshold").value; if (last) render(); };
  for (const id of ["minwords", "mode", "bypage"]) $(id).onchange = () => { if (last) analyze(); };

  // ---------------------------------------------------------------- rendering
  const band = (s, t) => (s == null || Number.isNaN(s) ? "na" : s >= t ? "high" : s >= t - 15 ? "mid" : "low");
  const verdict = (s, t) => ({ high: "Likely AI-written", mid: "Mixed signals", low: "Likely human-written", na: "Not enough text" }[band(s, t)]);
  const pill = (s, t, text) => `<span class="pill ${band(s, t)}">${text ?? fmt(s)}</span>`;
  const bar = (v) => `<div class="bar"><i style="width:${Number.isNaN(v) ? 0 : Math.max(0, Math.min(100, v))}%"></i></div>`;
  const anchor = (i) => `sender-${i}`;

  function dial(score, t) {
    const r = 62, c = 2 * Math.PI * r, v = Number.isNaN(score) ? 0 : score / 100;
    const col = { high: "var(--highink)", mid: "var(--midink)", low: "var(--lowink)", na: "var(--ink3)" }[band(score, t)];
    return `<div class="dial" role="img" aria-label="AI-likely score ${fmt(score)} out of 100">
      <svg viewBox="0 0 148 148"><circle cx="74" cy="74" r="${r}" fill="none" stroke="var(--track)" stroke-width="12"/>
      <circle cx="74" cy="74" r="${r}" fill="none" stroke="${col}" stroke-width="12" stroke-linecap="round"
        stroke-dasharray="${(c * v).toFixed(1)} ${c.toFixed(1)}"/></svg>
      <div class="val"><span class="num">${fmt(score)}</span><span class="of">out of 100</span></div></div>`;
  }

  function features(row) {
    return `<div class="feat">${Object.keys(GC.WEIGHTS).map((k) =>
      `<div class="f"><div class="name">${GC.FEATURE_LABELS[k]}<small>${GC.FEATURE_HELP[k]}</small></div>${bar(100 * row[k])}<div class="v">${fmt(100 * row[k])}</div></div>`).join("")}</div>`;
  }

  function messageList(recs, t, isChat, limit) {
    const shown = recs.slice(0, limit);
    const items = shown.map((r) => {
      const ts = r.timestamp ? r.timestamp.toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "";
      const meta = Object.keys(GC.WEIGHTS).map((k) => `<span>${GC.FEATURE_LABELS[k]} <b>${fmt(100 * r[k])}</b></span>`).join("");
      return `<li class="msg">${pill(r.score, t)}<div><div class="text">${esc(r.message)}</div>
        <div class="meta">${ts ? `<span>${esc(ts)}</span>` : ""}<span>${r.words} words</span>${meta}</div></div></li>`;
    });
    return items.join("");
  }

  function render() {
    const t = opts().threshold;
    const { records, modes } = last;
    GC.scoreRecords(records, opts().minWords);
    const summary = GC.summarize(records, t);
    const scored = records.filter((r) => r.scored);
    const isChat = modes.some((m) => m.mode === "chat");
    const unit = isChat ? "messages" : "passages";

    const detected = modes.map((m) => m.mode === "chat"
      ? `${esc(m.name)}: WhatsApp chat, ${m.n} messages from ${m.senders} ${m.senders === 1 ? "person" : "people"}`
      : `${esc(m.name)}: document, ${m.n} ${m.n === 1 ? "passage" : "passages"}`).join(" · ");
    setStatus("");

    if (!scored.length) {
      $("results").innerHTML = `<div class="card"><h2>Not enough text</h2><p class="muted">Nothing was long enough to score.
        Each ${unit.slice(0, -1)} needs at least ${opts().minWords} words. Add more text, or lower the limit under Options.</p>
        <p class="note">${detected}</p></div>`;
      return;
    }

    let html = "";
    if (summary.length === 1) {
      const s = summary[0];
      html += `<div class="card"><div class="hero">${dial(s.avg_score, t)}<div>
        <p class="muted small" style="margin:0">AI-likely score</p>
        <p class="verdict">${verdict(s.avg_score, t)}</p>
        <div class="stats"><span><b>${fmt(s.ai_likely_pct)}%</b> of ${unit} score ≥ ${t}</span>
          <span><b>${s.messages_scored}</b> ${unit} scored${s.messages_total > s.messages_scored ? ` (${s.messages_total - s.messages_scored} too short)` : ""}</span>
          <span><b>${fmt(s.avg_words)}</b> words each on average</span></div>
        </div></div>
        <h2 style="margin-top:24px">What drove the score</h2>${features(s)}
        <p class="note">${detected}</p></div>`;
    } else {
      html += `<div class="card"><h2>${isChat ? "Who writes most like AI" : "Which part reads most like AI"}</h2>
        <p class="muted small" style="margin-top:-6px">Share of each ${isChat ? "person's" : "section's"} ${unit} scoring ≥ ${t}. Click a name for details.</p>
        <div class="chart">${summary.map((s, i) => `<a class="c" href="#${anchor(i)}" title="${esc(s.sender)}: ${fmt(s.ai_likely_pct)}% AI-likely, average score ${fmt(s.avg_score)}, ${s.messages_scored} ${unit} scored">
          <span class="who">${esc(s.sender)}</span>${bar(s.ai_likely_pct)}
          <span class="v"><b>${fmt(s.ai_likely_pct)}%</b> · avg ${fmt(s.avg_score)}</span></a>`).join("")}</div>
        <div class="scroll" style="margin-top:20px"><table><thead><tr><th>${isChat ? "Person" : "Section"}</th><th class="num">AI-likely</th><th class="num">Avg score</th><th class="num">Scored / total</th>
          ${Object.keys(GC.WEIGHTS).map((k) => `<th class="num">${GC.FEATURE_LABELS[k]}</th>`).join("")}</tr></thead><tbody>
          ${summary.map((s, i) => `<tr><td><a href="#${anchor(i)}">${esc(s.sender)}</a></td><td class="num">${pill(s.avg_score, t, `${fmt(s.ai_likely_pct)}%`)}</td>
            <td class="num">${fmt(s.avg_score)}</td><td class="num">${s.messages_scored} / ${s.messages_total}</td>
            ${Object.keys(GC.WEIGHTS).map((k) => `<td class="num">${fmt(100 * s[k])}</td>`).join("")}</tr>`).join("")}
        </tbody></table></div>
        <p class="note">${detected}. Feature columns are 0–100, higher = more AI-like.</p></div>`;
    }

    // Per sender: messages, highest score first.
    const LIMIT = 20;
    html += `<div class="card"><h2>${summary.length === 1 ? `Scored ${unit}, highest first` : (isChat ? `Messages by person` : `Passages by section`)}</h2>`;
    summary.forEach((s, i) => {
      const recs = scored.filter((r) => r.sender === s.sender).sort((a, b) => b.score - a.score);
      if (summary.length === 1) {
        html += `<ul class="msgs" data-sender="${i}">${messageList(recs, t, isChat, LIMIT)}</ul>`;
      } else {
        html += `<details class="sender" id="${anchor(i)}" ${i === 0 ? "open" : ""}><summary><h3>${esc(s.sender)}</h3>
          ${pill(s.avg_score, t, `${fmt(s.ai_likely_pct)}% AI-likely`)}<span class="muted small">avg ${fmt(s.avg_score)} · ${s.messages_scored} scored</span></summary>
          <ul class="msgs" data-sender="${i}">${messageList(recs, t, isChat, LIMIT)}</ul></details>`;
      }
      if (recs.length > LIMIT) html += `<button class="btn link" data-more="${i}">Show all ${recs.length}</button>`;
    });
    html += `</div>`;
    $("results").innerHTML = html;

    $("results").querySelectorAll("[data-more]").forEach((btn) => {
      btn.onclick = () => {
        const i = +btn.dataset.more, s = summary[i];
        const recs = scored.filter((r) => r.sender === s.sender).sort((a, b) => b.score - a.score);
        $("results").querySelector(`ul[data-sender="${i}"]`).innerHTML = messageList(recs, t, isChat, Infinity);
        btn.remove();
      };
    });
    $("results").querySelectorAll('a[href^="#sender-"]').forEach((a) => {
      a.onclick = () => { const d = document.querySelector(a.getAttribute("href")); if (d) d.open = true; };
    });
  }
})();
