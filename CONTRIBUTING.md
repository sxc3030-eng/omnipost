# Contributing to OmniPost

Thank you for your interest in contributing!

## How to Contribute

1. **Fork** the repository
2. **Create** a branch: `git checkout -b feature/my-feature`
3. **Commit** your changes: `git commit -m "Add my feature"`
4. **Push**: `git push origin feature/my-feature`
5. **Open a Pull Request**

## Development Setup

```bash
git clone https://github.com/sxc3030-eng/omnipost.git
cd omnipost
pip install websockets
python omnipost.py
```

## Project Structure

```
omnipost.py              Backend — WebSocket server, post management, AI
omnipost_dashboard.html  Frontend — all UI in a single HTML file
competitor_analyzer.py   Backend — site analysis engine
competitor_analyzer.html Frontend — competitor analysis UI
```

## Guidelines

- Keep the single-file HTML approach (no build tools required)
- Test on Windows 10/11 with Python 3.10+
- No external JS dependencies except for optional Claude API calls
- Comment complex logic in both Python and JavaScript

## Ideas Welcome

- New platform integrations
- More hook templates
- Better viral score algorithm
- Mobile-responsive dashboard
- Dark/light theme toggle

## Bug Reports

Open an issue with:
- OS and Python version
- Steps to reproduce
- Console error message (F12 in browser)
