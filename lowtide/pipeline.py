"""One search run: fetch new listings -> hard filters -> AI screen -> deep dive
on the best few -> save everything."""
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import ai, db, jobs_api

MAX_QUERIES = int(os.environ.get("MAX_QUERIES_PER_RUN", "5"))
MAX_SCREEN = int(os.environ.get("MAX_SCREEN_PER_RUN", "40"))
MAX_DEEP_DIVES = int(os.environ.get("MAX_DEEP_DIVES_PER_RUN", "5"))
DEEP_DIVE_MIN_SCORE = int(os.environ.get("DEEP_DIVE_MIN_SCORE", "60"))

_lock = threading.Lock()


def _rank(fit, slack, want_slack):
    fit = fit or 0
    if want_slack and slack is not None:
        return round(0.5 * fit + 0.5 * slack)
    return fit


def _int(v):
    try:
        return max(0, min(100, int(float(v))))
    except (TypeError, ValueError):
        return None


def _base_record(n):
    return {
        "id": n["id"], "dedupe_key": n["dedupe_key"], "found_at": db.now(),
        "search_query": n["search_query"], "title": n["title"], "company": n["company"],
        "company_website": n["company_website"], "location": n["location"],
        "is_remote": n["is_remote"], "employment_type": n["employment_type"],
        "pay_text": n["pay_text"], "pay_source": "posted" if n["pay_text"] else "unknown",
        "posted_at": n["posted_at"], "listing_url": n["listing_url"],
        "direct_url": n["direct_url"], "description": n["description"],
        "resp_verbatim": json.dumps(n["listing_responsibilities"][:4]),
        "resp_inferred": "[]", "status": "new", "rank_score": 0,
    }


def run(trigger="manual"):
    """Returns True if a run happened, False if one was already going."""
    if not _lock.acquire(blocking=False):
        return False
    run_id = None
    try:
        db.init()
        if db.run_in_progress():
            return False
        run_id = db.start_run(trigger)
        log = lambda m: db.append_log(run_id, m)
        _run(log)
        log("Done.")
        db.finish_run(run_id, "done")
        return True
    except Exception as e:  # keep the error visible in the app
        if run_id:
            db.append_log(run_id, f"Stopped with an error: {e}")
            db.finish_run(run_id, "failed")
        raise
    finally:
        _lock.release()


def _run(log):
    criteria = db.get_criteria()
    if not criteria or not criteria.get("search_queries"):
        raise RuntimeError("Set up your profile first so Lowtide knows what to search for.")
    want_slack = criteria.get("low_workload_priority", False)
    min_salary = criteria.get("min_salary") or 0

    date_posted = "week" if db.count_jobs() == 0 else "3days"
    seen_ids, seen_keys = db.known_job_keys()
    fresh = []
    for q in criteria["search_queries"][:MAX_QUERIES]:
        try:
            results = jobs_api.search(q, date_posted)
        except Exception as e:
            log(f"Search \"{q}\" failed: {e}")
            continue
        added = 0
        for raw in results:
            n = jobs_api.normalize(raw, q)
            if n["id"] in seen_ids or n["dedupe_key"] in seen_keys:
                continue
            seen_ids.add(n["id"]); seen_keys.add(n["dedupe_key"])
            fresh.append(n); added += 1
        log(f"Searched \"{q}\": {len(results)} listings, {added} new.")

    # Hard filter: posted pay clearly below the floor. Saves AI cost.
    to_screen = []
    for n in fresh:
        top = n["pay_max_annual"] or n["pay_min_annual"]
        if min_salary and top and top < min_salary * 0.95:
            rec = _base_record(n)
            rec.update(passes=False, reject_reason=f"Posted pay ({n['pay_text']}) is under your minimum.")
            db.save_job(rec)
        else:
            to_screen.append(n)

    if len(to_screen) > MAX_SCREEN:
        log(f"{len(to_screen)} new listings; screening the {MAX_SCREEN} most recent.")
        to_screen.sort(key=lambda n: n["posted_at"] or db.now(), reverse=True)
        to_screen = to_screen[:MAX_SCREEN]
    log(f"Screening {len(to_screen)} listings…")

    liked, disliked = db.feedback_examples()
    passed = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(ai.screen_job, criteria, n, liked, disliked): n for n in to_screen}
        for f in as_completed(futures):
            n = futures[f]
            rec = _base_record(n)
            try:
                s = f.result()
            except Exception as e:
                log(f"Couldn't screen \"{n['title']}\": {e}")
                continue
            fit, slack = _int(s.get("fit_score")), _int(s.get("slack_score"))
            est = (s.get("estimated_pay") or "").strip()
            verbatim = s.get("responsibilities_verbatim") or n["listing_responsibilities"][:4]
            rec.update(
                passes=bool(s.get("passes_dealbreakers", True)),
                reject_reason=s.get("reject_reason") or "",
                remote_note=s.get("remote_note") or "",
                employment_type=s.get("employment_type") or n["employment_type"],
                fit_score=fit, fit_reason=s.get("fit_reason") or "",
                qualified=s.get("qualified") or "",
                slack_score=slack if want_slack else None,
                slack_confidence="listing only" if want_slack else "",
                slack_notes=s.get("slack_notes") or "",
                company_size=s.get("company_size") or "",
                resp_verbatim=json.dumps(list(verbatim)[:4]),
                resp_inferred=json.dumps(list(s.get("responsibilities_inferred") or [])[:3]),
                rank_score=_rank(fit, slack, want_slack),
            )
            if not n["pay_text"] and est:
                rec.update(pay_text=est, pay_source="estimated")
            db.save_job(rec)
            if rec["passes"]:
                passed.append(rec)

    log(f"{len(passed)} passed your dealbreakers.")

    # Deep dive on the best few.
    picks = sorted((r for r in passed if r["rank_score"] >= DEEP_DIVE_MIN_SCORE),
                   key=lambda r: r["rank_score"], reverse=True)[:MAX_DEEP_DIVES]
    for r in picks:
        log(f"Researching {r['company']} ({r['title']})…")
        try:
            d = ai.deep_dive(criteria, r)
        except Exception as e:
            log(f"Research on {r['company']} failed: {e}")
            continue
        fields = {"deep_dive": json.dumps(d)}
        if d.get("company_size"):
            fields["company_size"] = str(d["company_size"])[:200]
        url = (d.get("direct_apply_url") or "").strip()
        if url.startswith("http") and not r.get("direct_url"):
            fields["direct_url"] = url
        if want_slack:
            new_slack = _int(d.get("slack_score"))
            if new_slack is not None:
                fields["slack_score"] = new_slack
                fields["slack_confidence"] = (d.get("confidence") or "low").lower()
                fields["rank_score"] = _rank(r["fit_score"], new_slack, True)
        db.update_job(r["id"], **fields)
