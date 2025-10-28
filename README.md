# Brawl Stars Tournament Bot

A Telegram bot for managing Brawl Stars tournaments with team registration, bracket generation, and roster management.

## Features
- Tournament creation and management
- Team registration with photo rosters
- Automatic bracket generation
- Admin controls
- SQLite database storage

## Deployment on Render

1. Fork this repository
2. Go to [Render.com](https://render.com)
3. Create a new Web Service
4. Connect your GitHub repository
5. Use these settings:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python bot.py`
6. Add environment variable:
   - `BOT_TOKEN` = your Telegram bot token

## Admin Setup
Edit `ADMINS` in `bot.py` with your Telegram user ID.
