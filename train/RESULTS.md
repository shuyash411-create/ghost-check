# ghost-check classifier: evaluation results

Model: TF-IDF (word 1–2-grams + character 3–5-grams, 270,000 features) +
logistic regression (C = 32, chosen on validation). Trained on formal text
only: essays, news articles, university assignments and exam essays.
Documents are scored in ~200-word sections, and the document score is the mean.

Raw numbers: [`results.json`](results.json). Reproduce with `prepare_data.py`,
`train.py` and `sanity/run_sanity.py` (see README, "Training").

**Short version:** near-perfect on text like its training data, good on
unseen human writing, but noticeably weaker on AI text from generators it
hasn't seen, including current models. In the 20-document real-world check it
got 15/20 right: 1 of 10 human documents was wrongly flagged and 4 of 10 AI
documents were missed. It is not 100% accurate, and no detector is.

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
- **Scale:** 58,371 training sections, 12,939 validation, 12,460 test (3,947
  test documents: 1,410 human, 2,537 AI).
- **Chunking:** everything is cut into ~200-word sections because AI essays in
  Ghostbuster are ~30% longer than human ones; whole documents would let the
  model cheat on length.
- **Normalisation:** typography (quotes, dashes) is flattened so the model
  can't learn which word processor or website a text came from.
- **Thresholds:** set on validation **human** documents only. "Likely AI" is
  at 0.40, where about 1% of validation human documents score higher.
  "Possibly AI" is at 0.21 (about 5%).

**Not done:**
- **Claude API samples (step 2):** no API key was available in the build
  environment. `generate_claude.py` is ready to run (see README), and
  `prepare_data.py` picks its output up automatically. This matters: see the
  sanity check below.
- **HC3 and other Hugging Face datasets:** Hugging Face was unreachable from
  the build environment. The GitHub-hosted Ghostbuster and ArguGPT corpora were
  used instead.
- **Fine-tuned transformer:** it needs pretrained weights from Hugging Face,
  which were unreachable. Only the TF-IDF + logistic regression baseline was
  trained.

## 1. Held-out test split (same sources as training, unseen documents)

| Threshold | Accuracy | False positive rate (human flagged) | False negative rate (AI missed) | ROC AUC |
|---|---:|---:|---:|---:|
| 0.5 | 99.5% | 0.35% (5/1,410) | 0.51% (13/2,537) | 0.9998 |
| **"Likely AI" (0.40)** | **99.5%** | **0.99%** (14/1,410) | **0.28%** (7/2,537) | |
| "Possibly AI" or higher (0.21) | 97.5% | 6.95% (98/1,410) | 0.08% (2/2,537) | |

The same test split scored per ~200-word section instead of per document:
98.0% accuracy, 2.9% FPR, 1.5% FNR at 0.5. Short excerpts are harder.

Flag rate ("Likely AI") by source, test documents:

| Source | Docs | Flagged "Likely AI" |
|---|---:|---:|
| Human student essays | 466 | 1.5% |
| Human Reuters news | 427 | 0.5% |
| Human UK university assignments (BAWE) | 219 | 0.0% |
| Human TOEFL essays (ETS, non-native) | 143 | 0.7% |
| Human learner writing (PELIC, non-native) | 155 | 2.6% |
| GPT-3.5 essays / news | 771 / 584 | 100% / 99.8% |
| Claude essays / news | 467 / 427 | 99.4% / 99.8% |
| ArguGPT gpt-3.5-turbo / text-davinci-003 / -002 | 115 / 112 / 61 | 100% / 99.1% / 98.4% |

## 2. Out-of-distribution: sources never used for training or thresholds

This is the better guide to real-world behaviour.

| Source | Docs | Flagged "Likely AI" | "Possibly" or higher |
|---|---:|---:|---:|
| **Human:** 1961 published prose (Brown corpus) | 374 | 0.3% | 2.4% |
| **Human:** Lang-8 learner writing (non-native) | 300 | 0.3% | 1.3% |
| **Human:** TOEFL-91 essays (non-native) | 91 | 4.4% | 4.4% |
| **AI:** GPT-4 essays (ArguGPT) | 100 | 100% | 100% |
| **AI:** Claude-instant essays (ArguGPT) | 100 | 100% | 100% |
| **AI:** "humanised" AI text (passed through undetectable.ai) | 100 | 85% | 90% |
| **AI:** Claude-written essays and emails (short, 53–69 words) | 33 | 79% | 82% |
| **AI:** Flan-T5-11B essays | 100 | 36% | 57% |
| **AI:** BLOOMZ-7B essays | 100 | 25% | 42% |

## 3. Real-world sanity check: 20 assignment-style documents, run as PDFs

Each text was rendered to a real PDF and run through the CLI's PDF path.
Human: 6 UK university assignments and 2 TOEFL essays from the held-out test
split, plus 2 US State of the Union addresses (never used). AI: 10
assignment-style texts written by a current Claude model, which is newer than
any generator in the training data. Full table: [`sanity/results.md`](sanity/results.md).

| True author | Correct | Wrong |
|---|---:|---:|
| Human (10) | 9 | 1: a TOEFL essay flagged "Likely AI" (score 40) |
| AI (10) | 6 (2 "Likely", 4 "Possibly") | 4 missed: a reflective assignment, a policy brief, a non-native-style essay, an informal essay |

**Overall: 15/20 = 75% accuracy. False positive rate 1/10, false negative rate 4/10.**

Even where it caught the current-model AI texts, the scores were modest
(24–52) compared with the older GPT and Claude texts (median ~99). The model
has learned what *older* LLM text looks like. Current models, and AI text
written in a personal, non-native or informal voice, are much harder for it.

## 4. v1 → v2: what changed

v1 trained only on Ghostbuster's polished essays and news. It scored 99.6% on
its own test split, but on unseen writers it wrongly flagged 14.5% of real
university assignments, 22.7% of Lang-8 and 36.3% of PELIC learner texts.
v2 added BAWE, ETS and PELIC human writing and ArguGPT AI essays written on
the same exam prompts. Real assignments dropped to 0.0% and Lang-8 (still
never trained on) to 0.3%. The in-distribution numbers barely moved, which is
why in-distribution accuracy alone says little.

## Limitations

- **Generalisation to new models is the weak point** (section 3). Training on
  samples from current models (`generate_claude.py`, and ideally other vendors
  too) is the most valuable next step.
- **Short texts** (under ~250 words) are less reliable, and under 80 words no
  score is given.
- **Topic leakage is reduced, not eliminated.** Some top features are subject
  words (e.g. "bacteria", "bones"), so the model partly uses topic.
- **English only.**
- **Edited, paraphrased or "humanised" AI text** will often evade it (85%
  caught here, but other tools and heavier editing will do worse).
- **Never use a score alone to accuse someone.**
