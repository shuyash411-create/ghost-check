# ghost-check

Estimates how likely a **formal document** was written by AI: an essay,
report, assignment or article. Give it a PDF or a text file and it prints an
AI-likelihood score with a one-line reason.

```
$ python ghost_check.py assignment.pdf
assignment.pdf: AI-likelihood 46/100 - Likely AI-written (Likely AI at 40+, Possibly at 21+).
  2 of 2 sections read as AI-like; AI-leaning wording: 'ultimately', 'a society', 'true'.
```

It uses a trained classifier (TF-IDF + logistic regression) trained on about
18,400 human and AI documents. There is also a browser app in [`web/`](web/),
which uses a separate, older style-heuristic engine; see
[web/README.md](web/README.md).

> **Not 100% accurate, and no detector is.** On documents like its training
> data it is 99.5% accurate, and about 1% of human documents are wrongly called
> "Likely AI". On AI text from newer models it is much weaker: in a 20-document
> real-world check it got **15/20** right, missing 4 of 10 AI documents written
> by a current model and wrongly flagging 1 of 10 human ones. Treat a score as
> a reason to look closer, never as proof. Full numbers:
> **[train/RESULTS.md](train/RESULTS.md)**.

## Install

Needs Python 3.10+.

```bash
git clone https://github.com/shuyash411-create/ghost-check.git
cd ghost-check
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt      # scikit-learn (pinned), numpy, scipy, joblib, pypdf
```

The trained model (`model/ghost_check.joblib`, 7 MB) is included. It is a
pickle, so it needs the pinned scikit-learn version, and like any pickle it
should only be loaded from a source you trust.

## Usage

```bash
python ghost_check.py essay.pdf                 # a PDF
python ghost_check.py report.txt notes.md       # text files, several at once
python ghost_check.py essay.pdf --details       # also score every ~200-word section
python ghost_check.py essay.pdf --json          # machine-readable output
```

**How it works:**
1. **Extract.** Text is read from the PDF with pypdf, or read directly from a
   text file. Scanned PDFs have no text layer; run OCR first (e.g. `ocrmypdf`).
2. **Split.** The text is cut into ~200-word sections, and each section gets a
   probability of being AI-written.
3. **Score.** The **AI-likelihood** (0–100) is the average over sections.
4. **Verdict.** The thresholds were set on held-out human documents:

| Verdict | Score | Meaning |
|---|---|---|
| **Likely AI-written** | 40+ | Only ~1% of held-out human documents score this high |
| **Possibly AI-written** | 21–39 | ~5% of human documents score 21+ |
| **No clear AI signs** | below 21 | Not proof a human wrote it: current-model and edited AI text often lands here |
| **Too short to judge** | under 80 words | No score given |

The **reason** says how many sections read as AI-like and names the words or
phrases that pushed the score most, in the model's own terms. These are
statistical cues, not a checklist of "AI words".

## Accuracy at a glance

| Test | Result |
|---|---|
| Held-out test documents (n = 3,947), "Likely AI" threshold | 99.5% accuracy · FPR 0.99% · FNR 0.28% |
| Human writing never seen in training (1961 prose, Lang-8 and TOEFL-91 learners) | 0.3–4.4% wrongly flagged |
| AI from unseen generators: GPT-4 / Claude-instant | 100% / 100% caught |
| AI from unseen small open models: Flan-T5 / BLOOMZ | 36% / 25% caught |
| "Humanised" AI text | 85% caught |
| **Real-world check: 20 assignment-style PDFs** | **15/20 · FPR 1/10 · FNR 4/10** |

FPR = human text wrongly flagged; FNR = AI text missed. Details, per-source
tables and caveats are in [train/RESULTS.md](train/RESULTS.md).

## Training

Everything needed to rebuild the model is in [`train/`](train/).

```bash
pip install -r requirements.txt -r train/requirements.txt

# 1. Get the data (public GitHub repos; Ghostbuster is ~2 GB)
git clone --depth 1 https://github.com/vivek3141/ghostbuster-data.git ../ghostbuster-data
git clone --depth 1 https://github.com/huhailinguist/ArguGPT.git ../ArguGPT
python eval/evaluate.py >/dev/null   # downloads the NLTK corpora (Brown) into eval/.cache/

# 2. (Optional, recommended) generate modern AI samples with the Claude API
export ANTHROPIC_API_KEY=...          # or put it in your environment's settings
python train/generate_claude.py --n 12000 --dry-run    # preview prompts and volume
python train/generate_claude.py --n 12000              # submit a Message Batch (50% price)
python train/generate_claude.py --collect              # wait for it and save the results

# 3. Build the dataset, train, evaluate
python train/prepare_data.py --ghostbuster ../ghostbuster-data --argugpt ../ArguGPT/data/argugpt
python train/train.py                 # writes model/, train/results.json
python train/sanity/run_sanity.py --ghostbuster ../ghostbuster-data   # 20-document real-world PDF check
```

**Datasets used:**

| Dataset | Used for |
|---|---|
| [Ghostbuster](https://github.com/vivek3141/ghostbuster-data) (Verma et al., 2023) | Student essays and Reuters news, each by humans, GPT-3.5 and Claude on the same prompts. Also BAWE (UK university assignments), ETS/TOEFL, PELIC and Lang-8 (non-native writers), and humanised AI text |
| [ArguGPT](https://github.com/huhailinguist/ArguGPT) (Liu et al., 2023) | AI exam essays; GPT-4, Claude-instant, BLOOMZ and Flan-T5 held out as unseen generators |
| Brown corpus (1961, via NLTK) | Never-seen human prose |

Splits are by prompt/article group, so human and AI texts on the same prompt
never appear on both sides of a split. Texts are scored in ~200-word sections,
so length can't give the answer away. `prepare_data.py` documents all of this.

**Not done in this version:**
- **Claude-generated training data (step 2):** there was no API key in the
  build environment. It is the most valuable next step, because current-model
  text is where the model is weakest.
- **HC3 and other Hugging Face datasets:** Hugging Face was unreachable from
  the build environment.
- **Fine-tuned transformer:** it needs pretrained weights from Hugging Face.
  Only the TF-IDF + logistic regression baseline was trained.

## Repository layout

| Path | What |
|---|---|
| `ghost_check.py` | The CLI: PDF/text extraction, sectioning, scoring, reasoning |
| `model/ghost_check.joblib` | Trained model and thresholds |
| `train/` | Data preparation, Claude generation, training, results, real-world sanity check |
| `web/` | Browser app, using the style-heuristic engine (`web/heuristic.py` is its Python reference) |
| `eval/`, `tests/` | Calibration and parity tests for the web app's heuristics |

## Limitations

- **Weak on current models and on AI text written in a personal, informal or
  non-native voice.** See [train/RESULTS.md](train/RESULTS.md), section 3.
- **Short texts are unreliable.** Under 80 words gets no score, and under ~250
  words is lower confidence.
- **English only**, and formal writing only. Chats, emails and social posts
  are out of scope for this model.
- **Edited, paraphrased or "humanised" AI text can evade it.**
- **Never use the score alone to accuse someone.**
