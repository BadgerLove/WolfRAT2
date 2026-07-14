"""WolfRAT v2.4.9: Fix First Blood false resets + version bump.
Changes:
1. Add on_missions_updated() to SpreeTab - proper map change detection via <CURRENT MISSION>
2. Remove kill-drop hack from check_sprees (caused false first blood resets on server glitches)
3. Connect missions_signal to spree_tab.on_missions_updated
4. Version bump 2.4.8 -> 2.4.9
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

path = r'C:\Users\Badger\.openclaw\workspace\wolfcontrol\WolfRAT2_Handoff\source_code\wolfrat\app.py'

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# ============================================================
# 1. Add on_missions_updated method to SpreeTab
#    Insert right before "    def check_sprees(self, players):"
# ============================================================
new_method = '''    def on_missions_updated(self, missions):
        """Detect real map changes via <CURRENT MISSION> tag. Reset first blood + streaks."""
        import re
        current = None
        for m in missions:
            if '<CURRENT MISSION>' in m:
                current = m.split(' - ')[0].strip()
                if ':' in current[:5]:
                    current = current.split(':', 1)[1].strip()
                current = re.sub(r'<[^>]*>', '', current).strip()
                break

        if not current:
            return

        if current != getattr(self, '_last_map', None):
            self._last_map = current
            self._first_blood_ready = True
            for stat in self._player_stats.values():
                stat['streak'] = 0
            try:
                from wolfrat.web_server import wire_log
            except ImportError:
                wire_log = lambda m: None
            wire_log(f"[SPREE] Map changed to {current} - first blood + streaks reset")

'''

old_check_sprees = '    def check_sprees(self, players):'
content = content.replace(old_check_sprees, new_method + old_check_sprees, 1)

# ============================================================
# 2. Remove kill-drop hack from check_sprees
#    Remove these 4 lines:
#            if kills < prev['kills']:
#                for stat in self._player_stats.values():
#                    stat['streak'] = 0
#                self._first_blood_ready = True
# ============================================================
old_hack = '''            if kills < prev['kills']:
                for stat in self._player_stats.values():
                    stat['streak'] = 0
                self._first_blood_ready = True
'''
new_replacement = '''            # Map change resets handled by on_missions_updated() (v2.4.9)
'''
content = content.replace(old_hack, new_replacement, 1)

# ============================================================
# 3. Connect missions_signal to spree_tab.on_missions_updated
#    Add after: self.signals.missions_signal.connect(self.map_voting_tab.on_missions_updated)
# ============================================================
old_signal = "        self.signals.missions_signal.connect(self.map_voting_tab.on_missions_updated)"
new_signal = old_signal + "\n        self.signals.missions_signal.connect(self.spree_tab.on_missions_updated)"
content = content.replace(old_signal, new_signal, 1)

# ============================================================
# 4. Version bump 2.4.8 -> 2.4.9
# ============================================================
count = content.count('2.4.8')
content = content.replace('2.4.8', '2.4.9')
print(f"Version: replaced {count} occurrences of 2.4.8 -> 2.4.9")

# Verify changes
assert 'def on_missions_updated(self, missions):' in content, "FAIL: on_missions_updated not found"
assert '_last_map' in content, "FAIL: _last_map not found"
assert 'Map change resets handled by on_missions_updated' in content, "FAIL: kill-drop hack not removed"
assert 'self.spree_tab.on_missions_updated' in content, "FAIL: signal connection not added"
assert '2.4.9' in content, "FAIL: version bump failed"
assert '2.4.8' not in content, "FAIL: old version still present"

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("All changes applied and verified!")
print("Backup at: app.py.bak")
