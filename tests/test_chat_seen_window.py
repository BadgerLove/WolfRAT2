"""Old chat must never be processed twice (old mod commands re-fired).

SgtMudd's log: '[MODS] Processed 500 messages' 124 times and old !skip /
!switch / !ping running again every 10 s, because the seen-id trim kept a
random 500 ids instead of the ones still in the chat window.
"""

from types import SimpleNamespace

from wolfrat.app import _forget_chat_ids_outside


def _poll(tab, window):
    fresh = [m for m in window if m["id"] not in tab._seen_chat_ids]
    tab._seen_chat_ids.update(m["id"] for m in fresh)
    _forget_chat_ids_outside(tab, window)
    return fresh


def test_a_long_session_processes_each_chat_line_once():
    tab = SimpleNamespace(_seen_chat_ids=set())
    processed = []
    # Ids well past the set's table size, three new lines per poll, the
    # server window holding the last 500 - the shape of a busy evening.
    messages = [{"id": i, "text": f"line {i}"} for i in range(30000, 36000)]
    for end in range(500, len(messages) + 1, 3):
        window = messages[max(0, end - 500):end]
        processed += [m["id"] for m in _poll(tab, window)]

    assert len(processed) == len(set(processed))
    assert len(tab._seen_chat_ids) <= 500
