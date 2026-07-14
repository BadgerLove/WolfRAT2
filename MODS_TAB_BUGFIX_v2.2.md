# WolfRAT v2.2 - ModsTab Chat Deduplication Bug (2026-06-11)

## The Issues
1. **Startup Replay:** When WolfRAT connected, it would instantly execute every `!switch`, `!kill`, or `!kd` command that was still in the server's chat history.
2. **Commands Ignoring Second Use:** If a player typed `!kd`, it worked once. If they typed `!kd` again later, the bot completely ignored it.

## The Root Cause
The `ModsTab` used a separate chat monitoring function from the `ChatbotTab` and relied on raw text strings for deduplication (`_seen_chat_raw`). 

1. **Why it replayed on startup:** The initialization logic checked `if not self._chat_initialized:`, skipped processing the first batch of messages, but **forgot to add those messages to its "seen" list**. On the very next poll, those messages came in again, weren't in the seen list, and were processed as "new". 
2. **Why commands ignored second use:** Because the game server doesn't attach timestamps, the raw string for a command is identical each time (e.g., `FMJ-BadgerLove: !kd`). When a player typed it the second time, the ModsTab checked its memory, saw that exact string was already processed earlier, and skipped it.

## The Fix
Rewrote the `update_chat` method in `ModsTab` (inside `wolfrat/app.py`) to mirror the main Chatbot tab:
- It now properly saves the initial batch of messages into its seen memory during connection so they aren't processed on the second poll.
- It now uses the unique `msg['id']` assigned by the network protocol layer (`protocol.py`) for deduplication instead of matching raw text strings. This means `FMJ-BadgerLove: !kd` (ID: 45) is treated as a different message than `FMJ-BadgerLove: !kd` (ID: 82), allowing commands to be repeated infinitely.

*Note for future agents/Mimo: Do not revert the `ModsTab` chat processing to raw text deduplication, and do not use `reset_chat()` during the initial connection cycle.*