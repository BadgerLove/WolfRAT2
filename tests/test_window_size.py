"""The main window remembers its size and place between runs (wolfrat_window.json)."""

from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from wolfrat import window_size

_app = QApplication.instance() or QApplication(sys.argv[:1])


def _window(width=1000, height=720):
    w = QWidget()
    w.setMinimumSize(960, 650)
    w.resize(width, height)
    return w


def _small_window():
    # The offscreen test screen is only 800 x 800 (smaller than WolfRAT's
    # 960 x 650 minimum), and restoreGeometry() fits a window to its screen,
    # so the blob round trip is checked with sizes that fit that screen.
    w = QWidget()
    w.resize(400, 300)
    return w


def test_size_survives_a_close_and_reopen(tmp_path):
    path = tmp_path / window_size.FILENAME
    first = _small_window()
    first.resize(640, 480)
    first.move(60, 40)
    window_size.save(path, window_size.capture(first))

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["width"] == 640 and saved["height"] == 480
    assert saved["maximized"] is False
    assert saved["geometry"]

    second = _small_window()                # opens at its default 400 x 300
    assert window_size.restore(second, window_size.load(path)) is True
    assert (second.width(), second.height()) == (640, 480)


def test_a_window_bigger_than_the_screen_is_fitted_to_it(tmp_path):
    # e.g. closed big on a large monitor, opened on a laptop
    path = tmp_path / window_size.FILENAME
    first = _small_window()
    first.resize(1600, 1200)
    window_size.save(path, window_size.capture(first))
    second = _small_window()
    window_size.restore(second, window_size.load(path))
    screen = _app.primaryScreen().availableGeometry()
    assert second.width() <= screen.width() and second.height() <= screen.height()


def test_no_file_keeps_the_default_size(tmp_path):
    w = _window()
    assert window_size.load(tmp_path / window_size.FILENAME) == {}
    assert window_size.restore(w, {}) is False
    assert (w.width(), w.height()) == (1000, 720)


def test_a_broken_file_keeps_the_default_size(tmp_path):
    path = tmp_path / window_size.FILENAME
    for junk in ("", "{not json", "[1, 2]", '"text"'):
        path.write_text(junk, encoding="utf-8")
        w = _window()
        assert window_size.restore(w, window_size.load(path)) is False
        assert (w.width(), w.height()) == (1000, 720)


def test_a_bad_blob_falls_back_to_the_plain_size():
    w = _window()
    state = {"geometry": "!!not base64 geometry!!", "width": 1280, "height": 800}
    assert window_size.restore(w, state) is True
    assert (w.width(), w.height()) == (1280, 800)


def test_silly_plain_sizes_are_ignored():
    for state in ({"width": 5, "height": 5}, {"width": "1280", "height": 800},
                  {"width": True, "height": 800}, {"width": 99999, "height": 800}):
        w = _window()
        assert window_size.restore(w, state) is False
        assert (w.width(), w.height()) == (1000, 720)


def test_never_smaller_than_the_window_minimum():
    w = _window()
    assert window_size.restore(w, {"width": 300, "height": 300}) is True
    assert w.width() >= 960 and w.height() >= 650


def test_maximized_is_remembered_with_its_normal_size(tmp_path):
    path = tmp_path / window_size.FILENAME
    first = _small_window()
    first.resize(640, 480)
    first.showMaximized()
    _app.processEvents()
    if not first.isMaximized():             # offscreen platform may not maximize
        first.close()
        return
    state = window_size.capture(first)
    first.close()
    assert state["maximized"] is True
    assert (state["width"], state["height"]) == (640, 480)

    window_size.save(path, state)
    second = _small_window()
    assert window_size.restore(second, window_size.load(path)) is True
    second.show()
    _app.processEvents()
    assert second.isMaximized()
    second.close()


def test_save_leaves_no_temp_file(tmp_path):
    path = tmp_path / window_size.FILENAME
    window_size.save(path, {"width": 1000, "height": 720})
    window_size.save(path, {"width": 1100, "height": 760})
    assert json.loads(path.read_text(encoding="utf-8"))["width"] == 1100
    assert [p.name for p in tmp_path.iterdir()] == [window_size.FILENAME]
