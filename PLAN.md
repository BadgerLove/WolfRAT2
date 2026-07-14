# WolfRAT 2.0 — Complete Rebuild Plan

## Architecture

wolfcontrol/
  main.py                 # Entry point
  config.py               # Settings, server config
  protocol/
    connection.py         # JO server connection + encryption
    commands.py           # All server commands
    parser.py             # Response parsing
  features/
    team_shuffle.py       # Random shuffle at round start
    stats.py              # Player stats database + !stats
    map_rotation.py       # Auto map rotation with pools
    spam_filter.py        # Chat spam detection
    reputation.py         # Teamkiller/griefer tracking
    match_tracker.py      # Match results, win rates
  discord_bot/
    bot.py                # Discord integration
  web/
    server.py             # Built-in web server (LAN)
    templates/dashboard.html
  gui/
    main_window.py        # Main application window
    tabs/
      server.py           # Server connection tab
      players.py          # Player list + admin actions
      maps.py             # Map management + pools
      stats_tab.py        # Stats dashboard
      settings.py         # Server settings
      chat.py             # Chat + moderation
      discord_tab.py      # Discord config
    styles.py             # Dark theme stylesheet (pyqtdarktheme or qt-material)
  data/
    stats.db              # SQLite stats database
    config.json           # Saved settings
  requirements.txt

## GUI Libraries
- pyqtdarktheme or qt-material for modern look
- Qt Style Sheets (QSS) for custom styling
- Compact layout, no stretched fields

## Features
1. Random Team Shuffle (round start)
2. !stats / !rank / !top5 chat commands
3. Map rotation with pools (Small/Medium/Large)
4. Spam detection
5. Reputation system
6. Match stats + win rates
7. Discord bot (status, notifications, !status/!players)
8. Web dashboard (LAN only, port 8080)
