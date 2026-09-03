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
  5. Print a diff summary against the previously committed publications.bib
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
        parts.append(f"{family}, {given}" if given else family)
    return " and ".join(parts)


def crossref_to_bibtex_entry(doi: str, message: dict[str, Any], existing_keys: set[str]) -> tuple[str, str, dict[str, str]]:
    """Return (citation_key, entry_type, fields) for one Crossref work record."""
    cr_type = message.get("type", "journal-article")
    entry_type = CROSSREF_TYPE_TO_BIBTEX.get(cr_type, "misc")

    titles = message.get("title") or [""]
    title = titles[0] if titles else ""

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
    journal = container[0] if container else ""

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
        fields["publisher"] = publisher

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

    fetched_keys: set[str] = set()
    generated: dict[str, str] = {}
    failures: list[str] = []

    for doi in dois:
        try:
            message = fetch_crossref(doi, use_cache=use_cache)
            key, entry_type, fields = crossref_to_bibtex_entry(doi, message, fetched_keys)
        except FetchError as exc:
            failures.append(f"{doi}: {exc}")
            continue
        fetched_keys.add(key)
        generated[key] = format_bibtex_entry(key, entry_type, fields)

    if failures:
        print("error: Crossref lookups failed for:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        print("publications.bib left untouched.", file=sys.stderr)
        return 1

    manual_entries = load_manual_entries()
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
