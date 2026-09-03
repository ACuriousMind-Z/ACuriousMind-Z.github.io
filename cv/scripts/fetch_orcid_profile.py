#!/usr/bin/env python3
"""Fetch the ORCID biography and write it to _data/orcid.yml for the site.

ORCID's /person endpoint carries the free-text biography shown on the public
ORCID page. This script mirrors it into the site's data directory so
index.md can render it, keeping the "About Me" text on jxzhao.com and the
ORCID record from drifting apart.

ORCID stores the biography as plain text, so two things the site had before
would be lost verbatim: the link to the lab, and the sub-heading sentences
that open the research-direction paragraphs. Two narrow, deterministic
transforms restore them (see AUTOLINKS and emphasize_lead_in). Both are
conservative and add no words: when a rule does not clearly apply, the text
passes through untouched. Run with --raw to see exactly what ORCID returned.

Markdown you write in the ORCID biography itself passes through unchanged;
a paragraph that already carries emphasis is left alone.

Only the public API is read; nothing is written back to ORCID (that would
need an OAuth member token). If the biography is set to private, or has never
been filled in, ORCID returns no content and this writes an empty value --
index.md falls back to its hand-written text in that case.

Like fetch_orcid.py, this exits non-zero WITHOUT touching the output file if
ORCID is unreachable, so a transient network failure can never blank the
site's biography.

Standalone usage:
    python3 fetch_orcid_profile.py
    python3 fetch_orcid_profile.py --dry-run
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

from fetch_orcid import ORCID_ID, USER_AGENT, FetchError, _get_with_backoff

CV_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = CV_DIR.parent
OUTPUT_YML = REPO_ROOT / "_data" / "orcid.yml"

ORCID_PERSON_URL = f"https://pub.orcid.org/v3.0/{ORCID_ID}/person"

# Phrases to turn into links, first occurrence only. Keep this list short and
# specific: it exists to restore links the site already had, not to decorate
# the text. A phrase that is already part of a markdown link is skipped.
AUTOLINKS = {
    "Microcellular Plastics Manufacturing Laboratory": "https://mpml.mie.utoronto.ca/lab/",
}

# A lead-in sentence is bolded only when all of these hold, so an ordinary
# opening sentence is never mistaken for a heading:
#   - it is the paragraph's first sentence and the paragraph continues after it
#   - it is at most LEAD_IN_MAX_CHARS long
#   - it contains exactly one period, its own terminator
#   - it is at least LEAD_IN_MIN_WORDS words long, which together with the
#     single-period rule is what keeps a leading abbreviation such as
#     "Prof. Chul B. Park." from being read as a one-word heading
#   - it contains no first-person pronoun, which a heading would not use
#   - the paragraph carries no markdown emphasis already
LEAD_IN_MAX_CHARS = 80
LEAD_IN_MIN_WORDS = 3
_LEAD_IN_RE = re.compile(r"^([^.]{1,%d}\.)(\s+)(\S.*)$" % (LEAD_IN_MAX_CHARS - 1), re.DOTALL)
_FIRST_PERSON_RE = re.compile(r"\b(I|my|we|our)\b", re.IGNORECASE)
_EXISTING_EMPHASIS_RE = re.compile(r"\*\*|__|\[[^\]]*\]\(")


def emphasize_lead_in(paragraph: str) -> str:
    """Bold a heading-like opening sentence. Returns the paragraph unchanged
    unless every condition above is met."""
    if _EXISTING_EMPHASIS_RE.search(paragraph):
        return paragraph
    match = _LEAD_IN_RE.match(paragraph.strip())
    if not match:
        return paragraph
    lead_in, gap, rest = match.groups()
    if len(lead_in.split()) < LEAD_IN_MIN_WORDS:
        return paragraph
    if _FIRST_PERSON_RE.search(lead_in):
        return paragraph
    return f"**{lead_in}**{gap}{rest}"


def apply_autolinks(text: str) -> str:
    for phrase, url in AUTOLINKS.items():
        if f"[{phrase}]" in text:
            continue  # already linked, by hand in ORCID or by an earlier run
        text = text.replace(phrase, f"[{phrase}]({url})", 1)
    return text


def to_markdown(biography: str) -> str:
    """Restore the link and the sub-headings ORCID's plain-text field drops."""
    paragraphs = [emphasize_lead_in(p) for p in biography.split("\n\n")]
    return apply_autolinks("\n\n".join(paragraphs))


def fetch_biography() -> str:
    """Return the public ORCID biography, or '' if it is unset or private."""
    try:
        resp = _get_with_backoff(
            ORCID_PERSON_URL,
            {"Accept": "application/json", "User-Agent": USER_AGENT},
        )
    except FetchError as exc:
        raise FetchError(f"ORCID unreachable: {exc}") from exc

    try:
        data = resp.json()
    except ValueError as exc:
        raise FetchError(f"ORCID returned invalid JSON: {exc}") from exc

    biography = data.get("biography") or {}
    # `visibility` is "public" whenever the field is returned at all by the
    # public API, but check anyway so a future API change fails closed.
    if biography.get("visibility") not in (None, "public", "PUBLIC"):
        return ""
    return (biography.get("content") or "").strip()


def build(dry_run: bool, raw: bool = False) -> int:
    try:
        biography = fetch_biography()
    except FetchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(f"{OUTPUT_YML} left untouched.", file=sys.stderr)
        return 1

    if biography and not raw:
        transformed = to_markdown(biography)
        if transformed != biography:
            print("Applied markdown transforms (links, lead-in emphasis).")
        biography = transformed

    if biography:
        print(f"ORCID: biography is {len(biography)} character(s).")
    else:
        print("ORCID: no public biography set; writing an empty value "
              "(index.md will use its hand-written text).")

    body = yaml.safe_dump(
        {"orcid": ORCID_ID, "biography": biography},
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=True,
        width=88,
    )
    out = (
        "# _data/orcid.yml -- GENERATED by cv/scripts/fetch_orcid_profile.py\n"
        "# Do not hand-edit. Source: the ORCID public API /person endpoint,\n"
        "# with links and lead-in emphasis restored (see that script).\n"
        "# Edit the biography at https://orcid.org/my-orcid instead.\n\n"
        + body
    )

    previous = OUTPUT_YML.read_text(encoding="utf-8") if OUTPUT_YML.exists() else ""
    print("Biography unchanged." if out == previous else "Biography changed.")

    if dry_run:
        print("--dry-run: not writing _data/orcid.yml")
        return 0

    OUTPUT_YML.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = OUTPUT_YML.with_suffix(".yml.tmp")
    tmp_path.write_text(out, encoding="utf-8")
    tmp_path.replace(OUTPUT_YML)
    print(f"Wrote {OUTPUT_YML}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would change, do not write the file")
    parser.add_argument("--raw", action="store_true",
                        help="write ORCID's plain text verbatim, skipping the "
                             "markdown transforms")
    args = parser.parse_args()
    return build(dry_run=args.dry_run, raw=args.raw)


if __name__ == "__main__":
    sys.exit(main())
