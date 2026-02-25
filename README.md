# Telegram Task Reminder Bot 🤖

A clean, efficient Telegram bot for managing your tasks with a minimalist interface. One persistent main message with inline buttons—no clutter, no chaos.

## Features ✨

- **Single Main Message**: All tasks displayed in one persistent message. No spam, no scrolling through dozens of messages
- **Smart Message Cleanup**: Temporary messages (confirmations, errors) are automatically deleted. Only your main task list remains
- **Flexible Date Parsing**: Supports multiple date formats:
  - `0` = today
  - `00` = tomorrow  
  - `DD` = day of this month
  - `DD/MM`, `DD-MM`, `DD.MM` = specific date this year
  - `DD/MM/YYYY` = full date with year
- **Easy Task Management**: 
  - Create tasks with just description + due date
  - Mark tasks as done with one click
  - View completed tasks
  - Clear done tasks
- **Admin-Only Access**: Only the owner (you) can control the bot
- **Persistent Storage**: Tasks saved to SQLite database—survives bot restarts
- **Error Handling**: Invalid inputs show helpful prompts, no cryptic messages
- **Startup Notifications**: Sends a message when bot comes online so you know it's running

## Quick Start 🚀

### 1. Get Your Bot Token
- Open Telegram, find **@BotFather**
- Send `/newbot`
- Follow prompts (choose name and username)
- Copy the token

### 2. Get Your User ID
- Open Telegram, find **@userinfobot**  
- Send `/start`
- Copy your User ID

### 3. Clone & Setup
```bash
git clone https://github.com/YOUR_USERNAME/telegram-task-reminder-bot.git
cd telegram-task-reminder-bot
python -m venv .venv
source .venv/bin/activate  # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
```

### 4. Configure `.env`
Edit the `.env` file:
```
TELEGRAM_BOT_TOKEN=YOUR_TOKEN_FROM_BOTFATHER
OWNER_USER_ID=YOUR_USER_ID
TASK_BOT_DB_PATH=tasks.db
```

### 5. Run
```bash
python bot.py
```

### 6. Test in Telegram
- Search for your bot username
- Send `/start`
- Create a task!

**For detailed setup instructions by OS, see [SETUP.md](SETUP.md)**

## Commands 📋

| Command | Description |
|---------|-------------|
| `/start` | Show tasks and available buttons |
| `/help` | Show help text with all commands |
| `/list` | Show all active tasks |
| `/clear` | Clear all done tasks |
| `/newstart` | Reset conversation (if stuck) |

## Usage Examples 💡

### Creating a Task
1. Click **"New Task"** button
2. Type your task (e.g., "Buy groceries")
3. Enter due date (e.g., `15`, `15/1`, `15/01/2024`)
4. Done! Task appears in main message

### Marking Tasks Complete
- Click the **"✓"** button next to any task

### Viewing Done Tasks
- Click **"Show Done"** button to see completed tasks

### Clearing Done Tasks
- Click **"Clear Done"** button to delete all completed tasks

## Project Structure 📁

```
telegram-task-reminder-bot/
├── bot.py                    # Main bot code (~680 lines)
├── requirements.txt          # Python dependencies
├── .env.example             # Environment template
├── .gitignore               # Git ignore rules
├── LICENSE                  # MIT License
├── README.md                # This file
├── SETUP.md                 # Detailed setup guide
├── CONTRIBUTING.md          # Contribution guidelines
└── tasks.db                 # SQLite database (auto-created)
```

## Configuration 🔧

### Environment Variables

Create a `.env` file based on `.env.example`:

- **TELEGRAM_BOT_TOKEN**: Your bot token from @BotFather
  - How to get: https://core.telegram.org/bots/tutorial
  
- **OWNER_USER_ID**: Your personal Telegram user ID
  - How to get: Send `/start` to @userinfobot
  - Only this user ID can control the bot
  
- **TASK_BOT_DB_PATH**: Path to SQLite database
  - Default: `tasks.db` in bot directory
  - Can be absolute or relative path

### Database
- Automatically created on first run
- SQLite format (no setup needed)
- Stores all tasks with due dates and completion status
- Survives bot restarts

## How It Works 🔍

### Message Management
1. **Main Message**: Displays all active tasks with buttons
2. **Temporary Messages**: Confirmations and errors auto-delete after action
3. **Clean Flow**: Every action follows this pattern:
   - Clear temporary messages
   - Show result
   - Update main message with latest tasks

### Task Storage
- Tasks stored in SQLite database
- Each task has: ID, owner ID, description, due date, done status
- Database auto-migrates schema if needed (backwards compatible)

### Tracking System
- Temporary message IDs tracked in memory
- Automatically deleted when action completes
- Main message preserved (never auto-deleted)

## Keyboard Layout

```
┌─────────────────────────────────┐
│  Your Task List (Main Message)  │
│                                 │
│ 🟢 Task 1 (due: Jan 15, Mon)   │ ─ [✓]
│ 🟢 Task 2 (due: Tomorrow)      │ ─ [✓]
│ 🟢 Task 3 (due: Today)         │ ─ [✓]
│                                 │
├─────────────────────────────────┤
│ [New Task]  [Show Done]         │
│ [Clear Done] [Help]             │
└─────────────────────────────────┘
```

## Troubleshooting 🔧

### Bot doesn't respond to `/start`

**Check:**
1. Bot token in `.env` is correct (from @BotFather)
2. User ID in `.env` matches your ID (from @userinfobot)
3. Bot is running: check terminal for error messages
4. Only the owner (your user ID) can use the bot

**Fix:**
- Get new token from @BotFather
- Verify your user ID with @userinfobot
- Restart bot: `python bot.py`

### "Module not found" error

**Check:**
1. Virtual environment activated: 
   - Windows: `.\.venv\Scripts\Activate.ps1`
   - macOS/Linux: `source .venv/bin/activate`
2. Dependencies installed: `pip install -r requirements.txt`

**Fix:**
```bash
pip install -r requirements.txt --force-reinstall
```

### Tasks disappear after restart

**Check:**
1. `TASK_BOT_DB_PATH` in `.env` is set to `tasks.db` (or your custom path)
2. `tasks.db` file exists in the bot directory

**Fix:**
- Delete `tasks.db` and restart bot (creates new database)
- Check file permissions (should be readable/writable)

### Buttons don't work

**Check:**
1. You're chatting with the right bot (search by username, not bot name)
2. Bot is running in terminal
3. No Python errors in terminal output

**Fix:**
- Restart bot with `python bot.py`
- Check terminal for stack traces
- Ensure `.env` variables are correct

## Security Notes 🔒

### Sensitive Information
- **NEVER commit `.env`** file to Git (it's in `.gitignore`)
- **NEVER share your bot token** - it controls your bot
- **NEVER share your user ID** if giving others access to code
- The `.env.example` is safe to commit (it's a template)

### Before Publishing
1. **Rotate your bot token**:
   - Open @BotFather
   - Find your bot
   - Click "Revoke old token"
   - Get new token
2. Update `.env` with new token
3. Restart bot

### Access Control
- Only `OWNER_USER_ID` can use the bot
- Delete bot's chat history if giving code to others
- Disable bot via @BotFather if compromised

## Requirements 📦

- Python 3.8+
- Dependencies (auto-installed):
  - `python-telegram-bot==22.2` - Telegram API wrapper
  - `python-dotenv==1.0.1` - Load environment variables
  - `python-dateutil==2.9.0.post0` - Flexible date parsing

See `requirements.txt` for exact versions.

## Installation Methods 💾

### Quick Install (Recommended)
```bash
git clone https://github.com/YOUR_USERNAME/telegram-task-reminder-bot.git
cd telegram-task-reminder-bot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your token and user ID
python bot.py
```

### Keep Bot Running

**Background (macOS/Linux):**
```bash
nohup python bot.py > bot.log 2>&1 &
```

**Systemd Service (macOS/Linux):**
See [SETUP.md](SETUP.md) for complete systemd setup.

**Screen/Tmux (All platforms):**
See [SETUP.md](SETUP.md) for screen/tmux sessions.

## Contributing 🤝

Want to help? We'd love contributions!

- **Found a bug?** Create an issue with steps to reproduce
- **Have an idea?** Suggest it in an issue
- **Want to code?** See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines

**Common contribution areas:**
- Improve error messages
- Add new task management features
- Enhance date parsing
- Improve documentation
- Add support for recurring tasks
- Add task categories

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full process.

## Development 💻

### Project Stats
- **Lines of Code**: ~680 (bot.py)
- **Languages**: Python 3
- **Dependencies**: 3 main packages
- **Database**: SQLite (file-based)
- **Architecture**: Async/await, ConversationHandler pattern

### Key Functions
- `send_main_message()` - Send/refresh task list
- `add_task()` - Add new task to database
- `mark_task_done()` - Complete a task
- `parse_due_date()` - Flexible date parsing
- `on_button()` - Handle button clicks
- `on_text()` - Handle text input for task creation

### Code Quality
- Error handling with fallbacks
- Database schema migrations
- Graceful shutdown support
- Comprehensive comments

## Roadmap 🗺️

### Planned Features
- [ ] Task reminders at due date
- [ ] Recurring tasks (daily, weekly, monthly)
- [ ] Task categories/tags
- [ ] Priority levels
- [ ] Telegram group chat support
- [ ] Task notes/descriptions
- [ ] Due date notifications
- [ ] Task search/filter

### Future Improvements
- [ ] Web dashboard
- [ ] Multi-user teams
- [ ] Task templates
- [ ] Subtasks
- [ ] Time estimates
- [ ] Productivity analytics

## License 📜

MIT License - See [LICENSE](LICENSE) for details.

This means:
- ✅ Use for personal projects
- ✅ Use in commercial projects  
- ✅ Modify the code
- ✅ Distribute copies
- ❌ Hold the creator liable
- ❌ Use trademark

## Support 💬

### Getting Help
1. **Check [SETUP.md](SETUP.md)** for detailed installation help
2. **Read `.env.example`** comments for configuration details
3. **Search existing issues** on GitHub
4. **Create an issue** with:
   - What you're trying to do
   - Steps to reproduce
   - Error messages (full terminal output)
   - Your OS and Python version

### Quick Links
- **Telegram Bot API Docs**: https://core.telegram.org/bots/api
- **python-telegram-bot Docs**: https://python-telegram-bot.readthedocs.io/
- **Python DateUtil Docs**: https://dateutil.readthedocs.io/

## Authors & Credits 🙏

- Created with ❤️ for Telegram bot enthusiasts
- Built with [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- Database with [SQLite](https://www.sqlite.org/)

## Changelog 📝

### Latest (v1.0.0)
- ✅ Full task management system
- ✅ Single main message UI  
- ✅ Auto message cleanup
- ✅ Flexible date parsing
- ✅ Persistent storage
- ✅ Error handling with suggestions
- ✅ Startup notifications
- ✅ Admin-only access

---

**Ready to get started?** See [Quick Start](#quick-start-) section above or [SETUP.md](SETUP.md) for detailed instructions.

**Want to contribute?** See [CONTRIBUTING.md](CONTRIBUTING.md).

**Found an issue?** Create one on GitHub!

Happy task managing! 🎉
