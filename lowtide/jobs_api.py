"""Fetches job listings from JSearch (RapidAPI), which pulls from Google for
Jobs: Indeed, LinkedIn, Glassdoor, ZipRecruiter, company career sites, etc."""
import datetime as dt
import os
import re

import requests

HOST = "jsearch.p.rapidapi.com"

_ANNUAL = {"YEAR": 1, "HOUR": 2080, "MONTH": 12, "WEEK": 52, "DAY": 260}

# Job boards / aggregators. An apply link NOT on one of these is probably the
# company's own site, which is what we prefer to send you to.
_AGGREGATORS = (
    "indeed.", "linkedin.", "glassdoor.", "ziprecruiter.", "monster.",
    "simplyhired.", "careerbuilder.", "talent.com", "jooble.", "dice.com",
    "snagajob.", "lensa.", "jobright.", "builtin.", "wellfound.", "adzuna.",
    "salary.com", "learn4good.", "teal", "bebee.", "jobgether.", "flexjobs.",
)


def search(query, date_posted="3days"):
    key = os.environ.get("RAPIDAPI_KEY", "").strip()
    if not key:
        raise RuntimeError("RAPIDAPI_KEY isn't set.")
    headers = {"X-RapidAPI-Key": key, "X-RapidAPI-Host": HOST}
    params = {
        "query": query,
        "num_pages": 1,
        "country": "us",
        "date_posted": date_posted,
        "work_from_home": "true",
    }
    # JSearch v5 moved search to /search-v2. Fall back to the old path just in case.
    r = None
    for path in ("/search-v2", "/search"):
        r = requests.get(f"https://{HOST}{path}", headers=headers, params=params, timeout=90)
        if r.status_code != 404:
            break
    if r.status_code == 429:
        raise RuntimeError("RapidAPI says you've hit your monthly request limit.")
    if r.status_code in (401, 403):
        raise RuntimeError("RapidAPI rejected the key. Check RAPIDAPI_KEY and that you're subscribed to JSearch.")
    r.raise_for_status()
    data = r.json().get("data")
    # v5 returns {"data": {"jobs": [...], "cursor": ...}}; older versions return {"data": [...]}
    if isinstance(data, dict):
        data = data.get("jobs") or []
    return data or []


def _is_aggregator(url):
    u = (url or "").lower()
    return any(a in u for a in _AGGREGATORS)


def _money(n):
    return f"${n/1000:.0f}k" if n >= 1000 else f"${n:,.0f}"


def _dedupe_key(title, company):
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
    return f"{norm(title)}|{norm(company)}"[:600]


def normalize(raw, query=""):
    title = raw.get("job_title") or "Untitled role"
    company = raw.get("employer_name") or "Unknown company"

    # Pay, converted to yearly so everything is comparable.
    smin, smax = raw.get("job_min_salary"), raw.get("job_max_salary")
    period = (raw.get("job_salary_period") or "YEAR").upper()
    mult = _ANNUAL.get(period, 1)
    amin = smin * mult if smin else None
    amax = smax * mult if smax else None
    if amin and amax:
        pay_text = f"{_money(amin)}–{_money(amax)}" if amin != amax else _money(amin)
    elif amin or amax:
        pay_text = _money(amin or amax)
    else:
        pay_text = ""
    if pay_text and period != "YEAR":
        pay_text += f" /yr (posted per {period.lower()})"

    # Links: prefer the company's own site over an aggregator.
    options = raw.get("apply_options") or []
    listing_url = raw.get("job_apply_link") or (options[0]["apply_link"] if options else "")
    direct_url = ""
    for o in options:
        link = o.get("apply_link") or ""
        if o.get("is_direct") or (link and not _is_aggregator(link)):
            direct_url = link
            break
    if not direct_url and listing_url and not _is_aggregator(listing_url):
        direct_url = listing_url

    posted = None
    ts = raw.get("job_posted_at_datetime_utc")
    if ts:
        try:
            posted = dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            posted = None

    loc_bits = [raw.get("job_city"), raw.get("job_state")]
    location = ", ".join(b for b in loc_bits if b) or (raw.get("job_country") or "")

    highlights = raw.get("job_highlights") or {}
    return {
        "id": str(raw.get("job_id") or _dedupe_key(title, company))[:255],
        "dedupe_key": _dedupe_key(title, company),
        "search_query": query[:255],
        "title": title[:500],
        "company": company[:500],
        "company_website": raw.get("employer_website") or "",
        "location": location[:500],
        "is_remote": bool(raw.get("job_is_remote")),
        "employment_type": raw.get("job_employment_type") or "",
        "pay_text": pay_text,
        "pay_min_annual": amin,
        "pay_max_annual": amax,
        "posted_at": posted,
        "listing_url": listing_url,
        "direct_url": direct_url,
        "description": raw.get("job_description") or "",
        "listing_responsibilities": highlights.get("Responsibilities") or [],
    }
