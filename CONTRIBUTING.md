# Contributing to Telegram Task Reminder Bot

Thank you for your interest in contributing! Here's how you can help.

## Getting Started

### Prerequisites
- Python 3.8+
- Git
- Telegram account with a bot token
- Familiarity with Python and/or Telegram Bot API

### Setting Up Development Environment

1. **Fork the repository** on GitHub

2. **Clone your fork:**
```bash
git clone https://github.com/YOUR_USERNAME/telegram-task-reminder-bot.git
cd telegram-task-reminder-bot
```

3. **Create a virtual environment:**
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .\.venv\Scripts\Activate.ps1
```

4. **Install dependencies:**
```bash
pip install -r requirements.txt
```

5. **Create `.env` file:**
```bash
cp .env.example .env
# Edit .env with your bot token and user ID
```

6. **Test the bot:**
```bash
python bot.py
```

## Development Workflow

### Creating a Feature Branch

```bash
git checkout -b feature/your-feature-name
```

Use descriptive names:
- ✅ `feature/add-task-reminders`
- ✅ `fix/handle-invalid-dates`
- ❌ `fix/stuff`
- ❌ `new-thing`

### Making Changes

1. Make your changes in the feature branch
2. Test thoroughly with your bot
3. Keep commits atomic and well-described

### Commit Messages

Use clear, descriptive commit messages:

```bash
# Good
git commit -m "Add time-based task reminders"
git commit -m "Fix date parsing for edge cases"
git commit -m "Improve message cleanup logic"

# Bad
git commit -m "fixes"
git commit -m "changes"
git commit -m "try this"
```

### Push and Create Pull Request

```bash
git push origin feature/your-feature-name
```

Then create a Pull Request on GitHub with:
- Clear description of changes
- Reference to any related issues
- Example of usage (if applicable)

## Code Style

### Python Style Guide
- Follow PEP 8
- Use 4 spaces for indentation
- Keep lines under 100 characters
- Use descriptive variable names

### Docstrings
Add docstrings to functions:
```python
async def my_function(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Brief description of what the function does.
    
    Args:
        update: The Telegram update
        context: The bot context
    """
```

## Testing

Before submitting a PR:

1. **Test all new features** with your bot
2. **Test existing features** to ensure nothing broke
3. **Check error handling** with invalid inputs
4. **Verify database persistence** - restart bot and check tasks remain

### Test Checklist

- [ ] Feature works as intended
- [ ] Temporary messages are cleaned up properly
- [ ] Main message updates correctly
- [ ] Old messages are deleted on startup
- [ ] Database tracks tasks correctly
- [ ] No errors in console

## Common Areas for Contribution

### Good for Beginners
- [ ] Improve error messages
- [ ] Add more date format examples
- [ ] Update documentation
- [ ] Fix typos in comments

### Intermediate
- [ ] Add task categories
- [ ] Improve error handling
- [ ] Optimize database queries
- [ ] Add logging improvements

### Advanced
- [ ] Time-based reminders
- [ ] Recurring tasks
- [ ] Task persistence improvements
- [ ] Multi-user group chat support

## Reporting Issues

Found a bug? Please create an issue with:

1. **Clear title:** "Bot crashes when entering invalid date"
2. **Description:** What you were trying to do
3. **Steps to reproduce:** How to trigger the bug
4. **Expected behavior:** What should happen
5. **Actual behavior:** What actually happened
6. **Environment:** Python version, OS, etc.

## Feature Requests

Have an idea? Great! Create an issue describing:

1. **Problem:** What problem does this solve?
2. **Solution:** How you'd like it implemented
3. **Examples:** Use cases and examples
4. **Alternatives:** Other possible approaches

## Code Review Process

1. Maintainers will review your PR
2. They may request changes
3. Make updates to your feature branch
4. Changes are automatically reflected in the PR
5. Once approved, your PR will be merged

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

## Questions?

- Check existing issues and PRs
- Read the main README
- Check code comments
- Ask in your PR description

---

Thank you for contributing! 🎉
