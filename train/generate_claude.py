#!/usr/bin/env python3
"""Generate AI-written formal texts with the Claude API (Message Batches, 50% price).

Creates ~N essays, reports and assignment-style answers across many subjects,
genres, education levels, lengths and writer personas, then writes them to
train/data/claude_generated.jsonl, which prepare_data.py picks up (split by
topic, like the rest of the data).

Needs an API key in ANTHROPIC_API_KEY (or another credential the SDK finds).

    python train/generate_claude.py --n 12000 --dry-run      # show prompts and a cost estimate
    python train/generate_claude.py --n 12000                # submit the batch
    python train/generate_claude.py --collect                # fetch results when it has ended

Cost: set by --model (default claude-opus-5). Estimate with --dry-run first.
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "train" / "data" / "claude_generated.jsonl"
STATE = ROOT / "train" / "data" / "claude_batch_state.json"

SUBJECTS = """
climate policy; renewable energy economics; urban planning; public health; epidemiology of obesity; vaccine hesitancy;
mental health in universities; social media and adolescents; artificial intelligence ethics; data privacy law;
cybersecurity in small businesses; supply chain resilience; international trade agreements; monetary policy and inflation;
microfinance; gig economy labour rights; minimum wage debates; universal basic income; housing affordability;
homelessness; criminal justice reform; policing and communities; immigration policy; refugee integration; nationalism;
the European Union; the Cold War; decolonisation in Africa; the Industrial Revolution; the French Revolution;
the Roman Empire's decline; the Renaissance; the printing press; World War I causes; the civil rights movement;
feminist movements; Shakespeare's tragedies; the Victorian novel; postcolonial literature; modernist poetry;
film adaptation of novels; music and identity; museum ethics; architecture and sustainability; fast fashion;
consumer behaviour; brand loyalty; marketing ethics; corporate social responsibility; leadership styles;
organisational culture; remote work productivity; human resource management; project management failures;
startup funding; accounting fraud cases; behavioural economics; game theory in business; auction design;
biodiversity loss; coral reef decline; deforestation; water scarcity; ocean plastics; agricultural technology;
genetically modified crops; food security; nutrition science; exercise physiology; sleep science; ageing populations;
healthcare funding models; antibiotic resistance; CRISPR gene editing; stem cell research; neuroscience of memory;
child development; language acquisition; bilingual education; standardised testing; online learning;
early childhood education; special educational needs; teacher retention; school choice; higher education funding;
philosophy of free will; utilitarianism; Kantian ethics; existentialism; philosophy of science; religion and politics;
secularism; media bias; misinformation; journalism ethics; freedom of speech; surveillance; smart cities;
autonomous vehicles; space exploration; quantum computing; nuclear power; electric vehicles; battery recycling;
materials science of polymers; civil engineering of bridges; earthquake preparedness; flood management;
tourism impacts; sports economics; doping in sport; esports; video games and learning; animal welfare;
zoos and conservation; pharmaceutical pricing; telemedicine; nursing workforce; public transport; cycling infrastructure;
air pollution; noise pollution; environmental justice; indigenous land rights; globalisation and culture;
the gender pay gap; parental leave policy; volunteering; charitable giving; cryptocurrency regulation;
central bank digital currencies; stock market bubbles; the 2008 financial crisis; development economics;
foreign aid effectiveness; urban-rural inequality; demography and fertility; migration and remittances
""".replace("\n", " ").split(";")
SUBJECTS = [s.strip() for s in SUBJECTS if s.strip()]

GENRES = {
    "argumentative essay": "Write an argumentative essay that takes and defends a clear position on a debatable question about {subject}.",
    "expository essay": "Write an expository essay explaining a key aspect of {subject} to a non-specialist reader.",
    "compare-contrast essay": "Write a compare-and-contrast essay on two competing approaches or viewpoints within {subject}.",
    "report": "Write a structured report on {subject} with short headed sections (e.g. introduction, findings, recommendations).",
    "policy brief": "Write a policy brief on {subject} for a government audience, ending with concrete recommendations.",
    "literature review section": "Write the literature review section of a student paper on {subject}, discussing prior research.",
    "case study analysis": "Write a case study analysis applying course concepts to a realistic example related to {subject}.",
    "exam answer": "Answer this exam-style question in essay form: 'Critically evaluate a major debate in {subject}.'",
    "reflective assignment": "Write a reflective assignment on what a student learned while studying {subject}.",
    "lab or project report": "Write a project report section (methods, results, discussion) for a student project on {subject}.",
}
LEVELS = ["high-school", "first-year undergraduate", "final-year undergraduate", "master's"]
LENGTHS = [250, 400, 600, 900]
PERSONAS = [
    "",
    "Write it the way a typical student would submit it.",
    "Write in a plain, direct style without flowery language.",
    "Write as a non-native English speaker who writes clearly but not perfectly idiomatically.",
    "Include a few in-text citations in author-date style.",
    "Use a slightly informal academic tone.",
]


def build_requests(n: int, model: str, seed: int) -> list[dict]:
    rng = random.Random(seed)
    combos = list(itertools.product(range(len(SUBJECTS)), GENRES))
    rng.shuffle(combos)
    reqs = []
    for i in range(n):
        s_idx, genre = combos[i % len(combos)]
        subject = SUBJECTS[s_idx]
        level, length, persona = rng.choice(LEVELS), rng.choice(LENGTHS), rng.choice(PERSONAS)
        parts = [GENRES[genre].format(subject=subject), f"Write it at a {level} level, about {length} words.",
                 persona, "Output only the text itself: no preamble, no notes, no word count."]
        prompt = " ".join(p for p in parts if p)
        reqs.append({"custom_id": f"g{i:05d}", "topic_id": s_idx, "subject": subject, "genre": genre,
                     "level": level, "length": length, "model": model,
                     "params": {"model": model, "max_tokens": 4000,
                                "output_config": {"effort": "low"},
                                "messages": [{"role": "user", "content": prompt}]}})
    return reqs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12000)
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true", help="print sample prompts and a rough cost estimate")
    ap.add_argument("--collect", action="store_true", help="wait for the submitted batch and write results")
    args = ap.parse_args()

    if args.dry_run:
        reqs = build_requests(args.n, args.model, args.seed)
        for r in reqs[:5]:
            print(r["params"]["messages"][0]["content"], "\n")
        out_tokens = sum(r["length"] * 1.4 for r in reqs)
        in_tokens = len(reqs) * 90
        print(f"{len(reqs)} requests, ~{in_tokens / 1e6:.1f}M input / ~{out_tokens / 1e6:.1f}M output tokens.")
        print("Batch price is 50% of the model's list price; see the pricing page for the model you chose.")
        return 0

    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    client = anthropic.Anthropic()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    if not args.collect:
        reqs = build_requests(args.n, args.model, args.seed)
        batch = client.messages.batches.create(requests=[
            Request(custom_id=r["custom_id"], params=MessageCreateParamsNonStreaming(**r["params"])) for r in reqs
        ])
        meta = {r["custom_id"]: {k: r[k] for k in ("topic_id", "subject", "genre", "level", "length", "model")}
                for r in reqs}
        STATE.write_text(json.dumps({"batch_id": batch.id, "meta": meta}))
        print(f"Submitted batch {batch.id} ({len(reqs)} requests). Run again with --collect to fetch results.")
        return 0

    state = json.loads(STATE.read_text())
    while True:
        batch = client.messages.batches.retrieve(state["batch_id"])
        if batch.processing_status == "ended":
            break
        print(f"{batch.processing_status}: {batch.request_counts.processing} still processing")
        time.sleep(60)

    kept = skipped = 0
    with open(OUT, "w", encoding="utf-8") as f:
        for result in client.messages.batches.results(state["batch_id"]):
            if result.result.type != "succeeded":
                skipped += 1
                continue
            msg = result.result.message
            if msg.stop_reason not in ("end_turn", "max_tokens"):  # e.g. "refusal"
                skipped += 1
                continue
            text = "".join(b.text for b in msg.content if b.type == "text").strip()
            if len(text.split()) < 100:
                skipped += 1
                continue
            m = state["meta"][result.custom_id]
            f.write(json.dumps({"id": result.custom_id, "text": text, **m}, ensure_ascii=False) + "\n")
            kept += 1
    print(f"Wrote {kept} samples to {OUT} ({skipped} skipped: errors, refusals or too short).")
    print("Next: python train/prepare_data.py ... && python train/train.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
