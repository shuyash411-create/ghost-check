#!/usr/bin/env python3
"""Check that the web engine (web/ghost-check.js) scores exactly like ghost_check.py.

Runs both on the same inputs and compares every message's features and score.
Needs Node.js on PATH.  Usage:  python tests/check_parity.py
"""

import json
import math
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import ghost_check as gc  # noqa: E402

FIELDS = ["words", "sentences", *gc.WEIGHTS, "score"]

SAMPLES = {
    "sample_chat.txt": (ROOT / "examples" / "sample_chat.txt").read_text(encoding="utf-8"),
    "essay.txt": (
        "In today's fast-paced world, effective communication plays a crucial role in organizational "
        "success. It is important to note that clear messaging fosters collaboration and trust. "
        "Additionally, teams that leverage modern tools can streamline their workflows.\n\n"
        "So I tried the new bus route today. Terrible idea. It took forty minutes, the driver missed my "
        "stop, and I had to walk back in the rain with a broken umbrella. Honestly? Never again!!"
    ),
    "ios.txt": (
        "‎[1/25/24, 9:05:12 PM] Sam: hey what time tomorrow, i was thinking maybe 6ish\n"
        "[1/25/24, 9:06:00 PM] Kim: Certainly! Here are some options that could work well for everyone involved.\n"
        "[1/25/24, 9:07:00 PM] Kim: ‎image omitted\n"
        "[1/25/24, 9:08:00 PM] Sam: ok cool — see you then; don't be late!!!\n"
        "[1/25/24, 9:09:00 PM] Kim: Rest assured, I will arrive on time. Feel free to reach out.\n"
        "multi-line continuation with a second line\n"
    ),
    "hindi.txt": "मैं कल बाज़ार गया था। वहाँ बहुत भीड़ थी। फिर हम घर वापस आ गए और खाना खाया।",
}

JS = r"""
const GC = require(process.argv[1]);
const samples = JSON.parse(require("fs").readFileSync(0, "utf8"));
const out = {};
for (const [name, text] of Object.entries(samples)) {
  const { records, mode } = GC.load([text], name);
  GC.scoreRecords(records, 6);
  out[name] = { mode, rows: records.map((r) => ({ sender: r.sender, message: r.message,
    ...Object.fromEntries(%s.map((k) => [k, Number.isNaN(r[k]) ? null : r[k]])) })) };
}
process.stdout.write(JSON.stringify(out));
""" % json.dumps(FIELDS)


def python_rows(name, text):
    recs, mode = gc.load_text([text], name, "auto", None, False)
    df = pd.DataFrame([r.__dict__ for r in recs])
    df = gc.score_frame(df, 6)
    rows = []
    for _, r in df.iterrows():
        row = {"sender": r.sender, "message": r.message}
        for k in FIELDS:
            v = r[k]
            row[k] = None if pd.isna(v) else float(v)
        rows.append(row)
    return mode, rows


def main() -> int:
    js = subprocess.run(
        ["node", "-e", JS, str(ROOT / "web" / "ghost-check.js")],
        input=json.dumps(SAMPLES), capture_output=True, text=True, check=True,
    )
    js_out = json.loads(js.stdout)
    failures = 0
    for name, text in SAMPLES.items():
        mode, py = python_rows(name, text)
        jr = js_out[name]
        if mode != jr["mode"] or len(py) != len(jr["rows"]):
            print(f"FAIL {name}: mode/records differ: py={mode}/{len(py)} js={jr['mode']}/{len(jr['rows'])}")
            failures += 1
            continue
        for i, (a, b) in enumerate(zip(py, jr["rows"])):
            for k in ["sender", "message", *FIELDS]:
                x, y = a[k], b[k]
                same = (x == y) if isinstance(x, str) or x is None or y is None else math.isclose(x, y, abs_tol=1e-9)
                if not same:
                    print(f"FAIL {name} #{i} {k}: py={x!r} js={y!r}")
                    failures += 1
        print(f"ok   {name}: {len(py)} records ({mode} mode)")
    print("PARITY OK" if not failures else f"{failures} mismatches")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
