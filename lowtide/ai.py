"""All the OpenAI calls live here."""
import json
import os
import re

from openai import OpenAI, BadRequestError

MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
RESEARCH_MODEL = os.environ.get("OPENAI_RESEARCH_MODEL", MODEL)

_client = None


def client():
    global _client
    if _client is None:
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY isn't set.")
        _client = OpenAI()
    return _client


def _ask(instructions, prompt, model=None, web=False, json_mode=True):
    """Send one request. Returns the text. Retries without optional settings
    if the chosen model doesn't support them."""
    args = {"model": model or MODEL, "instructions": instructions, "input": prompt,
            "reasoning": {"effort": "low"}}
    if web:
        args["tools"] = [{"type": "web_search"}]
    elif json_mode:
        args["text"] = {"format": {"type": "json_object"}}
    for _ in range(3):
        try:
            return client().responses.create(**args).output_text
        except BadRequestError as e:
            msg = str(e).lower()
            if "reasoning" in msg and "reasoning" in args:
                args.pop("reasoning")
                continue
            if "format" in msg and "text" in args:
                args.pop("text")
                continue
            raise
    return client().responses.create(**args).output_text


def _json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def _clip(s, n):
    s = s or ""
    return s if len(s) <= n else s[:n] + "\n…[trimmed]"


# ---------------------------------------------------------------- profile

CRITERIA_INSTRUCTIONS = """You turn a job seeker's rambling description, resume and notes
into clear, structured search settings. Be faithful to what they actually said; don't
moralize about their preferences. Write in plain language. Return JSON only."""

CRITERIA_SHAPE = """Return a JSON object with exactly these keys:
{
  "summary": "2-3 plain sentences: who they are and what they're looking for",
  "min_salary": integer yearly USD floor (their absolute minimum),
  "remote_only": true/false,
  "employment_types": list from ["Full-time","Contract","Part-time"],
  "target_titles": 6-12 realistic job titles they're a credible candidate for,
  "search_queries": 5-6 short job-board searches (2-4 words each, e.g. "proposal writer"); no location words,
  "must_haves": short list of non-negotiables beyond pay/remote,
  "nice_to_haves": short list,
  "avoid": short list of things that would make a job a bad fit,
  "low_workload_priority": true if they want a calm, low-pressure, low-workload or low-surveillance job,
  "strengths": 4-8 short phrases describing what makes them hireable
}"""


def build_criteria(p):
    prompt = f"""{CRITERIA_SHAPE}

WHAT THEY'RE LOOKING FOR (their own words, may be dictated):
{_clip(p.get('description'), 8000)}

EXTRA NOTES ABOUT THEIR BACKGROUND:
{_clip(p.get('notes'), 4000)}

RESUME:
{_clip(p.get('resume_text'), 10000)}

LINKEDIN:
{_clip(p.get('linkedin_text'), 6000)}

COVER LETTER (for tone/background only):
{_clip(p.get('cover_letter_text'), 3000)}"""
    data = _json(_ask(CRITERIA_INSTRUCTIONS, prompt))
    return clean_criteria(data)


def clean_criteria(d):
    def lst(k):
        v = d.get(k) or []
        if isinstance(v, str):
            v = [x.strip() for x in v.splitlines()]
        return [str(x).strip() for x in v if str(x).strip()]
    try:
        min_salary = int(float(str(d.get("min_salary") or 0).replace(",", "").replace("$", "")))
    except ValueError:
        min_salary = 0
    return {
        "summary": str(d.get("summary") or "").strip(),
        "min_salary": min_salary,
        "remote_only": bool(d.get("remote_only", True)),
        "employment_types": lst("employment_types") or ["Full-time"],
        "target_titles": lst("target_titles"),
        "search_queries": lst("search_queries")[:8],
        "must_haves": lst("must_haves"),
        "nice_to_haves": lst("nice_to_haves"),
        "avoid": lst("avoid"),
        "low_workload_priority": bool(d.get("low_workload_priority", False)),
        "strengths": lst("strengths"),
    }


def _criteria_block(c):
    return json.dumps({k: c.get(k) for k in (
        "summary", "min_salary", "remote_only", "employment_types", "target_titles",
        "must_haves", "nice_to_haves", "avoid", "low_workload_priority", "strengths")},
        indent=1)


# ---------------------------------------------------------------- screening

SCREEN_INSTRUCTIONS = """You screen job listings for one specific job seeker. Be skeptical
and concrete. Read between the lines of corporate language. Return JSON only."""

SLACK_GUIDE = """WORKLOAD / PRESSURE SCORE ("slack_score", 0-100, higher = calmer, more autonomy,
less surveillance, lighter load). Signals that RAISE it: large established organization;
regulated or slow-moving industry (banking, credit unions, insurance, utilities,
government/government contractors, universities, healthcare systems, pharma); maintaining
or updating existing materials; cyclical/annual/deadline-based work with natural lulls;
long review cycles; established team; async or flexible-schedule language; output-based
evaluation. Signals that LOWER it: startup, "fast-paced", hypergrowth, "wear many hats",
"scrappy", high volume, quotas, billable hours or utilization targets, agency work,
heavy client-facing duties, on-call, "hit the ground running", building something from
scratch, time tracking or monitoring software, sales targets, constant meetings."""


def screen_job(criteria, job, liked, disliked):
    want_slack = criteria.get("low_workload_priority")
    prompt = f"""THE JOB SEEKER'S SETTINGS:
{_criteria_block(criteria)}

{"Jobs they marked as good fits before: " + "; ".join(liked) if liked else ""}
{"Jobs they rejected before: " + "; ".join(disliked) if disliked else ""}

{SLACK_GUIDE if want_slack else ""}

THE LISTING:
Title: {job['title']}
Company: {job['company']}
Location: {job['location']}  (job board marks it remote: {'yes' if job['is_remote'] else 'no'})
Employment type: {job['employment_type']}
Posted pay: {job['pay_text'] or 'not posted'}
Description:
{_clip(job['description'], 7000)}

Return a JSON object with exactly these keys:
{{
  "passes_dealbreakers": true/false. False if it clearly fails remote_only (hybrid,
     onsite, or remote-only-within-one-metro), pay clearly under min_salary, wrong
     employment type, or it's clearly something from their "avoid" list. Missing pay is NOT a fail.
  "reject_reason": "one short sentence if false, else empty",
  "remote_note": "under 8 plain words, e.g. 'Fully remote', 'Remote, some travel', 'Remote, US only', 'Hybrid', 'Unclear'",
  "employment_type": "Full-time / Contract / Part-time / Unknown",
  "estimated_pay": "if pay isn't posted, your best yearly estimate like \\"$70k–$85k\\", else empty",
  "fit_score": 0-100 overall match for this person,
  "fit_reason": "one plain sentence, under 25 words",
  "qualified": "strong" / "reasonable" / "stretch" (would they realistically get an interview?),
  "slack_score": {"0-100 per the guide above" if want_slack else "null"},
  "slack_notes": "{"one or two short sentences, under 40 words, naming the specific signals you saw" if want_slack else ""}",
  "company_size": "your best guess, e.g. '10,000+ employees' or 'small startup'",
  "responsibilities_verbatim": up to 4 short duties copied word-for-word from the listing,
  "responsibilities_inferred": up to 3 likely duties NOT stated in the listing, your educated guess
}}"""
    return _json(_ask(SCREEN_INSTRUCTIONS, prompt))


# ---------------------------------------------------------------- deep dive

DIVE_INSTRUCTIONS = """You are a careful researcher checking out an employer and a specific
role for a job seeker, using web search. Look at employee reviews (Glassdoor, Indeed
company reviews, Reddit, Blind, Fishbowl, Comparably), recent news (layoffs, acquisitions,
return-to-office mandates, funding trouble), company size and stability, and what people
in this kind of role at this company say. Report only what you actually found; say so
when evidence is thin. Return JSON only, no prose outside the JSON."""


def deep_dive(criteria, job):
    want_slack = criteria.get("low_workload_priority")
    focus = ("Their top priority is a calm, low-pressure, low-surveillance job with a "
             "manageable workload and autonomy over their time." if want_slack else
             "They care about stability, work-life balance and whether it's a good fit.")
    prompt = f"""Research this job for the seeker. {focus}

Seeker summary: {criteria.get('summary', '')}

Job: {job['title']} at {job['company']}
Company website: {job.get('company_website') or 'unknown'}
Listing link: {job.get('listing_url') or ''}
Listing excerpt:
{_clip(job['description'], 2500)}

Also try to find this exact job on the company's own careers page or applicant
tracking system (Workday, Greenhouse, Lever, iCIMS, etc.) so they can apply directly.

Return a JSON object with exactly these keys:
{{
  "verdict": "3-4 plain sentences: what working here in this role is probably like and whether it fits",
  "slack_score": 0-100 (higher = calmer, more autonomy, lighter load, less monitoring),
  "confidence": "low" / "medium" / "high" (how much real evidence you found),
  "green_flags": up to 4 short specific findings,
  "red_flags": up to 4 short specific findings,
  "company_size": "e.g. '~2,000 employees'",
  "recent_turmoil": "layoffs/RTO/acquisition news in the last 18 months, or 'none found'",
  "direct_apply_url": "link to this job on the company's own site, or empty if not found",
  "sources": up to 5 URLs you relied on
}}"""
    return _json(_ask(DIVE_INSTRUCTIONS, prompt, model=RESEARCH_MODEL, web=True, json_mode=False))


# ---------------------------------------------------------------- tailoring

LEVELS = {
    0: ("Keep it as is",
        "Keep their resume essentially unchanged. Only reorder bullets so the most relevant "
        "come first and adjust the summary to name this role. Do not reword bullets."),
    1: ("Light touch",
        "Keep their wording mostly intact. Reorder for relevance, tune the summary, and swap "
        "in the job's terminology where it means the same thing as what they wrote."),
    2: ("Tailored",
        "Rewrite the summary and bullets to mirror the job's language and priorities. Lead "
        "with the most relevant experience, trim bullets that don't help, and add relevant "
        "skills they clearly have from their background."),
    3: ("Bold",
        "Confidently reframe their experience around this job. Emphasize transferable work, "
        "describe adjacent duties in the job's terms, and use strong, specific verbs. "
        "Present the closest-matching parts of each role prominently."),
    4: ("Stretch",
        "Make the strongest honest case possible for a job that isn't a natural fit. Reframe "
        "every role around transferable skills, lead with whatever overlaps most, and position "
        "them as a credible career-changer."),
}

TAILOR_INSTRUCTIONS = """You are an expert resume writer tailoring a resume and cover letter
to one specific job. HARD RULES at every level: never invent employers, job titles, dates,
degrees, certifications, licenses, security clearances, tools or software the person has
not used, or numbers/metrics that aren't in their materials. You may reword, reorder,
reframe and emphasize. If the job requires something they don't have, don't claim it;
the cover letter may address it honestly. Write in a natural, confident human voice
without buzzword stuffing. Return JSON only."""


def tailor(profile, criteria, job, level, want_cover):
    level = max(0, min(4, int(level)))
    name, how = LEVELS[level]
    prompt = f"""TAILORING LEVEL: {level} of 4 ({name}). {how}

THE JOB:
{job['title']} at {job['company']}
{_clip(job['description'], 9000)}

THEIR CURRENT RESUME (source of truth for facts):
{_clip(profile.get('resume_text'), 12000)}

EXTRA BACKGROUND THEY WROTE (also true; may use):
{_clip(profile.get('notes'), 4000)}

LINKEDIN (also true; may use):
{_clip(profile.get('linkedin_text'), 6000)}

THEIR STOCK COVER LETTER (for voice and facts only):
{_clip(profile.get('cover_letter_text'), 4000)}

Return a JSON object with exactly these keys:
{{
  "resume": {{
    "name": "their name",
    "contact": "one line of contact details exactly as in their resume (keep placeholders as-is)",
    "summary": "2-4 sentence professional summary aimed at this job",
    "sections": [
      {{"title": "Experience", "entries": [
        {{"heading": "job title", "subheading": "employer", "dates": "dates as written", "location": "", "bullets": ["..."]}}
      ]}},
      {{"title": "Education", "entries": [ ... same shape, bullets may be empty ... ]}}
    ],
    "skills": [{{"label": "group name", "items": "comma-separated skills"}}]
  }},
  "cover_letter": {"\"3-4 short paragraphs addressed to the hiring team, no address block, no placeholders except [Hiring Manager] if needed, signed with their name\"" if want_cover else "\"\""},
  "changes": ["3-6 short notes on what you changed and why"],
  "be_ready_to_explain": ["claims that are reframed enough that they should be ready to talk about them in an interview; empty if none"],
  "gaps": ["requirements of the job they don't appear to meet; empty if none"]
}}"""
    return _json(_ask(TAILOR_INSTRUCTIONS, prompt))
