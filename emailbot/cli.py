"""Same steps without Slack, for testing.

  python -m emailbot.cli sync
  python -m emailbot.cli match "text from the design" [--hint "like welcome.html"]
  python -m emailbot.cli new <figma link> ["notes"]
  python -m emailbot.cli change <job id> "make the button green"
  python -m emailbot.cli final <job id> <S3 folder URL>
"""
from __future__ import annotations

import argparse

from . import library, pipeline


def main():
    p = argparse.ArgumentParser(prog="emailbot")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync"); s.add_argument("--force", action="store_true")
    m = sub.add_parser("match"); m.add_argument("text"); m.add_argument("--hint", default="")
    n = sub.add_parser("new"); n.add_argument("figma"); n.add_argument("notes", nargs="?", default="")
    c = sub.add_parser("change"); c.add_argument("job"); c.add_argument("request")
    f = sub.add_parser("final"); f.add_argument("job"); f.add_argument("s3")
    a = p.parse_args()

    if a.cmd == "sync":
        print(f"{len(library.sync(force=a.force))} templates cached.")
    elif a.cmd == "match":
        for t, score in library.find_matches(a.text, hint=a.hint):
            print(f"{score:.3f}  {t.file}  — {t.title}")
    elif a.cmd in ("new", "change"):
        res = (
            pipeline.start(a.figma, a.notes)
            if a.cmd == "new"
            else pipeline.revise(pipeline.Job.load(a.job), a.request)
        )
        print(f"\nJob {res.job.id}  base={res.job.base}\nHTML: {res.html}\nImages for S3: {res.images_zip}")
        print(res.notes)
        for w in res.warnings:
            print(w)
    elif a.cmd == "final":
        res = pipeline.finalize(pipeline.Job.load(a.job), a.s3)
        print(f"Client zip: {res.final_zip}")
        for w in res.warnings:
            print(w)


if __name__ == "__main__":
    main()
