# Setup Guide

Detailed setup instructions for different operating systems.

## Prerequisites

- **Python 3.8+** installed
- **Git** installed
- **Telegram account** (free)
- **BotFather bot** access on Telegram
- **Text editor** (VS Code, Notepad, etc.)

## Step 1: Get Your Bot Token

1. **Open Telegram** on your phone or computer
2. **Search for @BotFather** and open the chat
3. **Send `/newbot`**
4. **Follow the prompts:**
   - Choose a display name (e.g., "My Task Bot")
   - Choose a username (must be unique, ends with "_bot", e.g., "my_task_bot_123")
5. **Copy the token** that BotFather gives you (looks like: `123456:ABC-DEF1234...`)
6. **Save this token** - you'll need it in the next steps

## Step 2: Get Your User ID

1. **Search for @userinfobot** on Telegram
2. **Send `/start`**
3. **Copy your User ID** (a number like `123456789`)
4. **Save this** - you'll need it for `.env` configuration

## Step 3: Clone the Repository

### On Windows (PowerShell):
```powershell
git clone https://github.com/YOUR_USERNAME/telegram-task-reminder-bot.git
cd telegram-task-reminder-bot
```

### On macOS/Linux (Terminal):
```bash
git clone https://github.com/YOUR_USERNAME/telegram-task-reminder-bot.git
cd telegram-task-reminder-bot
```

## Step 4: Set Up Python Environment

### Windows (PowerShell):
```powershell
# Create virtual environment
python -m venv .venv

# Activate it
.\.venv\Scripts\Activate.ps1

# If activation fails, try:
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# Then try activation again
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

### macOS/Linux (Terminal):
```bash
# Create virtual environment
python3 -m venv .venv

# Activate it
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Step 5: Configure Environment Variables

1. **Copy the example file:**
   
   **Windows (PowerShell):**
   ```powershell
   Copy-Item .env.example .env
   ```
   
   **macOS/Linux (Terminal):**
   ```bash
   cp .env.example .env
   ```

2. **Edit `.env`** with your preferred text editor:
   - Open `.env` in VS Code, Notepad, or your favorite editor
   - Replace `YOUR_BOT_TOKEN_HERE` with the token from BotFather
   - Replace `YOUR_USER_ID_HERE` with your User ID from userinfobot
   - Keep the `TASK_BOT_DB_PATH` as is (or change only if you know what you're doing)

3. **Example `.env` file:**
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl_zyx...
   OWNER_USER_ID=987654321
   TASK_BOT_DB_PATH=tasks.db
   ```

## Step 6: Test the Bot

### Windows (PowerShell):
```powershell
# Make sure your virtual environment is activated first
.\.venv\Scripts\Activate.ps1

# Run the bot
python bot.py
```

You should see output like:
```
2024-01-15 10:30:45,123 - telegram.ext.Application - INFO - Application started
...
```

### macOS/Linux (Terminal):
```bash
# Make sure your virtual environment is activated first
source .venv/bin/activate

# Run the bot
python3 bot.py
```

## Step 7: Test in Telegram

1. **Open Telegram**
2. **Search for your bot username** (the one you created with BotFather)
3. **Send `/start`**
4. **You should see:**
   ```
   Welcome! I'm your task reminder bot.
   Send /help to see available commands.
   ```

5. **Test creating a task:**
   - Click "New Task"
   - Type a task description
   - Enter a due date

## Step 8: Keep Bot Running

The bot will stay running while the terminal window is open. To keep it running in the background:

### Windows (Background Service)
1. Install **NSSM** (Non-Sucking Service Manager)
2. Run as Administrator:
   ```powershell
   nssm install TelegramTaskBot "C:\path\to\.venv\Scripts\python.exe" "C:\path\to\bot.py"
   nssm start TelegramTaskBot
   ```

### macOS/Linux (Systemd Service)
1. Create `/etc/systemd/system/telegram-task-bot.service`:
   ```ini
   [Unit]
   Description=Telegram Task Reminder Bot
   After=network.target

   [Service]
   Type=simple
   User=your_username
   WorkingDirectory=/path/to/telegram-task-reminder-bot
   ExecStart=/path/to/.venv/bin/python bot.py
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=multi-user.target
   ```

2. Enable and start:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable telegram-task-bot
   sudo systemctl start telegram-task-bot
   ```

### Alternative: Use Screen or Tmux (All Platforms)

**With Screen:**
```bash
screen -S taskbot
source .venv/bin/activate
python bot.py

# Press Ctrl+A then D to detach
# Reattach with: screen -r taskbot
```

**With Tmux:**
```bash
tmux new-session -d -s taskbot "bash -c 'source .venv/bin/activate && python bot.py'"

# View logs: tmux attach-session -t taskbot
```

## Troubleshooting

### Bot doesn't respond

1. **Check the bot is running:**
   - Look for output in the terminal
   - Check for error messages

2. **Check your token:**
   - Make sure it's correct in `.env`
   - Get a new one from BotFather if needed

3. **Check your User ID:**
   - Only you (as owner) see messages
   - Verify ID from userinfobot

4. **Check internet connection:**
   - Bot needs internet to work
   - Check your firewall settings

### "Module not found" errors

1. **Activate your virtual environment:**
   - Windows: `.\.venv\Scripts\Activate.ps1`
   - macOS/Linux: `source .venv/bin/activate`

2. **Reinstall dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

### Database errors

1. **Delete the old database:**
   ```bash
   rm tasks.db  # or del tasks.db on Windows
   ```

2. **Restart the bot** - it will create a new database

### Python version issues

- Minimum Python 3.8 required
- Check your version: `python --version`

## Next Steps

- Read the main [README.md](README.md) for feature documentation
- Check [CONTRIBUTING.md](CONTRIBUTING.md) if you want to help develop
- Look at command documentation: `/help` in the bot

## Still Having Issues?

1. Check the [README.md](README.md) Troubleshooting section
2. Review bot.py comments for implementation details
3. Check python-telegram-bot documentation: https://python-telegram-bot.readthedocs.io/
4. Search existing GitHub issues
5. Create a new issue with detailed error information

---

Happy task reminding! 🚀
