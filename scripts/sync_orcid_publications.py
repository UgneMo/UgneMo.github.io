#!/usr/bin/env python3
"""
Sync new publications from an ORCID record into the AcademicPages
_publications/ collection.

Runs in GitHub Actions on a schedule (see .github/workflows/sync-publications.yml).
It only writes new files into _publications/. The workflow then publishes
complete entries (ones with an author list) straight to master, and opens a
pull request for any entry ORCID gave no author list for, so a bare
"(year). Title." citation never goes live without review.

Matching against existing entries is done by DOI first, falling back to a
normalized title comparison, so a paper you already added by hand (even
with different formatting) won't get duplicated.
"""
import json
import os
import re
import unicodedata
import urllib.request
from datetime import date
from pathlib import Path

ORCID_ID = "0009-0001-6688-0042"
ORCID_API = f"https://pub.orcid.org/v3.0/{ORCID_ID}/works"
PUBLICATIONS_DIR = Path("_publications")
HEADERS = {"Accept": "application/json"}


def fetch_json(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def normalize_title(title):
    title = unicodedata.normalize("NFKD", title or "")
    title = re.sub(r"[^a-z0-9]+", " ", title.lower())
    return title.strip()


def slugify(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:80]


def load_existing():
    """Return (dois, normalized_titles) already present in _publications/."""
    dois, titles = set(), set()
    for path in PUBLICATIONS_DIR.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        m = re.search(r'^title:\s*"(.*?)"\s*$', text, re.MULTILINE)
        if m:
            titles.add(normalize_title(m.group(1)))
        for doi_match in re.finditer(r"10\.\d{4,9}/\S+", text):
            dois.add(doi_match.group(0).rstrip("\"'.,)"))
    return dois, titles


def extract_doi(summary):
    for eid in summary.get("external-ids", {}).get("external-id", []):
        if eid.get("external-id-type") == "doi":
            return eid.get("external-id-value")
    return None


def fetch_contributors(put_code):
    try:
        record = fetch_json(f"https://pub.orcid.org/v3.0/{ORCID_ID}/work/{put_code}")
    except Exception:
        return []
    names = []
    for c in record.get("contributors", {}).get("contributor", []) or []:
        name = c.get("credit-name", {}).get("value")
        if name:
            names.append(name)
    return names


CATEGORY_MAP = {
    "conference-paper": "conferences",
    "conference-abstract": "conferences",
    "conference-poster": "conferences",
    "journal-article": "manuscripts",
    "preprint": "preprints",
    "working-paper": "preprints",
}


def build_entry(summary):
    title = summary["title"]["title"]["value"]
    put_code = summary["put-code"]
    doi = extract_doi(summary)
    category = CATEGORY_MAP.get(summary.get("type", ""), "manuscripts")
    pub_date = summary.get("publication-date") or {}
    year = (pub_date.get("year") or {}).get("value") or str(date.today().year)
    month = (pub_date.get("month") or {}).get("value") or "01"
    day = (pub_date.get("day") or {}).get("value") or "01"
    iso_date = f"{year}-{int(month):02d}-{int(day):02d}"
    venue = (summary.get("journal-title") or {}).get("value") or ""
    url = f"https://doi.org/{doi}" if doi else (summary.get("url") or {}).get("value", "")

    contributors = fetch_contributors(put_code)
    if contributors:
        citation = f"{', '.join(contributors)} ({year}). {title}. {venue}."
    else:
        citation = f"({year}). {title}. {venue}."  # author list not in ORCID record

    slug = f"{iso_date}-{slugify(title)}"
    needs_review = "" if contributors else "\n<!-- TODO: ORCID had no contributor list -- fill in the author string above. -->"
    front_matter = f"""---
title: "{title}"
collection: publications
category: {category}
permalink: /publication/{slug}
excerpt: ""
date: {iso_date}
venue: "{venue}"
paperurl: "{url}"
citation: "{citation}"
---
{needs_review}
<!-- Auto-generated from ORCID on {date.today().isoformat()}. Check the
     excerpt, category, and citation formatting. -->
"""
    return slug, front_matter, bool(contributors)


def main():
    PUBLICATIONS_DIR.mkdir(exist_ok=True)
    data = fetch_json(ORCID_API)
    existing_dois, existing_titles = load_existing()
    complete, incomplete = [], []

    for group in data.get("group", []):
        for summary in group.get("work-summary", []):
            title = summary["title"]["title"]["value"]
            doi = extract_doi(summary)
            if doi and doi in existing_dois:
                continue
            if normalize_title(title) in existing_titles:
                continue

            slug, content, has_authors = build_entry(summary)
            out_path = PUBLICATIONS_DIR / f"{slug}.md"
            if out_path.exists():
                continue
            out_path.write_text(content, encoding="utf-8")
            (complete if has_authors else incomplete).append(str(out_path))
            if doi:
                existing_dois.add(doi)
            existing_titles.add(normalize_title(title))

    if complete:
        print("Ready to publish:", ", ".join(complete))
    if incomplete:
        print("Needs review (no author list):", ", ".join(incomplete))
    if not complete and not incomplete:
        print("No new publications found.")

    # Hand the two lists to the workflow (space-separated; slugs have no spaces).
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a", encoding="utf-8") as fh:
            fh.write(f"complete={' '.join(complete)}\n")
            fh.write(f"incomplete={' '.join(incomplete)}\n")


if __name__ == "__main__":
    main()

