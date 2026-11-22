# ghost-check classifier: evaluation results

Model: TF-IDF (word 1–2-grams + character 3–5-grams, 270,000 features) +
logistic regression (C = 32, chosen on validation). Trained on formal text
only: essays, news articles, university assignments and exam essays.
Documents are scored in ~200-word sections, and the document score is the
word-weighted mean. The CLI and the web app use the same model and give
identical scores (`tests/check_parity.py`).

Raw numbers: [`results.json`](results.json) and
[`sanity/results.json`](sanity/results.json). Reproduce with `prepare_data.py`,
`train.py` and `sanity/run_sanity.py` (see the README, "Training").

**Short version:** near-perfect on text like its training data, and good on
unseen *human* writing, but noticeably weaker on AI text from generators it
hasn't seen, especially current models. In the 21-document real-world check
it got 15/21 right: none of the 10 human documents was wrongly flagged, and 6
of the 11 AI documents were missed, including a real, user-submitted, cited
economics report (see section 3). It is not 100% accurate, and no detector is.

## Data

| | Human | AI |
|---|---|---|
| Ghostbuster essays (same prompts for all sources) | 2,994 student essays | 5,001 GPT-3.5 + 3,000 Claude |
| Ghostbuster Reuters news (same articles) | 3,000 | 4,000 GPT-3.5 + 3,000 Claude |
| BAWE: UK university assignments | 1,444 | – |
| ETS (TOEFL essays) and PELIC (learners), non-native writers | 1,000 + 1,000 | – |
| ArguGPT exam essays (TOEFL/WECCL/GRE prompts) | – | 1,958 by text-davinci-002/003, gpt-3.5-turbo |

- **Split:** 70/15/15 train/validation/test **by prompt/article group**, so
  human and AI texts on the same prompt never straddle splits.
- **Scale:** 58,355 training sections, 12,521 validation, 12,322 test (3,939
  test documents: 1,416 human, 2,523 AI).
- **Chunking:** everything is cut into ~200-word sections at sentence ends,
  because AI essays in Ghostbuster are ~30% longer than human ones; whole
  documents would let the model cheat on length.
- **Normalisation:** typography (quotes, dashes) is flattened so the model
  can't learn which word processor or website a text came from.
- **Thresholds:** set on validation **human** documents only. "Likely AI" is
  at 0.40, where about 1% of validation human documents score higher.
  "Possibly AI" is at 0.18 (about 5%).

**Not done:**
- **Claude API samples:** no API key was available in the build environment.
  `generate_claude.py` is ready to run, and `prepare_data.py` picks its output
  up automatically. This is the most valuable next step: see section 3.
- **HC3 and other Hugging Face datasets:** Hugging Face was unreachable from
  the build environment. The GitHub-hosted Ghostbuster and ArguGPT corpora
  were used instead.
- **Fine-tuned transformer:** it needs pretrained weights from Hugging Face,
  which were unreachable. Only the TF-IDF + logistic regression baseline was
  trained.

## 1. Held-out test split (same sources as training, unseen documents)

| Threshold | Accuracy | False positive rate (human flagged) | False negative rate (AI missed) | ROC AUC |
|---|---:|---:|---:|---:|
| 0.5 | 99.2% | 0.78% (11/1,416) | 0.79% (20/2,523) | 0.9997 |
| **"Likely AI" (0.40)** | **99.3%** | **1.06%** (15/1,416) | **0.44%** (11/2,523) | |
| "Possibly AI" or higher (0.18) | 98.2% | 4.87% (69/1,416) | 0.08% (2/2,523) | |

The same test split scored per ~200-word section instead of per document:
98.4% accuracy, 2.0% FPR, 1.4% FNR at 0.5. Short excerpts are harder.

Flag rate ("Likely AI") by source, test documents:

| Source | Docs | Flagged "Likely AI" |
|---|---:|---:|
| Human student essays | 460 | 1.1% |
| Human Reuters news | 429 | 0.2% |
| Human UK university assignments (BAWE) | 219 | 0.0% |
| Human TOEFL essays (ETS, non-native) | 153 | 0.7% |
| Human learner writing (PELIC, non-native) | 155 | 5.2% |
| GPT-3.5 essays / news | 770 / 577 | 100% / 100% |
| Claude essays / news | 461 / 429 | 99.3% / 99.3% |
| ArguGPT gpt-3.5-turbo / text-davinci-003 / -002 | 96 / 108 / 82 | 100% / 100% / 93.9% |

## 2. Out-of-distribution: sources never used for training or thresholds

This is the better guide to real-world behaviour.

| Source | Docs | Flagged "Likely AI" | "Possibly" or higher |
|---|---:|---:|---:|
| **Human:** 1961 published prose (Brown corpus) | 374 | 0.0% | 1.3% |
| **Human:** Lang-8 learner writing (non-native) | 300 | 1.0% | 2.7% |
| **Human:** TOEFL-91 essays (non-native) | 91 | 4.4% | 5.5% |
| **AI:** Claude-instant essays (ArguGPT) | 100 | 100% | 100% |
| **AI:** GPT-4 essays (ArguGPT) | 100 | 99% | 100% |
| **AI:** "humanised" AI text (passed through undetectable.ai) | 100 | 85% | 92% |
| **AI:** Claude-written essays and emails (short, 53–69 words) | 33 | 76% | 85% |
| **AI:** Flan-T5-11B essays | 100 | 37% | 60% |
| **AI:** BLOOMZ-7B essays | 100 | 24% | 44% |

## 3. Real-world sanity check: 21 assignment-style documents, run as PDFs

Each text was rendered to a real PDF and scored through the CLI's PDF path
(pypdf) and through the web app (pdf.js). Both gave identical scores for all
21 documents.
- **Human (10):** 6 UK university assignments and 2 TOEFL essays from the
  held-out test split, plus 2 US State of the Union addresses (never used).
- **AI (11):** assignment-style texts written by a current Claude model, which
  is newer than any generator in the training data. #11 is a real ~3,100-word
  cited economics report a user submitted to us as a false negative (it
  originally scored 0–6/100 depending on PDF extractor); it is anonymised
  (names, registration number and instructor identity removed) but otherwise
  verbatim.

Full table: [`sanity/results.md`](sanity/results.md).

| True author | Correct | Wrong |
|---|---:|---:|
| Human (10) | **10** (scores 0–15) | 0 |
| AI (11) | 5: Frankenstein essay and printing-press essay "Likely"; business case, lab report and policy brief "Possibly" | **6 missed**: minimum-wage essay (13), antibiotic-resistance explainer (17), reflective assignment (11), non-native-style essay (1), informal essay (0), **cited economics report (3)** |

**Overall: 15/21 = 71% accuracy. False positive rate 0/10, false negative rate 6/11.**

The model has learned what *older* LLM text looks like: GPT-3.5, GPT-4 and
Claude-instant are caught 99–100% of the time. Text from a current model
scores far lower (median 17 here, vs ~99 for the older models). AI text in a
personal, non-native, informal, or long factual/cited-report voice scored
close to 0. The cited economics report is a particularly hard case: v2's
retraining specifically taught the model that dense academic phrasing
(semicolons, parentheticals, "of the", formal connectors) is a *human*
signal, because that fixed v1's false positives on real student essays. A
well-researched, properly cited AI report in that same register lands on the
wrong side of that same fix — see the tradeoff noted in section 4.

## 4. Version history: what changed and why

| Version | Change | Effect |
|---|---|---|
| v1 | Ghostbuster essays and news only | 99.6% on its own test split, but wrongly flagged 14.5% of real university assignments, 22.7% of Lang-8 and 36.3% of PELIC learner texts as "Likely AI" |
| v2 | Added BAWE, ETS and PELIC human writing, and ArguGPT AI essays on the same exam prompts as the ETS essays | Real assignments dropped to 0% flagged and Lang-8 (never trained on) to 0.3%. In-distribution numbers barely moved, which is why in-distribution accuracy alone says little |
| v3 (current) | Sections split only at sentence ends (not line breaks), and document score word-weighted | In v2 a PDF's line wraps moved section boundaries, so the same document could score 26 as text and 11 as a PDF. v3 gives the same score for text and PDF, and the same score in the CLI and the web app |

## Limitations

- **Generalisation to new models is the weak point** (section 3). Training on
  samples from current models (`generate_claude.py`, and ideally other vendors
  too) is the most valuable next step.
- **Long, factually dense, properly cited reports are a specific hard case**,
  confirmed by a real user-submitted false negative added to the sanity check
  (`train/sanity/ai/11_econ_report_asean.txt`). The word patterns that fixed
  v1's false positives on real student essays (semicolons, parentheticals,
  formal connectors) are the same patterns a well-cited AI report uses.
  `generate_claude.py` now has a "cited research report" genre and a longer
  length range (up to 2,000 words, was 900) to target this gap, but it hasn't
  been run yet (no API key was available). One real example isn't enough data
  to retrain on without just memorising that document; a batch of generated
  examples in this genre is needed first.
- **Short texts** (under ~250 words) are less reliable, and under 80 words no
  score is given.
- **Topic leakage is reduced, not eliminated.** Some top features are subject
  words, so the model partly uses topic.
- **English, formal writing only.**
- **Edited, paraphrased or "humanised" AI text** will often evade it (85%
  caught here, but other tools and heavier editing will do worse).
- **Never use a score alone to accuse someone.**
