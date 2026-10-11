import datetime as dt
import hmac
import json
import os
import re
import threading
from functools import wraps

from dotenv import load_dotenv

load_dotenv()

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,  # noqa: E402
                   request, send_file, session, url_for)

from lowtide import ai, db, files, pipeline, worddocs  # noqa: E402

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or "dev-only-change-me"
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
app.config["PERMANENT_SESSION_LIFETIME"] = dt.timedelta(days=30)
db.init()

TABS = [("new", "New"), ("saved", "Saved"), ("applied", "Applied"),
        ("not_interested", "Not for me"), ("filtered", "Filtered out")]
STATUSES = {"new", "saved", "applied", "not_interested"}


# ------------------------------------------------------------ helpers

def login_required(view):
    @wraps(view)
    def wrapper(*a, **kw):
        uid = session.get("uid")
        user = db.get_user(uid) if uid else None
        if not user:
            session.clear()
            return redirect(url_for("login", next=request.path))
        g.uid, g.user = uid, user
        return view(*a, **kw)
    return wrapper


@app.context_processor
def inject_user():
    return {"current_user": getattr(g, "user", None)}


@app.template_filter("fromjson")
def fromjson(s):
    try:
        return json.loads(s) if s else None
    except (TypeError, json.JSONDecodeError):
        return None


@app.template_filter("ago")
def ago(d):
    if not d:
        return ""
    days = (dt.datetime.utcnow() - d).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


@app.template_filter("ts")
def ts(d):
    return int(d.timestamp()) if d else 0


@app.template_filter("lines")
def lines(v):
    return "\n".join(v or [])


def _split(text):
    return [x.strip() for x in (text or "").splitlines() if x.strip()]


def _invite_code():
    return os.environ.get("INVITE_CODE") or os.environ.get("APP_PASSWORD", "")


# ------------------------------------------------------------ accounts

@app.route("/login", methods=["GET", "POST"])
def login():
    if not _invite_code():
        return render_template("login.html", missing=True)
    if db.user_count() == 0:
        return redirect(url_for("signup"))
    if request.method == "POST":
        u = db.check_login(request.form.get("username", "").strip(), request.form.get("password", ""))
        if u:
            session.clear()
            session.permanent = True
            session["uid"] = u["id"]
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("home"))
        flash("That username and password don't match.")
    return render_template("login.html", missing=False)


@app.route("/signup", methods=["GET", "POST"])
def signup():
    code = _invite_code()
    if not code:
        return render_template("login.html", missing=True)
    first = db.user_count() == 0
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        pw = request.form.get("password", "")
        if not hmac.compare_digest(request.form.get("invite", ""), code):
            flash("That invite code isn't right.")
        elif not re.fullmatch(r"[A-Za-z0-9_.-]{2,40}", username):
            flash("Usernames can use letters, numbers, dots, dashes and underscores (2 to 40 characters).")
        elif len(pw) < 8:
            flash("Pick a password with at least 8 characters.")
        elif db.find_user(username):
            flash("That username is taken.")
        else:
            uid = db.create_user(username, pw)
            session.clear()
            session.permanent = True
            session["uid"] = uid
            return redirect(url_for("home"))
    return render_template("signup.html", first=first)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ------------------------------------------------------------ pages

@app.route("/")
@login_required
def home():
    return redirect(url_for("jobs") if db.get_criteria(g.uid) else url_for("profile"))


@app.route("/profile")
@login_required
def profile():
    return render_template("profile.html", p=db.get_profile(g.uid),
                           c=db.get_criteria(g.uid), active="profile")


@app.post("/profile/about")
@login_required
def save_about():
    fields = {
        "description": request.form.get("description", "").strip(),
        "notes": request.form.get("notes", "").strip(),
        "linkedin_text": request.form.get("linkedin_text", "").strip(),
    }
    for kind in ("resume", "cover_letter"):
        up = request.files.get(kind)
        if up and up.filename:
            try:
                fields[f"{kind}_text"] = files.extract_text(up)
                fields[f"{kind}_filename"] = up.filename
            except Exception as e:
                flash(f"Couldn't read {up.filename}: {e}")
                return redirect(url_for("profile"))
        if request.form.get(f"remove_{kind}"):
            fields[f"{kind}_text"] = ""
            fields[f"{kind}_filename"] = ""
    db.save_profile(g.uid, **fields)

    if not (fields["description"] or db.get_profile(g.uid).get("resume_text")):
        flash("Saved. Add a description or a resume so Lowtide can build your search.")
        return redirect(url_for("profile"))
    try:
        db.save_criteria(g.uid, ai.build_criteria(db.get_profile(g.uid)))
        flash("Search settings updated from what you wrote. Check them below, then run a search.")
    except Exception as e:
        flash(f"Saved, but building search settings failed: {e}")
    return redirect(url_for("profile") + "#settings")


@app.post("/profile/settings")
@login_required
def save_settings():
    f = request.form
    criteria = ai.clean_criteria({
        "summary": f.get("summary", ""),
        "min_salary": f.get("min_salary", "0") or "0",
        "remote_only": bool(f.get("remote_only")),
        "employment_types": f.getlist("employment_types"),
        "target_titles": _split(f.get("target_titles")),
        "search_queries": _split(f.get("search_queries")),
        "must_haves": _split(f.get("must_haves")),
        "nice_to_haves": _split(f.get("nice_to_haves")),
        "avoid": _split(f.get("avoid")),
        "low_workload_priority": bool(f.get("low_workload_priority")),
        "strengths": _split(f.get("strengths")),
    })
    db.save_criteria(g.uid, criteria)
    flash("Search settings saved.")
    return redirect(url_for("profile") + "#settings")


@app.route("/jobs")
@login_required
def jobs():
    tab = request.args.get("tab", "new")
    if tab not in dict(TABS):
        tab = "new"
    return render_template("jobs.html", rows=db.list_jobs(g.uid, tab), tab=tab, tabs=TABS,
                           counts=db.tab_counts(g.uid), c=db.get_criteria(g.uid) or {},
                           run=db.latest_run(g.uid), tailored_ids=db.jobs_with_tailored(g.uid),
                           now=dt.datetime.utcnow(), active="jobs")


@app.post("/jobs/<int:job_id>/status")
@login_required
def set_status(job_id):
    status = (request.get_json(silent=True) or {}).get("status")
    if status not in STATUSES:
        return jsonify(error="bad status"), 400
    db.update_job(g.uid, job_id, status=status)
    return jsonify(ok=True)


# ------------------------------------------------------------ tailoring

@app.route("/jobs/<int:job_id>/tailor", methods=["GET", "POST"])
@login_required
def tailor(job_id):
    job = db.get_job(g.uid, job_id)
    if not job:
        abort(404)
    p = db.get_profile(g.uid)
    if request.method == "POST":
        if not p.get("resume_text"):
            flash("Upload a resume on your Profile page first.")
            return redirect(url_for("tailor", job_id=job_id))
        level = int(request.form.get("level", 2))
        want_cover = bool(request.form.get("cover"))
        try:
            out = ai.tailor(p, db.get_criteria(g.uid) or {}, job, level, want_cover)
            notes = {k: out.get(k) or [] for k in ("changes", "be_ready_to_explain", "gaps")}
            doc_id = db.save_tailored(g.uid, job_id, level, out.get("resume") or {},
                                      out.get("cover_letter") if want_cover else "", notes)
            if job["status"] == "new":
                db.update_job(g.uid, job_id, status="saved")
            return redirect(url_for("tailor", job_id=job_id, doc=doc_id))
        except Exception as e:
            flash(f"Couldn't write that: {e}")
            return redirect(url_for("tailor", job_id=job_id))
    docs = db.list_tailored(g.uid, job_id)
    current = None
    want = request.args.get("doc", type=int)
    if docs:
        current = next((d for d in docs if d["id"] == want), docs[0])
    return render_template("tailor.html", job=job, docs=docs, current=current,
                           levels=ai.LEVELS, has_resume=bool(p.get("resume_text")), active="jobs")


def _filename(job, kind):
    base = re.sub(r"[^A-Za-z0-9]+", "_", f"{job['company']}_{job['title']}")[:60].strip("_")
    return f"{kind}_{base}.docx"


@app.get("/tailored/<int:doc_id>/<kind>.docx")
@login_required
def download(doc_id, kind):
    d = db.get_tailored(g.uid, doc_id)
    if not d or kind not in ("resume", "cover_letter"):
        abort(404)
    job = db.get_job(g.uid, d["job_id"]) or {"company": "job", "title": ""}
    resume = json.loads(d["resume_json"] or "{}")
    if kind == "resume":
        buf = worddocs.resume_docx(resume)
    else:
        if not d["cover_letter"]:
            abort(404)
        buf = worddocs.cover_docx(resume.get("name", ""), d["cover_letter"])
    return send_file(buf, as_attachment=True, download_name=_filename(job, "Resume" if kind == "resume" else "Cover_Letter"),
                     mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


# ------------------------------------------------------------ runs

def _background_run(uid):
    try:
        pipeline.run(uid, "manual")
    except Exception as e:
        print("Run failed:", e)


@app.post("/run")
@login_required
def start_run():
    if not db.get_criteria(g.uid):
        return jsonify(error="Set up your profile first."), 400
    if db.run_in_progress(g.uid):
        return jsonify(ok=True, already=True)
    threading.Thread(target=_background_run, args=(g.uid,), daemon=True).start()
    return jsonify(ok=True)


@app.get("/run/status")
@login_required
def run_status():
    r = db.latest_run(g.uid)
    if not r:
        return jsonify(state="none")
    return jsonify(state=r["state"], log=r["log"][-4000:], running=db.run_in_progress(g.uid))


@app.get("/health")
def health():
    return "ok"


if __name__ == "__main__":
    app.run(debug=True, port=5000)
