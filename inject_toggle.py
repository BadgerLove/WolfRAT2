import re

fpath = r'C:\Users\Badger\.openclaw\workspace\wolfcontrol\WolfRAT2_Handoff\source_code\wolfrat\app.py'
with open(fpath, 'r', encoding='utf-8') as f:
    text = f.read()

injection = """    # ---- Settings action helpers ----

    def _on_toggle(self, key, state):
        if self._loading:
            return
        val = "1" if state == 2 else "0"
        self.server.set_setting(key, val)
        label = self.CHECKBOX_SETTINGS.get(key, key)
        self._show_feedback(f"{label} = {'ON' if val == '1' else 'OFF'}")

    def _on_slider(self, key, val):"""

text = text.replace("    def _on_slider(self, key, val):", injection)

with open(fpath, 'w', encoding='utf-8') as f:
    f.write(text)

print("Injected missing _on_toggle function.")