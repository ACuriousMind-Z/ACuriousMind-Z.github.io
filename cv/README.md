# CV and publication pipeline

Everything about the CV and the site's publication list lives here. The short
version: **ORCID is the source of truth for anything with a DOI, `manual.bib`
is the source of truth for everything else, and three generated files are
never hand-edited.**

## Files

| File | Hand-maintained? | What it is |
| --- | --- | --- |
| `cv.tex` | yes | The CV document. Publications are `\input` from `publications.tex`, not typed here. |
| `cv.sty` | yes | Styling. Targets the layout of `CV2026_Jianxiang_Zhao_V4.docx`, kept here as the reference. |
| `manual.bib` | yes | Works ORCID/Crossref cannot supply: no-DOI items (in-preparation manuscripts, conference presentations, patents) and corrections to Crossref records. |
| `overrides.yml` | yes | Per-entry CV section, status label, sort order, site visibility, journal badge, PDF link. |
| `orcid-import.bib` | yes | Not part of the build. A BibTeX file to upload to ORCID once, registering the no-DOI conference papers and patents. |
| `publications.bib` | **no, generated** | ORCID + Crossref, merged with `manual.bib`. |
| `publications.tex` | **no, generated** | The `\item` lines `cv.tex` inputs. |
| `../_data/publications.yml` | **no, generated** | The site's publication list. |
| `../_data/orcid.yml` | **no, generated** | ORCID biography, for the site's About Me. |
| `scripts/fetch_orcid_profile.py` | yes | Fetches the biography and restores the markdown ORCID's plain-text field cannot hold. |

## How the sync works

```
ORCID public API  ──(DOIs)──►  Crossref  ──┐
                                           ├──► publications.bib ──► publications.tex ──► cv.pdf
manual.bib  ───────────────────────────────┘                    └──► _data/publications.yml
ORCID public API  ──(biography)───────────────────────────────────►  _data/orcid.yml
```

`.github/workflows/cv-update.yml` runs weekly (Mondays 06:00 UTC), on any push
touching `cv/**`, and on manual dispatch. It has two jobs:

- **`sync-publications`** runs `fetch_orcid.py` then `render.py`. If any of the
  generated files changed it opens a PR on branch `cv/orcid-sync`. It never
  commits to `main`, so nothing from ORCID reaches the CV or the site without
  review.
- **`build-and-release-pdf`** compiles `cv.tex` from whatever is already on
  `main` and publishes `cv.pdf` to the `cv-latest` release. That is the stable
  download URL.

Locally: `make fetch`, `make render`, `make pdf`, or `make all`.

## Two rules that keep the pipeline correct

1. **A work with a DOI belongs to ORCID.** Add the DOI to your ORCID record
   (Add works → Search & link, or by DOI) and the next sync picks it up with
   Crossref's author list, volume, issue, and pages.
2. **Every entry in `publications.bib` needs a row in `overrides.yml`.** The
   lookup key is **the DOI if the entry has one, otherwise the citation key**
   (`render.py: override_key_for`). An entry with no matching row falls back to
   `defaults` and `render.py` prints a warning in the workflow log. Read those
   warnings; they are the early signal that something is misclassified.

## Runbook: an in-preparation item gets published

This is the one routine change that will produce a **duplicate entry** if
handled carelessly, because the deduplication guard matches on DOI and an
in-preparation `manual.bib` entry has none.

What goes wrong if you do nothing: you add the new DOI to ORCID, `fetch_orcid.py`
sees a DOI no `manual.bib` entry claims, fetches Crossref, and mints a *new*
citation key. The merge in `fetch_orcid.py` is by citation key, so the new
Crossref entry and the old `manual.bib` entry have different keys, no collision
fires, and both end up in the CV. `render.py` then warns that the new DOI is
unclassified and drops it into the default section.

None of that reaches `main` silently. Three things flag it:

- `fetch_orcid.py` compares every Crossref-fetched title against the
  `manual.bib` titles and reports anything above 85% similarity. The warning
  goes to the job log **and** into the sync PR's own description, so it is
  visible where you actually review.
- The sync PR diff shows the new entry added alongside the old one.
- `render.py` warns that the new DOI has no `overrides.yml` row.

The similarity check only compares against `manual.bib` entries that could
plausibly acquire a DOI (`@article`, `@inproceedings`, `@incollection`).
Patents are excluded: Crossref does not index them, and the 2020
microcapillary-films paper and its own PCT patent are 91% similar, so
including `@misc` would warn on every sync forever.

### Preferred fix: let Crossref own the record

Use this for a normal journal article. You gain the final author list, volume,
issue, pages, and the publisher's canonical title.

1. Delete the entry from `manual.bib`.
2. In `overrides.yml`, re-key that entry from its citation key to the new DOI,
   and delete its `status:` line.
3. Delete `site: false` if it had one, so the published version appears on the
   site.
4. Add the DOI to ORCID.
5. Re-run the workflow (Actions → CV update → Run workflow) and merge the PR.

Before:

```yaml
  zhao2026acsami:
    section: first_author
    status: "Under review"
    conference_short: "ACS AMI"
    sort: 2026.5
```

After:

```yaml
  10.1021/acsami.6xxxxxx:
    section: first_author
    conference_short: "ACS AMI"
    sort: 2026.5
```

**Step 2 is the step people skip.** Because `override_key_for` prefers the DOI
whenever an entry has one, leaving the old citation key in `overrides.yml` means
the entry silently falls back to `defaults` and loses its curated
`conference_short` and section. This has already happened once in this repo.

### Alternative fix: keep the hand-written record

Use this only when Crossref's record is worse than yours (it was, for the 2023
book chapter). Add a `doi = {...}` field to the existing `manual.bib` entry
instead of deleting it. `fetch_orcid.py` then skips that DOI entirely, so no
second entry is generated. **You still have to do step 2**, for the same reason:
the entry now has a DOI, so that is the key `overrides.yml` is read by.

## Runbook: a brand-new work appears on ORCID

Nothing to do in `manual.bib`. The sync PR will contain the new entry and the
job log will warn that it is unclassified. Add a row to `overrides.yml` keyed by
its DOI, push it to the PR branch, and merge.

## Runbook: a work has no DOI and never will

Conference presentations, patents, in-preparation manuscripts. Add a
`manual.bib` entry plus an `overrides.yml` row keyed by its citation key. ORCID
is free to also carry the item (see below); `fetch_orcid.py` skips ORCID works
with no DOI, so there is no duplication risk.

## Runbook: importing the no-DOI works into ORCID

`orcid-import.bib` exists for this. Upload it at orcid.org/my-orcid ->
Works -> Add -> Import BibTeX. Afterwards, change the two patents from work
type "Other" to "Patent" in the ORCID UI: `@patent` is not standard BibTeX and
is not in ORCID's supported type list, so they are written as `@misc`, which
imports reliably.

Two things will make ORCID reject the file with
`TypeError: Token mismatch: match`:

- **An at-sign inside a `%` comment.** ORCID's BibTeX reader finds entries by
  scanning for the at-sign and does not honour `%` comments, so `@misc` written
  in a comment is parsed as the start of a malformed entry. This is what broke
  the first version of the file.
- **Unbalanced braces** in any field value.

Validate before uploading, against the same parser ORCID uses:

```sh
npm install bibtex-parse-js
node -e 'console.log(require("bibtex-parse-js").toJSON(require("fs").readFileSync("cv/orcid-import.bib","utf8")).length + " entries")'
```

Re-importing the file creates duplicates in ORCID. Import once, then edit in
place.

## The biography transforms

ORCID stores the biography as plain text, so two things the site had before
would be lost verbatim: the link to the lab, and the sentences that open the
research-direction paragraphs and read as sub-headings.
`fetch_orcid_profile.py` restores both. Neither transform adds, removes, or
reorders a word.

- **`AUTOLINKS`** turns a listed phrase into a markdown link, first occurrence
  only, skipping any phrase that is already linked. Keep the list short and
  specific.
- **`emphasize_lead_in`** bolds a paragraph's opening sentence, but only when
  all of: the paragraph continues after it; it is at most 80 characters; it is
  at least 3 words; it contains exactly one period, its own terminator; it
  contains no first-person pronoun; and the paragraph carries no markdown
  emphasis already. The single-period and word-count rules together are what
  stop a leading abbreviation such as "Prof. Chul B. Park." from being read as
  a heading.

Both are deliberately conservative: when a rule does not clearly apply, the
text passes through untouched. Markdown you write in the ORCID biography
yourself is never overwritten, so `**bolding a line**` there is the way to
override the heuristic. Run `python3 cv/scripts/fetch_orcid_profile.py --raw`
to see what ORCID returned before any transform.

## What is deliberately not synced

- **Teaching, mentoring, and professional service.** ORCID has no work type for
  these, and its BibTeX import only feeds the Works section. They stay in
  `cv.tex`.
- **Peer review.** ORCID's peer-review section is populated by publisher
  integrations (Web of Science Reviewer Recognition), not by anything this
  pipeline can write. The reviewer count in `cv.tex` is maintained by hand.
- **Employment and education.** Short, stable, and already correct in `cv.tex`.

Writing to ORCID at all would require an OAuth member token; this pipeline only
reads the public API.
