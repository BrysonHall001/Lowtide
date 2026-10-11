"""Database layer. Postgres in production (DATABASE_URL), local SQLite file
otherwise. Every row belongs to a user, so each person has their own
profile, jobs and search history."""
import json
import os
import re
import datetime as dt

from sqlalchemy import (
    Boolean, Column, DateTime, Integer, MetaData, String, Table, Text, UniqueConstraint,
    create_engine, func, select, insert, update, delete, desc, inspect,
)
from werkzeug.security import check_password_hash, generate_password_hash


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

users = Table(
    "users", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("username", String(80), unique=True, nullable=False),
    Column("password_hash", String(300), nullable=False),
    Column("created_at", DateTime),
)

profiles = Table(
    "profiles", meta,
    Column("user_id", Integer, primary_key=True),
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
    "user_jobs", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, index=True, nullable=False),
    Column("ref", String(255), nullable=False),          # the job board's id
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
    Column("pay_low", Integer, nullable=True),
    Column("pay_high", Integer, nullable=True),
    Column("posted_at", DateTime, nullable=True),
    Column("listing_url", Text),
    Column("direct_url", Text),
    Column("description", Text),
    Column("resp_verbatim", Text),
    Column("resp_inferred", Text),
    Column("passes", Boolean),
    Column("reject_reason", Text),
    Column("fit_score", Integer),
    Column("fit_reason", Text),
    Column("qualified", String(30)),
    Column("slack_score", Integer, nullable=True),
    Column("slack_confidence", String(20)),
    Column("slack_notes", Text),
    Column("company_size", String(200)),
    Column("deep_dive", Text),
    Column("rank_score", Integer, default=0),
    Column("status", String(30), default="new"),
    UniqueConstraint("user_id", "ref", name="uq_user_job"),
)

runs = Table(
    "user_runs", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, index=True),
    Column("started_at", DateTime),
    Column("finished_at", DateTime, nullable=True),
    Column("trigger", String(30)),
    Column("state", String(20)),
    Column("log", Text, default=""),
)

tailored = Table(
    "tailored_docs", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, index=True),
    Column("job_id", Integer, index=True),
    Column("level", Integer),
    Column("created_at", DateTime),
    Column("resume_json", Text),
    Column("cover_letter", Text, default=""),
    Column("notes_json", Text, default=""),
)


def now():
    return dt.datetime.utcnow()


def init():
    meta.create_all(engine)


# ---------- pay parsing (for sorting) ----------

def parse_pay(text):
    """'$72k–$90k' -> (72000, 90000). Returns (None, None) if unreadable."""
    if not text:
        return None, None
    vals = []
    for num, k in re.findall(r"\$?\s*(\d[\d,]*(?:\.\d+)?)\s*([kK])?", text):
        try:
            v = float(num.replace(",", ""))
        except ValueError:
            continue
        if k:
            v *= 1000
        if v >= 1000:
            vals.append(int(v))
    if not vals:
        return None, None
    return min(vals[:2]), max(vals[:2])


# ---------- users ----------

def user_count():
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(users)).scalar() or 0


def create_user(username, password):
    first = user_count() == 0
    with engine.begin() as c:
        res = c.execute(insert(users).values(
            username=username, password_hash=generate_password_hash(password), created_at=now()))
        uid = res.inserted_primary_key[0]
        c.execute(insert(profiles).values(user_id=uid, updated_at=now()))
    if first:
        _adopt_legacy_data(uid)
    return uid


def find_user(username):
    with engine.connect() as c:
        row = c.execute(select(users).where(func.lower(users.c.username) == username.lower())).mappings().first()
        return dict(row) if row else None


def check_login(username, password):
    u = find_user(username)
    if u and check_password_hash(u["password_hash"], password):
        return u
    return None


def all_user_ids():
    with engine.connect() as c:
        return [r[0] for r in c.execute(select(users.c.id)).all()]


def get_user(uid):
    with engine.connect() as c:
        row = c.execute(select(users).where(users.c.id == uid)).mappings().first()
        return dict(row) if row else None


def _adopt_legacy_data(uid):
    """Version 1 stored one person's data in tables called 'profile' and
    'jobs'. Hand that data to the first account created."""
    insp = inspect(engine)
    names = insp.get_table_names()
    lmeta = MetaData()
    try:
        if "profile" in names:
            lp = Table("profile", lmeta, autoload_with=engine)
            with engine.begin() as c:
                row = c.execute(select(lp)).mappings().first()
                if row:
                    vals = {k: row.get(k) for k in (
                        "description", "resume_text", "resume_filename", "cover_letter_text",
                        "cover_letter_filename", "linkedin_text", "notes", "criteria_json")}
                    c.execute(update(profiles).where(profiles.c.user_id == uid).values(**vals))
        if "jobs" in names:
            lj = Table("jobs", lmeta, autoload_with=engine)
            keep = {col.name for col in jobs.columns} - {"id", "user_id", "ref", "pay_low", "pay_high"}
            with engine.begin() as c:
                for row in c.execute(select(lj)).mappings().all():
                    vals = {k: row.get(k) for k in keep if k in row}
                    lo, hi = parse_pay(row.get("pay_text"))
                    vals.update(user_id=uid, ref=row["id"], pay_low=lo, pay_high=hi)
                    c.execute(insert(jobs).values(**vals))
    except Exception as e:  # never block signup over old data
        print("Couldn't copy version 1 data:", e)


# ---------- profile ----------

def get_profile(uid):
    with engine.connect() as c:
        row = c.execute(select(profiles).where(profiles.c.user_id == uid)).mappings().first()
    if not row:
        with engine.begin() as c:
            c.execute(insert(profiles).values(user_id=uid, updated_at=now()))
        return get_profile(uid)
    return dict(row)


def save_profile(uid, **fields):
    get_profile(uid)
    fields["updated_at"] = now()
    with engine.begin() as c:
        c.execute(update(profiles).where(profiles.c.user_id == uid).values(**fields))


def get_criteria(uid):
    raw = get_profile(uid).get("criteria_json") or ""
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None


def save_criteria(uid, criteria):
    save_profile(uid, criteria_json=json.dumps(criteria))


# ---------- jobs ----------

def known_job_keys(uid):
    with engine.connect() as c:
        rows = c.execute(select(jobs.c.ref, jobs.c.dedupe_key).where(jobs.c.user_id == uid)).all()
    return {r[0] for r in rows}, {r[1] for r in rows}


def count_jobs(uid):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(jobs).where(jobs.c.user_id == uid)).scalar() or 0


def save_job(uid, rec):
    rec = dict(rec, user_id=uid)
    rec["pay_low"], rec["pay_high"] = parse_pay(rec.get("pay_text"))
    with engine.begin() as c:
        row = c.execute(select(jobs.c.id).where(jobs.c.user_id == uid, jobs.c.ref == rec["ref"])).first()
        if row:
            c.execute(update(jobs).where(jobs.c.id == row[0]).values(**rec))
            return row[0]
        return c.execute(insert(jobs).values(**rec)).inserted_primary_key[0]


def update_job(uid, job_id, **fields):
    with engine.begin() as c:
        c.execute(update(jobs).where(jobs.c.id == job_id, jobs.c.user_id == uid).values(**fields))


def get_job(uid, job_id):
    with engine.connect() as c:
        row = c.execute(select(jobs).where(jobs.c.id == job_id, jobs.c.user_id == uid)).mappings().first()
        return dict(row) if row else None


def list_jobs(uid, tab):
    q = select(jobs).where(jobs.c.user_id == uid)
    if tab == "filtered":
        q = q.where(jobs.c.passes == False)  # noqa: E712
    else:
        q = q.where(jobs.c.passes == True, jobs.c.status == tab)  # noqa: E712
    q = q.order_by(desc(jobs.c.rank_score), desc(jobs.c.found_at)).limit(400)
    with engine.connect() as c:
        return [dict(r) for r in c.execute(q).mappings().all()]


def tab_counts(uid):
    out = {}
    with engine.connect() as c:
        for status, n in c.execute(
            select(jobs.c.status, func.count())
            .where(jobs.c.user_id == uid, jobs.c.passes == True)  # noqa: E712
            .group_by(jobs.c.status)
        ).all():
            out[status] = n
        out["filtered"] = c.execute(
            select(func.count()).select_from(jobs)
            .where(jobs.c.user_id == uid, jobs.c.passes == False)  # noqa: E712
        ).scalar() or 0
    return out


def feedback_examples(uid, limit=15):
    def grab(statuses):
        with engine.connect() as c:
            rows = c.execute(
                select(jobs.c.title, jobs.c.company)
                .where(jobs.c.user_id == uid, jobs.c.status.in_(statuses))
                .order_by(desc(jobs.c.found_at)).limit(limit)
            ).all()
        return [f"{t} at {co}" for t, co in rows]
    return grab(["saved", "applied"]), grab(["not_interested"])


# ---------- tailored documents ----------

def save_tailored(uid, job_id, level, resume, cover_letter, notes):
    with engine.begin() as c:
        return c.execute(insert(tailored).values(
            user_id=uid, job_id=job_id, level=level, created_at=now(),
            resume_json=json.dumps(resume), cover_letter=cover_letter or "",
            notes_json=json.dumps(notes or {}))).inserted_primary_key[0]


def list_tailored(uid, job_id):
    with engine.connect() as c:
        rows = c.execute(select(tailored).where(tailored.c.user_id == uid, tailored.c.job_id == job_id)
                         .order_by(desc(tailored.c.id))).mappings().all()
    return [dict(r) for r in rows]


def get_tailored(uid, doc_id):
    with engine.connect() as c:
        row = c.execute(select(tailored).where(tailored.c.id == doc_id, tailored.c.user_id == uid)).mappings().first()
        return dict(row) if row else None


def jobs_with_tailored(uid):
    with engine.connect() as c:
        return {r[0] for r in c.execute(select(tailored.c.job_id).where(tailored.c.user_id == uid)).all()}


# ---------- runs ----------

def start_run(uid, trigger):
    with engine.begin() as c:
        return c.execute(insert(runs).values(
            user_id=uid, started_at=now(), trigger=trigger, state="running", log="")
        ).inserted_primary_key[0]


def append_log(run_id, msg):
    stamp = now().strftime("%H:%M:%S")
    with engine.begin() as c:
        cur = c.execute(select(runs.c.log).where(runs.c.id == run_id)).scalar() or ""
        c.execute(update(runs).where(runs.c.id == run_id).values(log=cur + f"[{stamp}] {msg}\n"))


def finish_run(run_id, state):
    with engine.begin() as c:
        c.execute(update(runs).where(runs.c.id == run_id).values(state=state, finished_at=now()))


def latest_run(uid):
    with engine.connect() as c:
        row = c.execute(select(runs).where(runs.c.user_id == uid)
                        .order_by(desc(runs.c.id)).limit(1)).mappings().first()
        return dict(row) if row else None


def run_in_progress(uid):
    r = latest_run(uid)
    if not r or r["state"] != "running":
        return False
    return (now() - r["started_at"]).total_seconds() < 7200
