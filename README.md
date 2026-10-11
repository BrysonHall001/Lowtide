# Lowtide

A personal job finder. You describe the job you want in plain words, and every
morning Lowtide searches job listings (Indeed, LinkedIn, Glassdoor, ZipRecruiter
and company career sites, via Google for Jobs), throws out anything that breaks
your dealbreakers, scores the rest for fit and for how calm the job is likely to
be, and researches the most promising few on the web (employee reviews, layoffs,
company size). Results show up in a table you can sort into Saved / Applied /
Not for me. Your choices teach it what you like.

## What it costs

- **Render** (hosts the app): free. The free tier falls asleep when unused, so
  the first page load after a while can take about a minute.
- **Neon** (stores your data): free.
- **JSearch on RapidAPI** (job listings): free plan. Lowtide runs 5 searches a
  day by default.
- **OpenAI** (the brain): pay-as-you-go, probably a few dollars a month. This is
  billed separately from a ChatGPT subscription. Set a monthly limit at
  platform.openai.com → Settings → Limits.

## Setup (about 30 minutes, once)

### 1. Put the code on GitHub
1. Create a new **private** repository on GitHub (keep it private; it's your job search).
2. Open it in a Codespace, drag `lowtide.zip` into the file panel, then run:

```bash
unzip -o lowtide.zip && rm lowtide.zip && git add . && git commit -m "Lowtide v1" && git push
```

### 2. Get your four keys
Keep these in a notes app for a few minutes. Treat them like passwords.

1. **Database:** sign up at neon.tech → create a project → copy the
   **connection string** (starts with `postgresql://`).
2. **Job listings:** sign up at rapidapi.com → search for **JSearch** (by
   OpenWeb Ninja) → **Subscribe** to the free Basic plan → copy the
   **X-RapidAPI-Key** shown on the endpoint page. Check the plan's monthly
   request limit; at 5 searches a day you'll use about 150 a month.
3. **OpenAI:** platform.openai.com → API keys → create a key. Add a few dollars
   of credit under Billing.
4. **App password:** make one up. This is what you'll log in with.

### 3. Put it online with Render
1. In Render: **New → Blueprint** → pick your Lowtide repository.
2. It will ask for four values. Paste them in:
   `APP_PASSWORD`, `OPENAI_API_KEY`, `RAPIDAPI_KEY`, `DATABASE_URL`.
3. Click **Apply**. After a few minutes you get a web address ending in `.onrender.com`.

### 4. Turn on the daily search
In your GitHub repository: **Settings → Secrets and variables → Actions → New
repository secret**. Add three secrets with these exact names and the same values:
`DATABASE_URL`, `OPENAI_API_KEY`, `RAPIDAPI_KEY`.

It runs every day around 7am Eastern. To test it right away: **Actions** tab →
**Daily job search** → **Run workflow**.

### 5. Use it
1. Open your Render address and create an account. The **invite code** is your `APP_PASSWORD`.
2. On **Profile**, describe what you want (type or press **Talk instead**),
   upload your resume, and press **Save and build my search**.
3. Check the search settings it builds and fix anything that's off.
4. Go to **Jobs** and press **Search now**. The first search takes a few minutes.

## Accounts
Everyone gets their own login, profile, jobs and tailored documents. To add
someone, send them the web address and the invite code (your `APP_PASSWORD`).
The first account created inherits any data from version 1.

Each person's daily search uses its own JSearch requests: about 150 a month per
person at the default settings. With more than one person, check your RapidAPI
plan's monthly limit, or lower `MAX_QUERIES_PER_RUN`.

## Tailored resumes and cover letters
On any job, press **Tailor resume**, pick how far to tailor (from *Keep it as is*
to *Stretch*), and download Word files. At every level it only reframes what's
true in your resume and profile; it never invents jobs, titles, dates, degrees,
tools or numbers. Each version lists what changed and anything you should be
ready to talk about in an interview.

## Tuning (optional)
Add any of these as environment variables in Render *and* as GitHub secrets
(the daily search reads GitHub's copy):

| Setting | Default | What it does |
|---|---|---|
| `MAX_QUERIES_PER_RUN` | 5 | Searches per run (each uses 1 RapidAPI request) |
| `MAX_SCREEN_PER_RUN` | 40 | Most listings the AI reviews per run |
| `MAX_DEEP_DIVES_PER_RUN` | 5 | How many top jobs get web research |
| `DEEP_DIVE_MIN_SCORE` | 60 | Minimum score before a job gets researched |
| `OPENAI_MODEL` | gpt-5-mini | Which OpenAI model to use |

If you add a tuning setting to the daily search, also add a matching line under
`env:` in `.github/workflows/daily-search.yml`.

## Good to know
- Scores are educated guesses. "Calm" scores marked *from the listing only* are
  based on the job description; *researched* ones include web findings.
  Confidence tells you how much real evidence turned up.
- Responsibilities in italics are the AI's guesses, not from the listing.
- **Company site** links go straight to the employer when Lowtide can find it,
  which beats applying through an aggregator.
- Jobs you mark **Not for me** steer future scoring away from similar roles.
