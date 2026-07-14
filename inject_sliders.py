import re

fpath = r'C:\Users\Badger\.openclaw\workspace\wolfcontrol\WolfRAT2_Handoff\source_code\wolfrat\app.py'
with open(fpath, 'r', encoding='utf-8') as f:
    text = f.read()

injection = """    def _on_slider_changed(self, key, value):
        if self._loading: return
        self._pending_values[key] = value
        self._debounce_timers[key].start()

    def _send_debounced(self, key):
        if key not in self._pending_values: return
        val = self._pending_values[key]
        self.server.send_command(f"set {key} {val}")
        self._show_feedback(f"Set {key} to {val}")
        del self._pending_values[key]
        self._debounce_timers[key].stop()

    def _show_feedback(self, msg):"""

text = text.replace("    def _show_feedback(self, msg):", injection)

with open(fpath, 'w', encoding='utf-8') as f:
    f.write(text)

print("Injected missing slider functions.")