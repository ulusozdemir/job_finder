# Job Finder

Automated LinkedIn job scraping pipeline with AI-powered matching, Telegram notifications, and autonomous job application via an AI agent.

```
LinkedIn search → SQLite → Pre-filter → Gemini AI scoring → Telegram alert → Apply/Reject → AI Agent auto-apply
```

## Setup

### 1. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure API keys

Copy the example env file and fill in your keys:

```bash
cp .env.example .env
```

| Variable | How to get it |
|---|---|
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/apikey) — free |
| `TELEGRAM_BOT_TOKEN` | Message [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` |
| `TELEGRAM_CHAT_ID` | Message [@userinfobot](https://t.me/userinfobot) on Telegram to get your chat ID |
| `LINKEDIN_EMAIL` | Your LinkedIn login email |
| `LINKEDIN_PASSWORD` | Your LinkedIn login password |
| `IMAP_EMAIL` | Email for LinkedIn 2FA code fetching (IMAP) |
| `IMAP_PASSWORD` | App Password for the IMAP email account |

### 3. Edit your profile

Open `profile.yaml` and customize:

- **summary** — describe yourself (used by the AI scorer)
- **skills** — your tech stack
- **searches** — LinkedIn search queries to run
- **must_have_any / deal_breakers** — fast keyword pre-filter rules
- **salary_expectation, english_proficiency, work_authorization** — used by the AI agent when filling forms

### 4. Save LinkedIn session (recommended)

Run once to save login cookies and avoid repeated CAPTCHA challenges:

```bash
python save_linkedin_session.py
```

This creates `linkedin_session.json` which the agent reuses for subsequent runs.

### 5. Run locally

```bash
# Scrape + score + notify
python -m src.main

# Auto-apply to approved jobs
python -m src.applicant.runner
```

### 6. Deploy to GitHub Actions

1. Push this repo to GitHub
2. Go to **Settings → Secrets and variables → Actions**
3. Add secrets: `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `LINKEDIN_EMAIL`, `LINKEDIN_PASSWORD`, `IMAP_EMAIL`, `IMAP_PASSWORD`
4. **Scrape workflow** runs on GitHub Actions every 6 hours
5. **Apply workflow** runs on a self-hosted runner (residential IP for anti-detection)

## How it works

### Scrape pipeline

| Stage | What happens | Cost |
|---|---|---|
| **Scrape** | Fetches public LinkedIn job search results | Free |
| **Dedup** | Skips jobs already in the SQLite database | Free |
| **Pre-filter** | Rejects jobs missing required keywords or containing deal-breakers | Free |
| **AI Score** | Sends surviving jobs to Gemini for 0-100 scoring | Free (Gemini free tier) |
| **Notify** | Sends scored jobs to Telegram with Apply/Reject buttons | Free |

### Auto-apply pipeline

| Stage | What happens |
|---|---|
| **Telegram callback** | User presses "Apply" on a job notification |
| **DB lookup** | Runner fetches job URL from SQLite by job_id |
| **Adapter selection** | Picks the right adapter based on URL (LinkedIn → AI agent, Lever, Greenhouse) |
| **AI Agent** | Browser-use agent navigates the form, fills fields from `profile.yaml`, submits |
| **Result notification** | Telegram message with result (success / fail with Retry button / captcha / closed) |

### Application statuses

| Status | Meaning |
|---|---|
| `not_applied` | Scraped, awaiting user decision |
| `approved` | User pressed "Apply", queued for agent |
| `applied` | Agent successfully submitted the application |
| `failed` | Agent failed — Retry button available in Telegram |
| `captcha` | CAPTCHA blocked — manual apply needed |
| `closed` | Job no longer accepting applications or already applied |

### Key features

- **Anti-detection**: Random delays between applications, daily limit (configurable), realistic browser fingerprint, LinkedIn session reuse
- **CAPTCHA handling**: Detected across all adapters, user notified via Telegram with manual apply link
- **Retry mechanism**: Failed applications get a Retry button in Telegram, deduplicated to prevent double-apply
- **LinkedIn 2FA**: Automatic email verification code fetching via IMAP
- **DB synchronization**: Apply results preserved across scrape/apply workflow runs via backup-restore mechanism
- **Custom form tools**: CDP-based typing, force click, autocomplete handling for dynamic forms (Workday, etc.)

## Project structure

```
job_finder/
├── .github/workflows/
│   ├── scrape.yml                    # Scrape + score + notify (GitHub Actions)
│   └── apply.yml                     # Auto-apply (self-hosted runner)
├── src/
│   ├── scraper/linkedin.py           # LinkedIn public page scraper
│   ├── db/models.py                  # SQLAlchemy Job model
│   ├── db/database.py                # DB engine + session
│   ├── matcher/profile.py            # Profile loader + pre-filter
│   ├── matcher/gemini.py             # Gemini AI scorer
│   ├── notifier/telegram.py          # Telegram bot notifications
│   ├── applicant/
│   │   ├── runner.py                 # Apply orchestrator (polls Telegram, picks adapter)
│   │   ├── agent_adapter.py          # AI agent (browser-use + custom tools)
│   │   ├── linkedin_adapter.py       # LinkedIn rule-based adapter
│   │   ├── lever_adapter.py          # Lever rule-based adapter
│   │   ├── greenhouse_adapter.py     # Greenhouse rule-based adapter
│   │   ├── telegram_poll.py          # Telegram callback polling
│   │   └── base.py                   # Shared types + profile loader
│   └── main.py                       # Scrape pipeline orchestrator
├── profile.yaml                      # Your profile & search config
├── config.py                         # App settings (reads .env)
├── save_linkedin_session.py          # One-time LinkedIn session saver
└── requirements.txt
```

## Workflows

### Scrape (`scrape.yml`)
- **Runs on**: GitHub Actions (schedule: every 6 hours + manual)
- **Does**: Scrape → Score → Notify via Telegram
- **Artifact**: Uploads `jobs.db` for the apply workflow

### Apply (`apply.yml`)
- **Runs on**: Self-hosted runner (schedule: every 30 minutes + manual)
- **Inputs**: `test_url` — optional direct job URL for testing
- **Does**: Downloads scrape DB → Restores local apply statuses → Processes approved/retried jobs → Uploads updated DB
- **DB sync**: Backs up local `apply_status` before downloading scrape DB, then merges back to preserve apply results
