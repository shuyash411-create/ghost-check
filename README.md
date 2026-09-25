# ghost-check

Estimates how likely a **formal document** was written by AI: an essay,
report, assignment or article. It comes in two forms that share one trained
model and give identical scores:

- **Web app** ([`web/`](web/)): paste text or drop PDFs in the browser. It runs
  entirely on your device, and nothing is uploaded.
- **CLI** (`ghost_check.py`): score PDFs or text files from the terminal.

```
$ python ghost_check.py essay.pdf
essay.pdf: AI-likelihood 66/100 - Likely AI-written (Likely AI at 40+, Possibly at 18+).
  2 of 2 sections read as AI-like; AI-leaning wording: 'ultimately', 'a society', 'portrayed as'.
```

The model is a TF-IDF + logistic regression classifier trained on about
18,500 human and AI documents.

> **Not 100% accurate, and no detector is.** On documents like its training
> data it is 99.3% accurate, and about 1% of human documents are wrongly called
> "Likely AI". It is much weaker on AI text from newer models: in a 21-document
> real-world check it got **15/21**. None of the 10 human documents was wrongly
> flagged, but it missed 6 of the 11 AI documents written by a current model,
> including a real cited economics report a user submitted after a false negative.
> Treat a score as a reason to look closer, never as proof. Full numbers:
> **[train/RESULTS.md](train/RESULTS.md)**.

## Web app

Open the deployed site, or run it locally:

```bash
cd web && python3 -m http.server 8000     # then open http://localhost:8000
```

1. **Paste text**, or switch to **Upload files** and drop PDFs, `.txt` or
   `.md` files. You can add several to compare them.
2. Click **Check for AI**.

For each document you get:
- a 0–100 score, the verdict and the one-line reason,
- where the score sits against the thresholds,
- a bar per ~200-word section, with the text of each section.

The **How accurate is this?** panel shows the measured error rates. It is
generated from the evaluation results, so it always matches the model.

On first load the page downloads the model (~2.2 MB compressed). PDFs are read
with [pdf.js](https://mozilla.github.io/pdf.js/), vendored in `web/vendor/`
with its license. `netlify.toml` publishes `web/` as a static site with no
build step; any static host works.

## CLI

Needs Python 3.10+.

```bash
git clone https://github.com/shuyash411-create/ghost-check.git
cd ghost-check
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt      # scikit-learn (pinned), numpy, scipy, joblib, pypdf

python ghost_check.py essay.pdf                 # a PDF
python ghost_check.py report.txt notes.md       # text files, several at once
python ghost_check.py essay.pdf --details       # also score every ~200-word section
python ghost_check.py essay.pdf --json          # machine-readable output
```

The trained model (`model/ghost_check.joblib`, 7 MB) is included. It is a
pickle, so it needs the pinned scikit-learn version, and like any pickle it
should only be loaded from a source you trust. Scanned PDFs have no text
layer; run OCR first (e.g. `ocrmypdf`).

## How scoring works

1. **Clean up.** Typography is flattened: curly quotes and dashes become
   plain ones.
2. **Split.** The text is cut into ~200-word sections at sentence ends. PDF
   line breaks are ignored, so a PDF scores the same as its text.
3. **Score each section.** Every section gets a probability of being
   AI-written.
4. **Combine.** The **AI-likelihood** (0–100) is the word-weighted average
   over sections.
5. **Verdict.** The thresholds were set on held-out *human* documents:

| Verdict | Score | Meaning |
|---|---|---|
| **Likely AI-written** | 40+ | Only ~1% of held-out human documents score this high |
| **Possibly AI-written** | 18–39 | ~5% of human documents score 18+ |
| **No clear AI signs** | below 18 | Not proof a human wrote it: current-model and edited AI text often lands here |
| **Too short to judge** | under 80 words | No score given |

The **reason** says how many sections read as AI-like and names the words or
phrases that pushed the score most, in the model's own terms. These are
statistical cues, not a checklist of "AI words".

## Accuracy at a glance

| Test | Result |
|---|---|
| Held-out test documents (n = 3,939), "Likely AI" threshold | 99.3% accuracy · FPR 1.06% · FNR 0.44% |
| Human writing never seen in training (1961 prose, Lang-8 and TOEFL-91 learners) | 0.0–4.4% wrongly flagged |
| AI from unseen generators: GPT-4 / Claude-instant | 99% / 100% caught |
| "Humanised" AI text | 85% caught |
| AI from unseen small open models: Flan-T5 / BLOOMZ | 37% / 24% caught |
| **Real-world check: 21 assignment-style PDFs** | **15/21 · FPR 0/10 · FNR 6/11** |

FPR = human text wrongly flagged; FNR = AI text missed. Details, per-source
tables, version history and caveats are in [train/RESULTS.md](train/RESULTS.md).

## Training

Everything needed to rebuild the model is in [`train/`](train/).

```bash
pip install -r requirements.txt -r train/requirements.txt

# 1. Get the data (public GitHub repos; Ghostbuster is ~2 GB)
git clone --depth 1 https://github.com/vivek3141/ghostbuster-data.git ../ghostbuster-data
git clone --depth 1 https://github.com/huhailinguist/ArguGPT.git ../ArguGPT

# 2. (Optional, recommended) generate modern AI samples with the Claude API
export ANTHROPIC_API_KEY=...          # or put it in your environment's settings
python train/generate_claude.py --n 12000 --dry-run    # preview prompts and volume
python train/generate_claude.py --n 12000              # submit a Message Batch (50% price)
python train/generate_claude.py --collect              # wait for it and save the results

# 3. Build the dataset, train, evaluate, export to the web app
python train/prepare_data.py --ghostbuster ../ghostbuster-data --argugpt ../ArguGPT/data/argugpt
python train/train.py                 # writes model/ and train/results.json
python train/sanity/run_sanity.py --ghostbuster ../ghostbuster-data   # 20-PDF real-world check
python train/export_web.py            # writes web/model/ (weights + accuracy panel data)
python tests/check_parity.py          # web app and CLI must agree (needs Node.js)
```

**Datasets used:**

| Dataset | Used for |
|---|---|
| [Ghostbuster](https://github.com/vivek3141/ghostbuster-data) (Verma et al., 2023) | Student essays and Reuters news, each by humans, GPT-3.5 and Claude on the same prompts. Also BAWE (UK university assignments), ETS/TOEFL, PELIC and Lang-8 (non-native writers), and humanised AI text |
| [ArguGPT](https://github.com/huhailinguist/ArguGPT) (Liu et al., 2023) | AI exam essays; GPT-4, Claude-instant, BLOOMZ and Flan-T5 held out as unseen generators |
| Brown corpus (1961, via NLTK data) | Never-seen human prose (downloaded automatically) |

Splits are by prompt/article group, so human and AI texts on the same prompt
never appear on both sides of a split. The data preparation is deterministic:
rebuilding it gives an identical dataset.

**Not done in this version:**
- **Claude-generated training data:** there was no API key in the build
  environment. It is the most valuable next step, because current-model text
  is where the model is weakest.
- **HC3 and other Hugging Face datasets:** Hugging Face was unreachable from
  the build environment.
- **Fine-tuned transformer:** it needs pretrained weights from Hugging Face.
  Only the TF-IDF + logistic regression baseline was trained.

## Repository layout

| Path | What |
|---|---|
| `ghost_check.py` | The CLI: PDF/text extraction, sectioning, scoring, reasoning |
| `model/ghost_check.joblib` | Trained model and thresholds |
| `web/` | Browser app: `ghost-check.js` (the same scoring in JavaScript), `app.js` (UI), `model/` (exported weights and accuracy data) |
| `train/` | Data preparation, Claude generation, training, export, results, real-world sanity check |
| `tests/check_parity.py` | Checks the web app and the CLI give identical n-grams, scores, verdicts and reasons |

## Limitations

- **Weak on current models and on AI text written in a personal, informal,
  non-native, or long/factual/cited-report voice.** See
  [train/RESULTS.md](train/RESULTS.md), section 3.
- **Short texts are unreliable.** Under 80 words gets no score, and under ~250
  words is lower confidence.
- **English only**, and formal writing only. Chats, emails and social posts
  are out of scope.
- **Edited, paraphrased or "humanised" AI text can evade it.**
- **Never use the score alone to accuse someone.**
