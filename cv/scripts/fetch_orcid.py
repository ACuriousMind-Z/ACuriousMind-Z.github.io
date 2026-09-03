#!/usr/bin/env python3
"""Fetch works from ORCID + Crossref and (re)build cv/publications.bib.

Pipeline:
  1. GET the public ORCID works list and pull a DOI out of each work's
     external-ids (works without a DOI are skipped -- they cannot be
     enriched from Crossref and belong in manual.bib instead).
  2. GET each DOI from Crossref, which gives a clean, consistent author
     list, journal title, volume/issue/pages that the ORCID summary does
     not reliably carry.
  3. Emit one BibTeX entry per DOI.
  4. Parse manual.bib and merge it on top by citation key -- manual.bib
     always wins on a key collision, so hand corrections survive reruns.
     A manual.bib entry may also declare its own `doi` field once that
     work appears on ORCID; such DOIs are skipped entirely in step 2 so
     the same work never gets a second, separately-keyed entry.
  5. Flag any generated entry whose title closely matches a manual.bib
     entry. That is the signature of a work that used to have no DOI and
     has since been published: the DOI-based skip in step 4 cannot catch
     it, and the by-key merge lets both copies through. See the runbook in
     cv/README.md.
  6. Print a diff summary against the previously committed publications.bib
     (added / removed / changed keys) and write the new file atomically.

If ORCID is unreachable, or any step fails, this script exits non-zero
*without* touching the committed publications.bib -- never write a
truncated/partial file.

Standalone usage:
    python3 fetch_orcid.py                # normal run, uses/updates cache
    python3 fetch_orcid.py --refresh-cache # ignore cached Crossref responses
    python3 fetch_orcid.py --dry-run       # print the diff, don't write

Requires: requests, bibtexparser (see requirements.txt).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import requests

try:
    import bibtexparser
except ImportError:  # pragma: no cover
    print("error: bibtexparser is required (pip install -r cv/requirements.txt)", file=sys.stderr)
    sys.exit(1)

ORCID_ID = "0000-0002-3453-2254"
ORCID_WORKS_URL = f"https://pub.orcid.org/v3.0/{ORCID_ID}/works"
CROSSREF_URL_TMPL = "https://api.crossref.org/works/{doi}?mailto={email}"
CONTACT_EMAIL = "hello@jxzhao.com"
USER_AGENT = f"cv-pipeline/1.0 (https://jxzhao.com; mailto:{CONTACT_EMAIL})"

CV_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = CV_DIR / ".cache"
MANUAL_BIB = CV_DIR / "manual.bib"
OUTPUT_BIB = CV_DIR / "publications.bib"
# Machine-readable warnings for the workflow to fold into the sync PR body.
# Under .cache/, which is gitignored.
SYNC_REPORT = CACHE_DIR / "sync-report.md"

REQUEST_TIMEOUT = 20
MAX_RETRIES = 5
BACKOFF_BASE = 2.0  # seconds; doubles each retry


class FetchError(RuntimeError):
    """Raised for any failure that must abort the run without writing output."""


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get_with_backoff(url: str, headers: dict[str, str]) -> requests.Response:
    delay = BACKOFF_BASE
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            last_exc = exc
            resp = None
        if resp is not None:
            if resp.status_code == 200:
                return resp
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
                if attempt < MAX_RETRIES:
                    time.sleep(wait)
                    delay *= 2
                    continue
                raise FetchError(f"{url} kept failing with HTTP {resp.status_code} after {MAX_RETRIES} attempts")
            # Non-retryable HTTP error.
            raise FetchError(f"{url} returned HTTP {resp.status_code}: {resp.text[:300]}")
        # Network-level failure; retry with backoff.
        if attempt < MAX_RETRIES:
            time.sleep(delay)
            delay *= 2
            continue
        raise FetchError(f"{url} unreachable after {MAX_RETRIES} attempts: {last_exc}")
    raise FetchError(f"{url} unreachable")  # unreachable in practice


def fetch_orcid_dois() -> list[str]:
    """Return the list of DOIs found in the ORCID public works record."""
    try:
        resp = _get_with_backoff(ORCID_WORKS_URL, {"Accept": "application/json", "User-Agent": USER_AGENT})
    except FetchError as exc:
        raise FetchError(f"ORCID unreachable: {exc}") from exc

    try:
        data = resp.json()
    except ValueError as exc:
        raise FetchError(f"ORCID returned invalid JSON: {exc}") from exc

    dois: list[str] = []
    for group in data.get("group", []):
        found_doi = None
        for summary in group.get("work-summary", []):
            for ext_id in (summary.get("external-ids") or {}).get("external-id", []):
                if ext_id.get("external-id-type") == "doi":
                    found_doi = ext_id.get("external-id-value")
                    break
            if found_doi:
                break
        if found_doi:
            dois.append(found_doi.strip().lower())
    seen = set()
    unique = []
    for doi in dois:
        if doi not in seen:
            seen.add(doi)
            unique.append(doi)
    return unique


def _cache_path(doi: str) -> Path:
    digest = hashlib.sha1(doi.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}.json"


def fetch_crossref(doi: str, use_cache: bool = True) -> dict[str, Any]:
    cache_file = _cache_path(doi)
    if use_cache and cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass  # fall through to a live fetch

    url = CROSSREF_URL_TMPL.format(doi=doi, email=CONTACT_EMAIL)
    resp = _get_with_backoff(url, {"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        message = resp.json()["message"]
    except (ValueError, KeyError) as exc:
        raise FetchError(f"Crossref returned unexpected payload for {doi}: {exc}") from exc

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(message, indent=2, sort_keys=True), encoding="utf-8")
    time.sleep(0.15)  # be polite to Crossref between calls
    return message


# ---------------------------------------------------------------------------
# BibTeX construction
# ---------------------------------------------------------------------------

CROSSREF_TYPE_TO_BIBTEX = {
    "journal-article": "article",
    "proceedings-article": "inproceedings",
    "book-chapter": "incollection",
    "book": "book",
    "posted-content": "unpublished",
    "report": "techreport",
}


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _slug(text: str) -> str:
    text = _strip_accents(text).lower()
    return re.sub(r"[^a-z0-9]", "", text)


_CROSSREF_MARKUP_RE = re.compile(r"</?(?:i|b|em|strong|sub|sup|scp)\b[^>]*>", re.IGNORECASE)


def clean_crossref_text(text: str) -> str:
    """Strip inline JATS/HTML markup Crossref sometimes embeds in text fields
    (e.g. "<i>via</i>") and collapse the whitespace/newlines left behind."""
    text = _CROSSREF_MARKUP_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def make_citation_key(family_name: str, year: str, journal: str, existing_keys: set[str]) -> str:
    base = f"{_slug(family_name) or 'anon'}{year or 'nd'}{_slug(journal)[:6]}"
    key = base
    suffix = ord("a")
    while key in existing_keys:
        key = f"{base}{chr(suffix)}"
        suffix += 1
    return key


def crossref_authors_to_bibtex(authors: list[dict[str, Any]]) -> str:
    parts = []
    for author in authors:
        family = author.get("family", "").strip()
        given = author.get("given", "").strip()
        if not family and not given:
            continue
        family = clean_crossref_text(family)
        given = clean_crossref_text(given)
        parts.append(f"{family}, {given}" if given else family)
    return " and ".join(parts)


def crossref_to_bibtex_entry(doi: str, message: dict[str, Any], existing_keys: set[str]) -> tuple[str, str, dict[str, str]]:
    """Return (citation_key, entry_type, fields) for one Crossref work record."""
    cr_type = message.get("type", "journal-article")
    entry_type = CROSSREF_TYPE_TO_BIBTEX.get(cr_type, "misc")

    titles = message.get("title") or [""]
    title = clean_crossref_text(titles[0] if titles else "")

    date_parts = (
        message.get("published-print", {}).get("date-parts")
        or message.get("published-online", {}).get("date-parts")
        or message.get("issued", {}).get("date-parts")
        or [[""]]
    )
    year = str(date_parts[0][0]) if date_parts and date_parts[0] else ""

    authors = message.get("author", [])
    author_field = crossref_authors_to_bibtex(authors)
    family_for_key = authors[0].get("family", "") if authors else ""

    container = message.get("container-title") or [""]
    journal = clean_crossref_text(container[0] if container else "")

    fields: dict[str, str] = {
        "title": title,
        "author": author_field,
        "year": year,
        "doi": doi,
    }
    if journal:
        fields["journal" if entry_type == "article" else "booktitle"] = journal
    if message.get("volume"):
        fields["volume"] = message["volume"]
    if message.get("issue"):
        fields["number"] = message["issue"]
    if message.get("page"):
        fields["pages"] = message["page"]
    publisher = message.get("publisher")
    if publisher and entry_type != "article":
        fields["publisher"] = clean_crossref_text(publisher)

    key = make_citation_key(family_for_key, year, journal, existing_keys)
    return key, entry_type, fields


BIBTEX_FIELD_ORDER = [
    "author", "title", "journal", "booktitle", "year", "volume", "number",
    "pages", "publisher", "doi", "note", "howpublished", "school", "address",
]


def format_bibtex_entry(key: str, entry_type: str, fields: dict[str, str]) -> str:
    lines = [f"@{entry_type}{{{key},"]
    ordered = [f for f in BIBTEX_FIELD_ORDER if f in fields]
    ordered += [f for f in fields if f not in ordered]
    for i, field in enumerate(ordered):
        value = fields[field].replace("{", "").replace("}", "")
        comma = "," if i < len(ordered) - 1 else ""
        lines.append(f"  {field} = {{{value}}}{comma}")
    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

# Above this title similarity, a generated entry and a manual.bib entry are
# reported as probably the same work. Tuned to be noisy rather than silent: a
# false positive costs one glance at the PR, a false negative puts a duplicate
# publication in the CV and on the site.
TITLE_MATCH_THRESHOLD = 0.85

# Only manual.bib entries of these types are compared. The risk being detected
# is a manual entry that later acquires a DOI, and Crossref indexes journal
# articles, proceedings papers and book chapters -- not patents, which are what
# @misc holds here. Excluding @misc is also what keeps the standing 91% match
# between the 2020 microcapillary-films paper and its own PCT patent, two
# legitimately separate CV entries, from warning on every single sync.
DUPLICATE_CHECK_TYPES = {"article", "inproceedings", "incollection"}


def normalize_title(title: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", _strip_accents(title).lower()).split())


def find_probable_duplicates(
    generated_titles: dict[str, tuple[str, str]],
    manual_entries: dict[str, tuple[str, dict[str, str]]],
) -> list[tuple[str, str, str, str, float]]:
    """Return (generated_key, doi, generated_title, manual_key, ratio) for every
    Crossref-generated entry that looks like an existing manual.bib entry.

    Compares normalized titles rather than DOIs on purpose: an entry that shares
    a DOI with manual.bib was already skipped upstream, so the case left to
    catch is exactly the one with no DOI on the manual side.
    """
    manual_titles = {
        key: normalize_title(fields.get("title", ""))
        for key, (entry_type, fields) in manual_entries.items()
        if fields.get("title", "").strip() and entry_type in DUPLICATE_CHECK_TYPES
    }
    hits = []
    for gen_key, (doi, title) in generated_titles.items():
        norm = normalize_title(title)
        if not norm:
            continue
        for man_key, man_norm in manual_titles.items():
            if not man_norm:
                continue
            ratio = SequenceMatcher(None, norm, man_norm).ratio()
            if ratio >= TITLE_MATCH_THRESHOLD:
                hits.append((gen_key, doi, title, man_key, ratio))
    hits.sort(key=lambda h: h[4], reverse=True)
    return hits


def format_duplicate_report(hits: list[tuple[str, str, str, str, float]]) -> str:
    lines = [
        "### Possible duplicate publications",
        "",
        "A Crossref entry fetched from ORCID closely matches an entry that is "
        "still in `cv/manual.bib`. That normally means a work with no DOI has "
        "been published and now has one, so the CV and the site would list it "
        "twice. Fix it before merging: see "
        "\"Runbook: an in-preparation item gets published\" in `cv/README.md`.",
        "",
    ]
    for gen_key, doi, title, man_key, ratio in hits:
        lines.append(f"- **{title}**")
        lines.append(f"  - from ORCID as `{gen_key}` (DOI `{doi}`)")
        lines.append(f"  - matches `cv/manual.bib` entry `{man_key}` "
                     f"({ratio:.0%} title similarity)")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# manual.bib merge
# ---------------------------------------------------------------------------

def load_manual_entries() -> dict[str, tuple[str, dict[str, str]]]:
    if not MANUAL_BIB.exists():
        return {}
    text = MANUAL_BIB.read_text(encoding="utf-8")
    db = bibtexparser.loads(text)
    entries: dict[str, tuple[str, dict[str, str]]] = {}
    for entry in db.entries:
        key = entry["ID"]
        entry_type = entry["ENTRYTYPE"]
        fields = {k: v for k, v in entry.items() if k not in ("ID", "ENTRYTYPE")}
        entries[key] = (entry_type, fields)
    return entries


def load_existing_bib(path: Path) -> dict[str, str]:
    """Key -> full raw entry text, for diffing. Empty dict if file is absent/empty."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    db = bibtexparser.loads(text)
    result = {}
    for entry in db.entries:
        key = entry["ID"]
        # Re-serialize deterministically so formatting differences don't
        # show up as false positives in the diff.
        entry_type = entry["ENTRYTYPE"]
        fields = {k: v for k, v in entry.items() if k not in ("ID", "ENTRYTYPE")}
        result[key] = format_bibtex_entry(key, entry_type, fields)
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build(dry_run: bool, use_cache: bool, verbose: bool = True) -> int:
    try:
        dois = fetch_orcid_dois()
    except FetchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("publications.bib left untouched.", file=sys.stderr)
        return 1

    if verbose:
        print(f"ORCID: found {len(dois)} work(s) with a DOI.")

    manual_entries = load_manual_entries()
    manual_dois = {
        fields["doi"].strip().lower()
        for _entry_type, fields in manual_entries.values()
        if fields.get("doi", "").strip()
    }

    fetched_keys: set[str] = set()
    generated: dict[str, str] = {}
    generated_titles: dict[str, tuple[str, str]] = {}
    failures: list[str] = []
    skipped_manual = [doi for doi in dois if doi in manual_dois]

    for doi in dois:
        if doi in manual_dois:
            continue
        try:
            message = fetch_crossref(doi, use_cache=use_cache)
            key, entry_type, fields = crossref_to_bibtex_entry(doi, message, fetched_keys)
        except FetchError as exc:
            failures.append(f"{doi}: {exc}")
            continue
        fetched_keys.add(key)
        generated[key] = format_bibtex_entry(key, entry_type, fields)
        generated_titles[key] = (doi, fields.get("title", ""))

    if failures:
        print("error: Crossref lookups failed for:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        print("publications.bib left untouched.", file=sys.stderr)
        return 1

    merged: dict[str, str] = dict(generated)
    overridden = [k for k in manual_entries if k in generated]
    for key, (entry_type, fields) in manual_entries.items():
        merged[key] = format_bibtex_entry(key, entry_type, fields)

    previous = load_existing_bib(OUTPUT_BIB)
    added = sorted(set(merged) - set(previous))
    removed = sorted(set(previous) - set(merged))
    changed = sorted(k for k in set(merged) & set(previous) if merged[k] != previous[k])

    print(f"Fetched {len(generated)} entr{'y' if len(generated) == 1 else 'ies'} from ORCID/Crossref, "
          f"{len(manual_entries)} from manual.bib ({len(overridden)} override collision(s)).")
    if skipped_manual:
        print(f"Skipped {len(skipped_manual)} ORCID DOI(s) already claimed by a manual.bib entry:")
        for doi in skipped_manual:
            print(f"  - {doi}")

    duplicates = find_probable_duplicates(generated_titles, manual_entries)
    if duplicates:
        print("", file=sys.stderr)
        print(f"WARNING: {len(duplicates)} possible duplicate(s) -- a Crossref entry "
              "closely matches a manual.bib entry:", file=sys.stderr)
        for gen_key, doi, title, man_key, ratio in duplicates:
            print(f"  - {gen_key} ({doi}) ~ manual.bib {man_key} "
                  f"[{ratio:.0%} title match]", file=sys.stderr)
            print(f"      {title}", file=sys.stderr)
        print("  This is what an in-preparation entry getting published looks like. "
              "See the runbook in cv/README.md before merging.", file=sys.stderr)
        print("", file=sys.stderr)

    if not dry_run:
        SYNC_REPORT.parent.mkdir(parents=True, exist_ok=True)
        SYNC_REPORT.write_text(format_duplicate_report(duplicates) if duplicates else "",
                               encoding="utf-8")
    print(f"Diff vs committed publications.bib: +{len(added)} added, -{len(removed)} removed, "
          f"~{len(changed)} changed.")
    for key in added:
        print(f"  + {key}")
    for key in removed:
        print(f"  - {key}")
    for key in changed:
        print(f"  ~ {key}")

    if dry_run:
        print("--dry-run: not writing publications.bib")
        return 0

    header = (
        "% publications.bib -- GENERATED by cv/scripts/fetch_orcid.py\n"
        "% Do not hand-edit. Sources: ORCID public API + Crossref, merged with\n"
        "% manual.bib (manual.bib wins on citation-key collision).\n"
        f"% Regenerate with: python3 cv/scripts/fetch_orcid.py\n\n"
    )
    body = "\n\n".join(merged[k] for k in sorted(merged))
    tmp_path = OUTPUT_BIB.with_suffix(".bib.tmp")
    tmp_path.write_text(header + body + "\n", encoding="utf-8")
    tmp_path.replace(OUTPUT_BIB)
    print(f"Wrote {OUTPUT_BIB} ({len(merged)} entries).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print the diff, do not write publications.bib")
    parser.add_argument("--refresh-cache", action="store_true", help="ignore cached Crossref responses")
    args = parser.parse_args()
    return build(dry_run=args.dry_run, use_cache=not args.refresh_cache)


if __name__ == "__main__":
    sys.exit(main())
