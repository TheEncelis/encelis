# encelis

An adaptive English grammar platform for Uzbek-speaking learners, from A1 to C2. The interface and explanations are in Uzbek (Latin script); examples, exercises and grammatical terms are in English.

encelis is built as one learning system. It tracks each learner's knowledge **per grammar concept**, decides what to study next from that model, and schedules reviews with FSRS. A learner can register, take an adaptive placement test, study theory, practise with varied exercises, get feedback that names their mistake, and come back to weak or fading concepts until using them is automatic.

---

## Architecture

```
config/            settings, urls, celery
apps/
  core/            platform settings, rate limiting, Resend backend, gating middleware, template tags, backup/admin commands
  accounts/        custom User, email/username login, verification and reset emails
  curriculum/      Level → Unit → Topic → LearningObjective, prerequisite graph, theory blocks, seed content
  questions/       Question bank, deterministic answer checking, validation, stored verdicts, learner reports
  learning/        the learning engine: mastery model, stage transitions, evaluation pipeline, selector, planner, sessions
  reviews/         FSRS cards and review logs (scheduling only)
  assessments/     adaptive placement (Bayesian ability estimation)
  ai/              DeepSeek gateway, output schemas, AI services, generation pipeline and jobs
  analytics/       daily activity, streaks, learner and platform analytics
  studio/          Admin Studio (superuser only); units, topics and theory blocks are reordered by drag and drop (or ↑/↓ on a focused handle)
templates/ static/ tests/
```

Business logic lives in service modules (`engine.py`, `mastery.py`, `planner.py`, `sessions.py`, `services.py`). Views only parse input and render.

### The learning model

Three kinds of state are kept separate:

| State | Model | Meaning |
|---|---|---|
| History | `learning.Attempt` | Every answer, never edited: score, error category, response time, whether the learner had seen that question before, whether it came after a delay, and how it was evaluated |
| Current knowledge | `learning.TopicMastery` | Signals derived from history, plus the learner's stage in the cycle |
| Schedule | `reviews.ReviewCard` | FSRS memory state and due date |

**Learning cycle.** Learn → Understand → Practice → Apply → Master → Retain (`learning/mastery.py::decide_stage`). A topic moves at most one stage per answer. It moves back when recent answers show the learner isn't coping:

- Understand/Practice → back to Learn. The theory is shown again, and a short theory item is inserted into the current session.
- Apply/Master → back to Practice.
- Retain → back to Practice when the topic decays, or after two failed FSRS reviews in a row.

**Mastery score.** The score is evidence-weighted accuracy × confidence. Each answer is weighted by:

- exercise type (production types such as transformation, error correction and translation count more than recognition types)
- question difficulty
- delay (an answer given after a real break counts more)
- novelty (a repeated question counts 0.2× within 24 h, then 0.5×)

Fluency (response time against the expected time) scales the quality of each answer. Confidence grows with independent evidence, and no single question can supply more than about 1.5 answers' worth of it. So repeating the same item never inflates mastery.

**Reaching Retain** requires all of the following:

- score ≥ the topic's threshold
- at least 3 exercise types answered correctly on first exposure
- at least 3 correct first-exposure production answers
- at least 6 distinct questions
- at least one correct answer after a delay (≥ 20 h)
- fluency ≥ 60%
- no error category repeating

**Prerequisites** are explicit graph edges, either hard or soft, and cycles are rejected. A topic is offered as new material only when its hard prerequisites have reached Apply. Learners can still open any topic. A weak topic surfaces its weak prerequisites first, because those are the likely root cause.

**FSRS** (`reviews/services.py`) schedules only. A review is a set of 3 varied questions. Its accuracy and speed map deterministically to Again, Hard, Good or Easy. Mastery is updated separately from the same attempts.

**Session planning** (`learning/planner.py`). The daily session fills the learner's goal (10, 15, 20 or 30 minutes) with:

- due reviews, about 40% of the time, ordered by how many topics depend on each one and how much it has decayed
- weakness practice
- the current topics, with new material only while fewer than 3 topics are in active learning

After 7 or more days away, reviews take up to 65% of the session and no new topics are introduced. Questions are chosen when an item is served, which gives:

- rotation, with unseen questions preferred
- difficulty that follows the learner's stage and recent accuracy
- variety of exercise types
- error targeting: a wrong answer inserts a remedial item that focuses on the same error category

**Placement** (`assessments/services.py`) estimates ability on a 0–6 grid (A1 = 0–1 … C2 = 5–6) with a Bayesian logistic model that corrects for guessing on multiple-choice items.

- It picks the most informative unused item, preferring topics not yet probed.
- It stops after 8–16 items, once the estimate is precise enough.
- Topics well below the estimate are credited as *assumed*. They are not trusted blindly: each gets a verification review in the following days.
- Missed topics below the learner's level are flagged as weak.
- After placement, ability keeps updating from every first-exposure answer.

### Answer evaluation and AI

1. **Deterministic check** (`questions/checking.py`). This normalizes case, punctuation and contractions, including ambiguous forms such as `'s` → is/has and `'d` → would/had. It accepts any listed alternative, gives partial credit on multi-blank items, and classifies mistakes by comparing tokens (article, preposition, agreement, verb form, word order, missing or extra word, spelling).
2. **Stored verdict.** An unlisted answer is looked up in `AnswerVerdict`, so identical answers are never evaluated twice. An admin can override a verdict.
3. **AI evaluation** (DeepSeek). Free production and unlisted alternative answers get a semantic and grammatical judgement. The output must pass a pydantic schema and deterministic guards, for example a sentence returned unchanged is never accepted.
4. **No validated verdict.** If none of the above produces one, the attempt is stored with `counted=False`. It never moves mastery in either direction.

`ai/client.py` is the only code that calls the API. It chooses the model per task:

| Model | Tasks |
|---|---|
| `deepseek-flash`, no thinking | routine evaluation, explanations, question generation, cross-checks |
| `deepseek-v4-pro` with thinking | theory drafts, theory review, free-production evaluation at B2+ |

The client also handles JSON mode, retries on transient errors and one repair round when output is invalid. Every call is logged to `AIUsageLog` with its tokens.

**Content workflow.** Reusable questions are generated by a `GenerationJob` and go through these gates:

1. schema validation
2. `validate_question`: structure, blank and answer consistency, constructions buildable from their tokens, Latin-script Uzbek, English-only fields
3. duplicate detection by content hash
4. an independent blind solve that must match the answer key, which also detects ambiguity

Questions that pass wait for admin approval, unless auto-approve is turned on.

When a learner is close to running out of unseen questions on a topic, encelis generates personal questions targeted at their error category. These must pass every gate, and only that learner sees them.

---

## Setup (development)

Requirements: Python 3.12+ and Node 20+. Redis is optional.

```bash
python -m venv .venv
```
```bash
.venv/Scripts/pip install -r requirements.txt
```
On macOS or Linux, use `.venv/bin/` instead of `.venv/Scripts/`.
```bash
npm install
```
```bash
npm run build:css
```

Copy `.env.example` to `.env` and fill in `DEEPSEEK_API_KEY`, `RESEND_API_KEY` and the `ADMIN_*` values. Then run:

```bash
.venv/Scripts/python manage.py migrate
```
```bash
.venv/Scripts/python manage.py seed_curriculum
```
```bash
.venv/Scripts/python manage.py ensure_admin
```
```bash
.venv/Scripts/python manage.py runserver
```

- Learner app: http://localhost:8000
- Admin Studio: http://localhost:8000/studio/ (sign in with `ADMIN_LOGIN` or `ADMIN_EMAIL`)
- Raw Django admin: `/django-admin/` (kept for emergencies)

Run `npm run watch:css` while you edit templates.

Behaviour without optional services:

- **No `REDIS_URL`:** Celery runs inline, and AI generation jobs run in a background thread so requests never block.
- **No `RESEND_API_KEY`:** emails are printed to the console. With a key but the default sender `onboarding@resend.dev`, Resend delivers only to the Resend account owner's address. To email learners, verify a domain in Resend and set `DEFAULT_FROM_EMAIL`.
- **No `DEEPSEEK_API_KEY`:** AI features switch off. Answers that cannot be judged deterministically are not counted.

### Management commands

| Command | Purpose |
|---|---|
| `seed_curriculum [--refresh-theory]` | Loads the A1–C2 curriculum: 21 topics with theory and about 250 validated questions. Safe to run repeatedly. |
| `ensure_admin` | Creates or updates the single superuser from `ADMIN_*` |
| `generate_questions <topic-slug> --type gap --count 6 --difficulty 3` | Batch AI generation from the CLI. Questions are saved as pending. |
| `backup_db [--keep 14]` | Consistent online SQLite backup to `var/backups/` |
| `send_test_email <address>` | Sends one test email through the active backend (Resend when `RESEND_API_KEY` is set) |

## Testing

```bash
.venv/Scripts/python manage.py test tests
```

The suite (91 tests) covers:

- mastery signals and anti-inflation
- stage transitions and reteach
- the prerequisite graph and unlocking
- FSRS rating, intervals and lapse demotion
- placement estimation, credit and verification
- answer checking and mistake classification
- question validation and the seed's validity
- the evaluation pipeline with a mocked AI
- AI client retries and schema enforcement
- the generation gates
- HTTP flows: registration, gating, an HTMX session, reports, access control and the Studio editors

## Deployment

1. Set `DJANGO_ENV=production`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `SITE_URL` (https) and `DEFAULT_FROM_EMAIL` on a domain verified in Resend.
2. Optional: set `DATABASE_URL` for PostgreSQL (`pip install "psycopg[binary]"`) and `REDIS_URL`.
3. Build and prepare:
   ```bash
   npm ci && npm run build:css
   ```
   ```bash
   python manage.py migrate && python manage.py collectstatic --noinput && python manage.py seed_curriculum && python manage.py ensure_admin
   ```
4. Run the web process:
   ```bash
   gunicorn config.wsgi --workers 3 --timeout 120
   ```
   Put it behind a TLS-terminating proxy that sets `X-Forwarded-Proto`. WhiteNoise serves the static files.
5. With Redis, also run a worker:
   ```bash
   celery -A config worker -l info
   ```
6. Schedule backups daily with cron or Task Scheduler (`python manage.py backup_db`). On PostgreSQL, use `pg_dump`. Keep copies off the server.

Production settings enable:

- HTTPS redirect, HSTS, and secure, HttpOnly, SameSite cookies
- `X-Frame-Options: DENY`, nosniff and a strict referrer policy

`python manage.py check --deploy` passes apart from the optional HSTS preload flag.

## Security notes

- Secrets are read only from the environment. Templates never receive them, and `.env` is git-ignored.
- **CSRF:** CSRF protection is on for every form, and HTMX sends the token from a meta tag.
- **Rate limits:** login, registration, password reset, verification resend, session start, answers, AI explanations and reports are rate-limited.
- **Account emails:** password-reset and verification links are built from `SITE_URL`, never from the request's Host header. Verification tokens are signed, carry the email address and expire after 48 h.
- **Data scoping:** learners can reach only their own sessions and attempts, and can report only questions they have been shown. Admin Studio requires a superuser.
- **AI input and output:** learner text is passed to the AI as delimited, truncated data. AI output is never trusted until it passes a schema and deterministic checks. Unvalidated output never touches mastery.
