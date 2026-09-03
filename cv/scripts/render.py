#!/usr/bin/env python3
"""Render cv/publications.bib + cv/overrides.yml into:

  1. cv/publications.tex        -- \\item lines for cv.tex, grouped by
                                    section, continuously numbered.
  2. _data/publications.yml     -- the site's publication list (repo root).

Both outputs are committed generated files; re-run this after editing
manual.bib or overrides.yml (or after `fetch_orcid.py` updates
publications.bib). Do not hand-edit either output.

Standalone usage:
    python3 render.py
"""
from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

import bibtexparser
import yaml

CV_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = CV_DIR.parent
PUBLICATIONS_BIB = CV_DIR / "publications.bib"
OVERRIDES_YML = CV_DIR / "overrides.yml"
OUTPUT_TEX = CV_DIR / "publications.tex"
OUTPUT_SITE_YML = REPO_ROOT / "_data" / "publications.yml"

# Name variants that get bolded in every rendered author list.
BOLD_NAME = ("Zhao", "Jianxiang")  # (family, given/initial family this matches)

SECTION_ORDER = ["first_author", "coauthored", "conference", "patent", "thesis"]
SECTION_TITLES = {
    "first_author": "First-Authored Publications",
    "coauthored": "Co-Authored Publications",
    "conference": "Conference Presentations",
    "patent": "Patents",
    "thesis": "Thesis",
}
# Only these sections feed the site's public publication list, matching what
# was already shown on jxzhao.com before this pipeline existed (conference
# talks and patents are CV-only).
SITE_SECTIONS = {"first_author", "coauthored"}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_bib_entries() -> list[dict[str, str]]:
    if not PUBLICATIONS_BIB.exists():
        print(f"error: {PUBLICATIONS_BIB} does not exist. Run fetch_orcid.py first.", file=sys.stderr)
        sys.exit(1)
    text = PUBLICATIONS_BIB.read_text(encoding="utf-8")
    db = bibtexparser.loads(text)
    if not db.entries:
        print(f"error: {PUBLICATIONS_BIB} contains no entries.", file=sys.stderr)
        sys.exit(1)
    return db.entries


def load_overrides() -> dict[str, Any]:
    if not OVERRIDES_YML.exists():
        print(f"error: {OVERRIDES_YML} does not exist.", file=sys.stderr)
        sys.exit(1)
    with OVERRIDES_YML.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    data.setdefault("defaults", {})
    data["defaults"].setdefault("section", "coauthored")
    data["defaults"].setdefault("site", True)
    data.setdefault("entries", {})
    return data


def override_key_for(entry: dict[str, str]) -> str:
    doi = entry.get("doi", "").strip().lower()
    return doi if doi else entry["ID"]


def classify(entries: list[dict[str, str]], overrides: dict[str, Any]) -> list[dict[str, Any]]:
    defaults = overrides["defaults"]
    table = overrides["entries"]
    classified = []
    unclassified = []
    for entry in entries:
        key = override_key_for(entry)
        rule = table.get(key)
        if rule is None:
            unclassified.append(key)
            rule = {}
        section = rule.get("section", defaults.get("section"))
        site = rule.get("site", defaults.get("site"))
        status = rule.get("status")
        sort = rule.get("sort")
        if sort is None:
            try:
                sort = float(entry.get("year", "0") or "0")
            except ValueError:
                sort = 0.0
        classified.append({
            "entry": entry,
            "override_key": key,
            "section": section,
            "site": site,
            "status": status,
            "sort": float(sort),
        })
    if unclassified:
        print("warning: the following publications.bib entries are not in overrides.yml "
              f"and fell back to the default section ({defaults.get('section')}):", file=sys.stderr)
        for key in unclassified:
            print(f"  - {key}", file=sys.stderr)
    return classified


# ---------------------------------------------------------------------------
# Shared formatting helpers
# ---------------------------------------------------------------------------

def _is_self(family: str, given: str) -> bool:
    family = family.strip().lower()
    given = given.strip().lower().rstrip(".")
    if family != "zhao":
        return False
    return given in ("jianxiang", "j")


def parse_authors(author_field: str) -> list[tuple[str, str]]:
    """'Family, Given and Family2, Given2 and others' -> [(family, given), ...].

    A literal 'others' token (BibTeX's own et al. convention) is kept as a
    sentinel ('', '') so callers can render it as "et al.".
    """
    parts = [p.strip() for p in author_field.split(" and ") if p.strip()]
    authors = []
    for part in parts:
        if part.lower() == "others":
            authors.append(("", ""))
            continue
        if "," in part:
            family, given = part.split(",", 1)
        else:
            tokens = part.rsplit(" ", 1)
            given, family = (tokens[0], tokens[1]) if len(tokens) == 2 else ("", part)
        authors.append((family.strip(), given.strip()))
    return authors


LATEX_ESCAPES = {
    "&": r"\&", "%": r"\%", "#": r"\#", "_": r"\_",
    "$": r"\$", "{": r"\{", "}": r"\}",
}


def escape_latex(text: str) -> str:
    return re.sub(r"[&%#_${}]", lambda m: LATEX_ESCAPES[m.group()], text or "")


def authors_display_tex(author_field: str) -> str:
    rendered = []
    for family, given in parse_authors(author_field):
        if not family and not given:
            rendered.append("et al.")
            continue
        name = f"{given} {family}".strip()
        name = escape_latex(name)
        if _is_self(family, given):
            name = f"\\textbf{{{name}}}"
        rendered.append(name)
    return ", ".join(rendered)


def authors_display_html(author_field: str) -> str:
    rendered = []
    for family, given in parse_authors(author_field):
        if not family and not given:
            rendered.append("et al.")
            continue
        name = f"{given} {family}".strip()
        if _is_self(family, given):
            name = f"<strong>{name}</strong>"
        rendered.append(name)
    return ", ".join(rendered)


def year_of(entry: dict[str, str]) -> str:
    return entry.get("year", "").strip()


def normalize_pages(pages: str) -> str:
    """'15491-15498' -> '15491--15498' (LaTeX en-dash convention)."""
    return re.sub(r"(?<=\d)-(?=\d)", "--", pages or "")


def with_period(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    return text if text.endswith((".", "!", "?")) else text + "."


# ---------------------------------------------------------------------------
# publications.tex
# ---------------------------------------------------------------------------

def format_citation_tex(entry: dict[str, str], status: str | None) -> str:
    authors = authors_display_tex(entry.get("author", ""))
    title = escape_latex(entry.get("title", "").strip())
    year = year_of(entry)
    entry_type = entry["ENTRYTYPE"]

    pieces = [with_period(authors), with_period(title)]

    if entry_type == "article":
        journal = escape_latex(entry.get("journal", ""))
        journal_part = f"\\textit{{{journal}}}" if journal else ""
        vol_issue = ""
        if entry.get("volume"):
            vol_issue = entry["volume"]
            if entry.get("number"):
                vol_issue += f"({entry['number']})"
        pages = normalize_pages(entry.get("pages", ""))
        tail_bits = [b for b in [year, vol_issue, pages] if b]
        tail = ", ".join(tail_bits)
        cite = f"{journal_part}, {tail}." if journal_part and tail else f"{journal_part}{tail}."
        pieces.append(cite)
    elif entry_type == "inproceedings":
        booktitle = escape_latex(entry.get("booktitle", ""))
        address = escape_latex(entry.get("address", ""))
        tail_bits = [b for b in [booktitle, address, year] if b]
        pieces.append(", ".join(tail_bits) + ".")
    elif entry_type == "incollection":
        booktitle = escape_latex(entry.get("booktitle", ""))
        publisher = escape_latex(entry.get("publisher", ""))
        edition = entry.get("edition", "")
        edition_str = f"{edition} ed., " if edition else ""
        pages = normalize_pages(entry.get("pages", ""))
        pages_str = f"pp. {pages}, " if pages else ""
        note = escape_latex(entry.get("note", ""))
        pieces.append(f"In \\textit{{{booktitle}}}, {publisher}, {edition_str}{pages_str}{year}."
                       + (f" {note}." if note else ""))
    else:  # misc (patents) and anything else
        howpublished = escape_latex(entry.get("howpublished", ""))
        tail_bits = [b for b in [howpublished, year] if b]
        pieces.append(", ".join(tail_bits) + ".")

    doi = entry.get("doi", "").strip()
    if doi:
        # \url (from the `url` package, loaded by hyperref) breaks the link
        # at "/" so long DOIs don't cause overfull hboxes the way a plain
        # \href{...}{...} with the URL as visible text would.
        pieces.append(f"\\url{{https://doi.org/{doi}}}")
    if status:
        pieces.append(f"\\textit{{({escape_latex(status)})}}")

    return " ".join(p for p in pieces if p)


def render_tex(classified: list[dict[str, Any]]) -> str:
    lines = [
        "% publications.tex -- GENERATED by cv/scripts/render.py",
        "% Do not hand-edit. Source data: publications.bib + overrides.yml.",
        "% Every \\begin{pubcounter} resumes the SAME named counter series",
        "% (defined once in cv.sty), so numbering stays continuous across",
        "% every section below, in the order they appear here.",
        "",
    ]
    for section in SECTION_ORDER:
        items = [c for c in classified if c["section"] == section]
        if not items:
            continue
        items.sort(key=lambda c: c["sort"], reverse=True)
        lines.append(f"\\subsection*{{{SECTION_TITLES[section]}}}")
        lines.append("\\begin{pubcounter}")
        for item in items:
            citation = format_citation_tex(item["entry"], item["status"])
            lines.append(f"\\item {citation}")
        lines.append("\\end{pubcounter}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# _data/publications.yml
# ---------------------------------------------------------------------------

def yaml_str(value: str) -> str:
    return yaml.safe_dump(value, allow_unicode=True, default_style='"', width=float("inf")).strip()


def render_site_yaml(classified: list[dict[str, Any]], overrides: dict[str, Any]) -> str:
    # Grouped by section (first-authored block, then co-authored block) in
    # SECTION_ORDER, matching how the list already read on the site;
    # descending by sort within each section.
    items: list[dict[str, Any]] = []
    for section in SECTION_ORDER:
        if section not in SITE_SECTIONS:
            continue
        section_items = [c for c in classified if c["section"] == section and c["site"]]
        section_items.sort(key=lambda c: c["sort"], reverse=True)
        items.extend(section_items)

    lines = [
        "# _data/publications.yml -- GENERATED by cv/scripts/render.py",
        "# Do not hand-edit. Source data: cv/publications.bib + cv/overrides.yml.",
        "# Regenerate with: python3 cv/scripts/render.py",
        "",
        "main:",
        "",
    ]
    for item in items:
        entry = item["entry"]
        key = item["override_key"]
        rule = overrides["entries"].get(key, {})

        title = entry.get("title", "").strip()
        authors_html = authors_display_html(entry.get("author", ""))
        entry_type = entry["ENTRYTYPE"]

        if entry_type == "article":
            journal = entry.get("journal", "")
            year = year_of(entry)
            vol_issue = entry.get("volume", "")
            if entry.get("number"):
                vol_issue += f"({entry['number']})"
            tail = ", ".join(b for b in [year, vol_issue, entry.get("pages", "")] if b)
            conference = f"{journal}, {tail}." if tail else f"{journal}."
        elif entry_type == "incollection":
            booktitle = entry.get("booktitle", "")
            publisher = entry.get("publisher", "")
            edition = entry.get("edition", "")
            pages = entry.get("pages", "")
            note = entry.get("note", "")
            conference = (f"Chapter in <em>{booktitle}</em>, {publisher}, "
                           f"{edition} ed., {year_of(entry)}, pp. {pages}."
                           + (f" {note}." if note else ""))
        else:
            booktitle = entry.get("booktitle", "")
            address = entry.get("address", "")
            tail = ", ".join(b for b in [booktitle, address, year_of(entry)] if b)
            conference = f"{tail}."

        conference_short = rule.get("conference_short") or journal_abbrev_fallback(entry)

        lines.append(f"- title: {yaml_str(title)}")
        lines.append(f"  authors: {yaml_str(authors_html)}")
        if conference_short:
            lines.append(f"  conference_short: {yaml_str(conference_short)}")
        lines.append(f"  conference: {yaml_str(conference)}")
        doi = entry.get("doi", "").strip()
        if doi:
            lines.append(f"  doi: {yaml_str('https://doi.org/' + doi)}")
        pdf = rule.get("pdf")
        if pdf:
            lines.append(f"  pdf: {yaml_str(pdf)}")
        if item["status"]:
            lines.append(f"  notes: {yaml_str(item['status'])}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def journal_abbrev_fallback(entry: dict[str, str]) -> str:
    return entry.get("journal", "") or entry.get("booktitle", "")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    entries = load_bib_entries()
    overrides = load_overrides()
    classified = classify(entries, overrides)

    tex_out = render_tex(classified)
    OUTPUT_TEX.write_text(tex_out, encoding="utf-8")
    print(f"Wrote {OUTPUT_TEX} ({sum(1 for c in classified)} entries).")

    yml_out = render_site_yaml(classified, overrides)
    OUTPUT_SITE_YML.write_text(yml_out, encoding="utf-8")
    site_count = sum(1 for c in classified if c["section"] in SITE_SECTIONS and c["site"])
    print(f"Wrote {OUTPUT_SITE_YML} ({site_count} entries).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
