import datetime as dt
import hmac
import json
import os
import threading
from functools import wraps

from dotenv import load_dotenv

load_dotenv()

from flask import (Flask, flash, jsonify, redirect, render_template, request,  # noqa: E402
                   session, url_for)

from lowtide import ai, db, files, pipeline  # noqa: E402

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
        if not session.get("ok"):
            return redirect(url_for("login", next=request.path))
        return view(*a, **kw)
    return wrapper


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


@app.template_filter("lines")
def lines(v):
    return "\n".join(v or [])


def _split(text):
    return [x.strip() for x in (text or "").splitlines() if x.strip()]


# ------------------------------------------------------------ auth

@app.route("/login", methods=["GET", "POST"])
def login():
    password = os.environ.get("APP_PASSWORD", "")
    if not password:
        return render_template("login.html", missing=True)
    if request.method == "POST":
        if hmac.compare_digest(request.form.get("password", ""), password):
            session.permanent = True
            session["ok"] = True
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") else url_for("home"))
        flash("That password didn't match.")
    return render_template("login.html", missing=False)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ------------------------------------------------------------ pages

@app.route("/")
@login_required
def home():
    return redirect(url_for("jobs") if db.get_criteria() else url_for("profile"))


@app.route("/profile")
@login_required
def profile():
    return render_template("profile.html", p=db.get_profile(),
                           c=db.get_criteria(), active="profile")


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
    db.save_profile(**fields)

    if not (fields["description"] or db.get_profile().get("resume_text")):
        flash("Saved. Add a description or a resume so Lowtide can build your search.")
        return redirect(url_for("profile"))
    try:
        db.save_criteria(ai.build_criteria(db.get_profile()))
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
    db.save_criteria(criteria)
    flash("Search settings saved.")
    return redirect(url_for("profile") + "#settings")


@app.route("/jobs")
@login_required
def jobs():
    tab = request.args.get("tab", "new")
    if tab not in dict(TABS):
        tab = "new"
    return render_template("jobs.html", rows=db.list_jobs(tab), tab=tab, tabs=TABS,
                           counts=db.tab_counts(), c=db.get_criteria() or {},
                           run=db.latest_run(), active="jobs")


@app.post("/jobs/<path:job_id>/status")
@login_required
def set_status(job_id):
    status = (request.get_json(silent=True) or {}).get("status")
    if status not in STATUSES:
        return jsonify(error="bad status"), 400
    db.update_job(job_id, status=status)
    return jsonify(ok=True)


# ------------------------------------------------------------ runs

def _background_run():
    try:
        pipeline.run("manual")
    except Exception as e:
        print("Run failed:", e)


@app.post("/run")
@login_required
def start_run():
    if not db.get_criteria():
        return jsonify(error="Set up your profile first."), 400
    if db.run_in_progress():
        return jsonify(ok=True, already=True)
    threading.Thread(target=_background_run, daemon=True).start()
    return jsonify(ok=True)


@app.get("/run/status")
@login_required
def run_status():
    r = db.latest_run()
    if not r:
        return jsonify(state="none")
    return jsonify(state=r["state"], log=r["log"][-4000:],
                   running=db.run_in_progress())


@app.get("/health")
def health():
    return "ok"


if __name__ == "__main__":
    app.run(debug=True, port=5000)
