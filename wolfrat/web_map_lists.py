"""Saved map lists on the phone page: what the page is told (plain data, no Qt).

The Missions tab does the work (``MissionsTab.phone_lists`` /
``phone_load_list``, the same feed as its Load button); this turns what it
keeps into the page's ``map_lists`` block:

    {"lists": [{"name", "maps", "missing"}],
     "busy": {"label", "done", "total"} | None,
     "last": {"id", "name", "mode", "state", "text", "tone"} | None}

``busy`` is any feed putting maps on the server (a load from the PC or an
Edit Order too): the page's buttons wait for it.  ``last`` is how the newest
load from a phone went.
"""

from __future__ import annotations

SHOWN_NAMES = 4      # map files named before "and N more"
MODES = ("add", "replace")


def maps_text(count):
    return f"{count} map" if count == 1 else f"{count} maps"


def names_text(files):
    files = [str(name) for name in files]
    shown = ", ".join(files[:SHOWN_NAMES])
    more = len(files) - SHOWN_NAMES
    return f"{shown} and {more} more" if more > 0 else shown


def summary(last):
    """How the newest load from a phone went, for the page; None for none."""
    if not isinstance(last, dict):
        return None
    name = str(last.get("name", ""))
    mode = "replace" if last.get("mode") == "replace" else "add"
    state = last.get("state")
    total = int(last.get("total") or 0)
    failed = list(last.get("failed") or ())
    missing = list(last.get("missing") or ())
    added = max(0, total - len(failed))
    if state == "running":
        text, tone = "", "info"
    elif state == "done":
        if mode == "replace":
            text = f"The rotation is now '{name}': {maps_text(added)}."
        else:
            text = f"'{name}' was added to the end: {maps_text(added)}."
        tone = "ok"
        if failed:
            text += f" The server would not add {names_text(failed)}."
            tone = "warn"
        if missing:
            text += f" Left out, not on the server: {names_text(missing)}."
            tone = "warn"
    else:
        tone = "bad"
        if last.get("lost"):
            text = (f"The connection to the server was lost: {added} of "
                    f"{maps_text(total)} from '{name}' went on.")
        else:
            reason = " ".join(str(last.get("error") or "").split()).rstrip(".")
            text = f"'{name}' was not loaded" + (f": {reason}." if reason else ".")
        state = "stopped"
    return {"id": int(last.get("id") or 0), "name": name, "mode": mode,
            "state": state, "text": text, "tone": tone}


def busy(feed):
    """The feed running now ({"label", "done", "total"}), or None."""
    if not isinstance(feed, dict):
        return None
    return {"label": str(feed.get("label", "")),
            "done": int(feed.get("done") or 0),
            "total": int(feed.get("total") or 0)}


def block(lists, feed, last):
    return {"lists": list(lists or ()), "busy": busy(feed), "last": summary(last)}
