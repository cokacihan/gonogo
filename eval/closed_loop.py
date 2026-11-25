"""Close the loop on a real gate failure: nasa/fprime PR #5170.

The gate fails #5170 because TLC-005 and TLC-006 were added to the TlmChan SDD without a citing test, in a
component whose tests cite requirements. This script
  1. imports the PR head (a local checkout of nasa/fprime at bf71d97) into a Relate graph,
  2. asks `gonogo suggest` where each blocking requirement's REQUIREMENT() line would go,
  3. writes the two suggestions as one patch (data/replay/pr5170-suggested.patch),
  4. applies it to the checkout, imports again, runs the same gate against the same base, and reverts the patch.
The gate-replay workflow repeats steps 3-4 on a fresh checkout in GitHub Actions with the committed patch.

Whether the suggested tests really check the requirements is a reviewer's call; the patch only shows that two
lines in the right place are all the gate needs, and where the place is.

  python -m eval.closed_loop --repo data/raw/fprime-pr5170-bf71d97
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from gonogo import db as store
from gonogo.diff import check
from gonogo.fprime import build_dataset
from gonogo.suggest import patch, suggest

ROOT = Path(__file__).resolve().parent.parent
REPLAY = ROOT / "data" / "replay"
BASE = REPLAY / "pr5170-d833799.json"
FILES = REPLAY / "pr5170-files.txt"
PATCH = REPLAY / "pr5170-suggested.patch"
REPORT = REPLAY / "pr5170-suggested-check.md"


def graph(dataset: dict):
    path = Path(tempfile.mkdtemp(prefix="gonogo_loop_")) / "store"
    store.build(dataset, path)
    return store.connect(path)


def main(repo: Path) -> dict:
    repo = repo.resolve()
    files = FILES.read_text(encoding="utf-8").split()
    base_db = graph(json.loads(BASE.read_text(encoding="utf-8")))
    head_db = graph(build_dataset(repo))
    before, _, code_before = check(base_db, head_db, files)
    blocking_ids = [b.rsplit(": ", 1)[1] for b in before["blocking"] if "without an own citing test" in b]

    diffs, picks = [], []
    for rid in blocking_ids:
        s = suggest(head_db, rid)
        top = s["sites"][0]
        diffs.append(patch(repo, rid, top))
        picks.append({"id": rid, "text": s["text"], "site": top["name"], "kind": top["kind"], "at": top["at"],
                      "next": [x["name"] for x in s["sites"][1:3]]})
    PATCH.write_text("".join(diffs), encoding="utf-8", newline="\n")

    subprocess.run(["git", "-C", str(repo), "apply", str(PATCH)], check=True)
    try:
        patched = build_dataset(repo)
    finally:
        subprocess.run(["git", "-C", str(repo), "apply", "-R", str(PATCH)], check=True)
    touched = sorted({line[6:] for line in PATCH.read_text(encoding="utf-8").splitlines() if line.startswith("+++ b/")})
    after, md, code_after = check(base_db, graph(patched), files + touched)

    lines = ["## Closing the loop on nasa/fprime#5170", "",
             f"Gate on the merged PR: **{'FAIL' if code_before else 'PASS'}** "
             f"({len(before['blocking'])} blocking). `gonogo suggest` proposed:", ""]
    lines += [f"- `{p['id']}` ({p['text'].rstrip('.')}): `REQUIREMENT(\"{p['id']}\");` in {p['kind']} "
              f"`{p['site']}` ({', '.join(p['at'])}); next candidates: {', '.join(f'`{n}`' for n in p['next'])}"
              for p in picks]
    lines += ["", f"With these {len(picks)} lines ([patch](pr5170-suggested.patch)) the same gate against the same "
              f"base says **{'FAIL' if code_after else 'PASS'}**:", "", md]
    REPORT.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return {"before": "FAIL" if code_before else "PASS", "after": "FAIL" if code_after else "PASS",
            "picks": picks, "blocking_after": after["blocking"]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(ROOT / "data" / "raw" / "fprime-pr5170-bf71d97"))
    print(json.dumps(main(Path(ap.parse_args().repo)), indent=1))
