"""Database layer. Uses Postgres in production (DATABASE_URL) and a local
SQLite file if DATABASE_URL isn't set (handy for testing)."""
import json
import os
import datetime as dt

from sqlalchemy import (
    Boolean, Column, DateTime, Integer, MetaData, String, Table, Text,
    create_engine, func, select, insert, update, desc,
)


def _db_url():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return "sqlite:///lowtide_local.db"
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


_url = _db_url()
_kwargs = {"pool_pre_ping": True, "future": True}
if _url.startswith("sqlite"):
    _kwargs["connect_args"] = {"check_same_thread": False}
engine = create_engine(_url, **_kwargs)
meta = MetaData()

profile = Table(
    "profile", meta,
    Column("id", Integer, primary_key=True),
    Column("description", Text, default=""),
    Column("resume_text", Text, default=""),
    Column("resume_filename", String(255), default=""),
    Column("cover_letter_text", Text, default=""),
    Column("cover_letter_filename", String(255), default=""),
    Column("linkedin_text", Text, default=""),
    Column("notes", Text, default=""),
    Column("criteria_json", Text, default=""),
    Column("updated_at", DateTime),
)

jobs = Table(
    "jobs", meta,
    Column("id", String(255), primary_key=True),
    Column("dedupe_key", String(600), index=True),
    Column("found_at", DateTime),
    Column("search_query", String(255)),
    Column("title", String(500)),
    Column("company", String(500)),
    Column("company_website", Text),
    Column("location", String(500)),
    Column("is_remote", Boolean),
    Column("remote_note", Text),
    Column("employment_type", String(100)),
    Column("pay_text", String(255)),
    Column("pay_source", String(30)),        # posted / estimated / unknown
    Column("posted_at", DateTime, nullable=True),
    Column("listing_url", Text),
    Column("direct_url", Text),
    Column("description", Text),
    Column("resp_verbatim", Text),           # JSON list
    Column("resp_inferred", Text),           # JSON list
    Column("passes", Boolean),
    Column("reject_reason", Text),
    Column("fit_score", Integer),
    Column("fit_reason", Text),
    Column("qualified", String(30)),
    Column("slack_score", Integer, nullable=True),
    Column("slack_confidence", String(20)),
    Column("slack_notes", Text),
    Column("company_size", String(200)),
    Column("deep_dive", Text),               # JSON object
    Column("rank_score", Integer, default=0),
    Column("status", String(30), default="new"),
)

runs = Table(
    "runs", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("started_at", DateTime),
    Column("finished_at", DateTime, nullable=True),
    Column("trigger", String(30)),
    Column("state", String(20)),             # running / done / failed
    Column("log", Text, default=""),
)


def now():
    return dt.datetime.utcnow()


def init():
    meta.create_all(engine)
    with engine.begin() as c:
        if not c.execute(select(profile.c.id).where(profile.c.id == 1)).first():
            c.execute(insert(profile).values(id=1, updated_at=now()))


# ---------- profile ----------

def get_profile():
    with engine.connect() as c:
        row = c.execute(select(profile).where(profile.c.id == 1)).mappings().first()
        return dict(row) if row else {}


def save_profile(**fields):
    fields["updated_at"] = now()
    with engine.begin() as c:
        c.execute(update(profile).where(profile.c.id == 1).values(**fields))


def get_criteria():
    raw = get_profile().get("criteria_json") or ""
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None


def save_criteria(criteria):
    save_profile(criteria_json=json.dumps(criteria))


# ---------- jobs ----------

def known_job_keys():
    with engine.connect() as c:
        rows = c.execute(select(jobs.c.id, jobs.c.dedupe_key)).all()
    return {r[0] for r in rows}, {r[1] for r in rows}


def count_jobs():
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(jobs)).scalar() or 0


def save_job(job):
    with engine.begin() as c:
        exists = c.execute(select(jobs.c.id).where(jobs.c.id == job["id"])).first()
        if exists:
            c.execute(update(jobs).where(jobs.c.id == job["id"]).values(**job))
        else:
            c.execute(insert(jobs).values(**job))


def update_job(job_id, **fields):
    with engine.begin() as c:
        c.execute(update(jobs).where(jobs.c.id == job_id).values(**fields))


def get_job(job_id):
    with engine.connect() as c:
        row = c.execute(select(jobs).where(jobs.c.id == job_id)).mappings().first()
        return dict(row) if row else None


def list_jobs(tab):
    q = select(jobs)
    if tab == "filtered":
        q = q.where(jobs.c.passes == False)  # noqa: E712
    else:
        q = q.where(jobs.c.passes == True, jobs.c.status == tab)  # noqa: E712
    q = q.order_by(desc(jobs.c.rank_score), desc(jobs.c.found_at)).limit(300)
    with engine.connect() as c:
        return [dict(r) for r in c.execute(q).mappings().all()]


def tab_counts():
    out = {}
    with engine.connect() as c:
        for status, n in c.execute(
            select(jobs.c.status, func.count()).where(jobs.c.passes == True)  # noqa: E712
            .group_by(jobs.c.status)
        ).all():
            out[status] = n
        out["filtered"] = c.execute(
            select(func.count()).select_from(jobs).where(jobs.c.passes == False)  # noqa: E712
        ).scalar() or 0
    return out


def feedback_examples(limit=15):
    """Titles/companies the user liked or rejected, to steer future scoring."""
    def grab(statuses):
        with engine.connect() as c:
            rows = c.execute(
                select(jobs.c.title, jobs.c.company)
                .where(jobs.c.status.in_(statuses))
                .order_by(desc(jobs.c.found_at)).limit(limit)
            ).all()
        return [f"{t} at {co}" for t, co in rows]
    return grab(["saved", "applied"]), grab(["not_interested"])


# ---------- runs ----------

def start_run(trigger):
    with engine.begin() as c:
        res = c.execute(insert(runs).values(
            started_at=now(), trigger=trigger, state="running", log=""))
        return res.inserted_primary_key[0]


def append_log(run_id, msg):
    stamp = now().strftime("%H:%M:%S")
    with engine.begin() as c:
        cur = c.execute(select(runs.c.log).where(runs.c.id == run_id)).scalar() or ""
        c.execute(update(runs).where(runs.c.id == run_id)
                  .values(log=cur + f"[{stamp}] {msg}\n"))


def finish_run(run_id, state):
    with engine.begin() as c:
        c.execute(update(runs).where(runs.c.id == run_id)
                  .values(state=state, finished_at=now()))


def latest_run():
    with engine.connect() as c:
        row = c.execute(select(runs).order_by(desc(runs.c.id)).limit(1)).mappings().first()
        return dict(row) if row else None


def run_in_progress():
    r = latest_run()
    if not r or r["state"] != "running":
        return False
    # Treat runs stuck for 2+ hours as dead (e.g. server restarted mid-run).
    return (now() - r["started_at"]).total_seconds() < 7200
