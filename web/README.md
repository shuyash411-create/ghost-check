# ghost-check web app (style heuristics)

> This documents the **web app** in this folder and its Python reference
> implementation, `web/heuristic.py`. They use calibrated writing-style
> heuristics and support WhatsApp chats. The main **command-line tool**
> (`../ghost_check.py`) uses a trained classifier for formal documents instead;
> see the [top-level README](../README.md).

Estimates how "AI-written" text looks. Paste any text, or give it a
**WhatsApp chat export** or a **PDF**, and it scores each message or passage
0–100. Chats are compared person by person.

It comes in two forms that share the same scoring:

- **Web app** (`web/`): paste or drop files in the browser and see the result
  immediately. Everything runs locally in the browser, and nothing is uploaded.
- **Python CLI** (`web/heuristic.py`): scores files from the terminal and writes a
  self-contained HTML report, with an optional CSV.

> **No AI detector is 100% accurate, and this one doesn't claim to be.**
> ghost-check is tuned to be careful. On human writing from before AI chatbots
> existed, about **1%** of texts are wrongly called "Likely AI-written", and **none**
> of 970 human writers judged on 10 texts each were. The flip side is that it
> misses a lot of AI text: about half of typical AI answers and essays get
> "Likely AI", and AI told to write casually usually passes. A high score is a
> reason to look closer, never proof. See [Accuracy](#accuracy).

## Web app

Open the deployed site, or run it locally:

```bash
cd web && python3 -m http.server 8000   # then open http://localhost:8000
```

1. **Paste text** (an essay, an email, or a copied WhatsApp chat), or switch to
   **Upload files** and drop a WhatsApp `.txt`/`.zip` export, a PDF, or any
   `.txt`/`.md` file. You can add several files and compare them.
2. Click **Check for AI**.
3. For a single text you get an overall score, a verdict (Likely AI-written /
   Some AI signs / No clear AI signs / Too short to judge), and a breakdown of
   which signals drove it. For a chat or several
   files you get a chart comparing people, plus each person's messages sorted
   by score.

**Options** lets you force chat or document mode, or score each PDF page
separately. The verdict thresholds are fixed because they are calibrated (see
[How the score works](#how-the-score-works)). The app's **How accurate is
this?** panel shows the measured error rates.

Files are read in your browser: PDFs with [pdf.js](https://mozilla.github.io/pdf.js/),
zips with [JSZip](https://stuk.github.io/jszip/). Both are vendored in
`web/vendor/` with their licenses. Scanned PDFs have no text layer, so they need OCR first.

**Deploying:** `netlify.toml` publishes `web/` as a static site with no build
step. Any static host works.

## Install (CLI)

Needs Python 3.9+.

```bash
git clone https://github.com/shuyash411-create/ghost-check.git
cd ghost-check
python3 -m venv .venv && source .venv/bin/activate
pip install -r eval/requirements.txt     # pandas, matplotlib, pypdf, ...
```

`pypdf` is only needed for PDF input.

## Export a WhatsApp chat

- **Android:** open the chat → ⋮ → More → Export chat → **Without media**
- **iPhone:** open the chat → tap the name → Export Chat → **Without Media**

You get a `.txt` file, or a `.zip` with `_chat.txt` inside. ghost-check reads either.

## Usage

```bash
# WhatsApp chat → report
python web/heuristic.py "WhatsApp Chat with Team.txt"

# The .zip WhatsApp produces works too
python web/heuristic.py chat.zip -o team-report.html

# A PDF: an essay, an assignment, or a chat saved as PDF
python web/heuristic.py assignment.pdf

# Compare pages of a PDF against each other
python web/heuristic.py assignment.pdf --by-page

# Several files in one report, plus a CSV of every message's scores
python web/heuristic.py chat.txt essay1.pdf essay2.pdf --csv scores.csv
```

The report opens in any browser (`ghost-check-report.html` by default). The
terminal also prints a short summary:

```
sample_chat.txt: 15 messages (chat mode)

                               verdict  avg score  likely-AI msgs  judged
sender
Meera                Likely AI-written       87.7               3       3
Arjun   Too few long messages to judge       29.0               0       0
Riya    Too few long messages to judge       25.7               0       0
```

Try it on the bundled example: `python web/heuristic.py examples/sample_chat.txt`.
The output is in `examples/sample_report.html`.

### Options

| Option | Default | What it does |
|---|---|---|
| `-o, --output PATH` | `ghost-check-report.html` | Where to write the HTML report |
| `--csv PATH` | – | Also write per-message scores and features to a CSV file |
| `--top N` | `25` | How many of each sender's highest-scoring messages to list |
| `--mode auto\|chat\|document` | `auto` | Force WhatsApp parsing or plain-document mode |
| `--by-page` | off | In document mode, treat every PDF page as its own "sender" |
| `--dayfirst` / `--monthfirst` | auto | Date order in the export, if auto-detection guesses wrong |

## PDF support

A PDF is handled in one of two ways, picked automatically:

1. **Chat PDF.** If the text contains WhatsApp-style lines (`12/03/2024, 9:05 am - Name: …`),
   it is parsed as a chat and scored per sender, the same as a `.txt` export.
2. **Document PDF.** Otherwise the text is split into paragraph-sized passages
   and each passage is scored. The "sender" is the file name, or the file name
   plus the page number with `--by-page`. Pass several PDFs to compare them.

Scanned PDFs have no text layer. Run them through OCR first (for example
`ocrmypdf in.pdf out.pdf`) and then run ghost-check on the result.

## How the score works

Each message or passage gets five signals. Each one was chosen because
research on LLM text shows it moves in a known direction, and each was then
checked against real data:

| Signal | What it measures |
|---|---|
| **AI-typical words** | Words LLMs overuse, per 100 words: "delve", "crucial", "foster", "additionally", "comprehensive"… (from Kobak et al. 2024 and Liang et al. 2024). The strongest signal: AI prose uses them about 20× more often than human prose (1.6 vs 0.08 per 100 words) |
| **Stock AI phrases** | "it's worth noting", "feel free to", "plays a crucial role", "I hope this helps", "Certainly!"… |
| **Formulaic transitions** | Share of sentences opening with "Furthermore", "Additionally", "Ultimately", "In conclusion"… |
| **Long words** | Average word length. AI vocabulary skews longer |
| **Even sentence rhythm** | Sentence length variance plus burstiness (how much length jumps between consecutive sentences). AI rhythm is flat |

They are combined with a logistic model whose weights were fitted on
labelled data (`eval/fit.py`). Every weight is forced to be ≥ 0, so a signal
can only push towards "AI". That stops the model from learning quirks of the
AI test set. The result is a **0–100 AI-likelihood score**.

**Verdicts.** The thresholds were set on human text only:

| Verdict | When | False-alarm rate on human text |
|---|---|---|
| **Likely AI-written** | score ≥ 84 | ~1% |
| **Some AI signs** | score ≥ 64 | ~5% |
| **No clear AI signs** | below 64 | – (not proof of a human) |
| **Too short to judge** | under 30 words | no verdict given |

**People in a chat.** A person is called "Likely AI-written" only if **at least
two** of their judgeable messages are, and those make up at least 20% of them.
Someone with many messages will eventually hit a 1% false alarm, so one flagged
message only gives "Some AI signs". People with fewer than two messages of 30+
words get "Too few long messages to judge".

**Documents** (pasted text, PDFs) are judged as a whole, using the word-weighted
average score of their passages.

Two signals from the first version were dropped after measurement. Punctuation
density was a coin flip (AUC 0.49), and repetition inside a message pointed the
wrong way: people repeat themselves ("Run away! Run away!"), while AI rarely does.

## Accuracy

Measured with `eval/evaluate.py`. Human text comes from corpora written before
AI chatbots existed, so every flag on it is a false alarm. AI text is
`eval/ai_samples.txt`.

| Tested on | Texts | "Likely AI" | At least "Some AI signs" |
|---|---:|---:|---:|
| Human published prose, 1961 (Brown corpus: news, academic, fiction) | 1,450 | 0.8% | 4.8% |
| Human web text, 2000s (forums, reviews, overheard quotes) | 298 | 1.7% | 6.4% |
| Human writers with 10 texts each, judged per person | 970 | **0%** | 7.7% |
| AI assistant-style answers | 25 | 48% | 80% |
| AI essays | 21 | 48% | 76% |
| AI emails | 12 | 25% | 50% |
| AI prompted to write casually | 12 | 0% | 17% |

Cross-validated AUC of the model: 0.76. Caveats:

- The AI samples were written by one AI model for this evaluation. The AI
  numbers are indicative and other models will differ. The human numbers come
  from real, pre-ChatGPT texts and are the reliable part.
- 2006 chat-room messages are almost all under 30 words, so they get no
  verdict. The false-alarm numbers above are for longer texts.
- English only. Other languages are tokenised correctly, but the word and
  phrase lists are English.

To reproduce, or re-fit after changing a signal:

```bash
pip install -r eval/requirements.txt
python eval/evaluate.py      # error rates (downloads the corpora on first run)
python eval/fit.py           # re-fit weights and thresholds
```

## Supported export formats

- Android: `31/12/23, 9:15 pm - Name: message`
- iOS: `[31/12/2023, 21:15:03] Name: message`
- 12- and 24-hour times, `/`, `.` or `-` date separators, 2- or 4-digit years
- Multi-line messages, `<This message was edited>` markers, and the invisible
  Unicode characters iOS inserts

Day/month order is detected from the file. If every date is ambiguous
(day ≤ 12), day-first is assumed. Use `--monthfirst` for US-style exports.

## Development

`web/ghost-check.js` is a port of the parsing and scoring in `web/heuristic.py`.
After changing either one, including re-fitted weights, check they still agree
(this needs Node.js):

```bash
python tests/check_parity.py
```

## Limitations

- **Not 100% accurate, and no detector is.** See [Accuracy](#accuracy).
- **AI prompted to sound casual, or edited by a person, usually passes.** "No
  clear AI signs" never proves a human wrote something.
- **Formal human writing** (academic, legal, corporate) is the most likely to
  get "Some AI signs".
- **Short messages can't be judged.** Most WhatsApp messages are under 30
  words, so in chats the verdict rests on each person's longer messages.
- **English-centric** word and phrase lists.
