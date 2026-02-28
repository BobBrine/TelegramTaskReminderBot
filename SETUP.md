# Setup Guide

## Prerequisites

- Python 3.10+
- Git
- Telegram account
- Telegram bot token from @BotFather
- Your Telegram user id from @userinfobot
- Optional: Ollama running locally for AI classification

## 1) Clone and install

### Windows (PowerShell)

```powershell
git clone https://github.com/YOUR_USERNAME/bob-task-reminder-bot.git
cd bob-task-reminder-bot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### macOS/Linux

```bash
git clone https://github.com/YOUR_USERNAME/bob-task-reminder-bot.git
cd bob-task-reminder-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2) Configure environment

Copy template:

- Windows: `Copy-Item .env.example .env`
- macOS/Linux: `cp .env.example .env`

Set required fields in `.env`:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
OWNER_USER_ID=your_user_id_here
TASK_BOT_DB_PATH=tasks.db
```

Optional Ollama fields:

```env
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.2
```

## 3) Run bot

```bash
python bot.py
```

## 4) Quick Telegram checks

- Send `/start`
- Send `note: buy milk`
- Send `remind: tmr 10pm call mom`
- Send `edit note`
- Send `edit reminder`

## 5) If Ollama is not running

Bot still works with local fallback classification/parsing.

## 6) Publish to GitHub

```bash
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git add .
git commit -m "Initial GitHub-ready bot project"
git branch -M main
git push -u origin main
```

## 7) CI

This project includes `.github/workflows/ci.yml` that installs dependencies and compiles `bot.py` on push/PR.
