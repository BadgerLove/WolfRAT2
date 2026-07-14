import re

path = r'C:\Users\Badger\.openclaw\workspace\wolfcontrol\WolfRAT2_Handoff\source_code\wolfrat\app.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old_block = '''            row_idx, fname = self._map_choices[winner_idx]
            display = fname
            if display.endswith('.bms') or display.endswith('.npj') or display.endswith('.npaj'):
                display = display.rsplit('.', 1)[0]

            self.server.send_chat(f"Vote Complete! {display} wins with {total} vote{"s" if total != 1 else ""}!")
            self.log(f"Winner declared: {display} (Row {row_idx})")
            
            # Queue the map
            self.server.send(f"MISSION SETNEXT {row_idx}")
            self._recently_played.append(fname)'''

new_block = '''            row_idx, fname = self._map_choices[winner_idx]
            display = fname
            if display.endswith('.bms') or display.endswith('.npj') or display.endswith('.npaj'):
                display = display.rsplit('.', 1)[0]

            self.server.send_chat(f"Vote Complete! {display} wins with {total} vote{\\"s\\" if total != 1 else \\"\\"}!")
            
            # --- FIX: Dynamically find the correct index at execution time ---
            mtab = getattr(self, 'missions_tab', None)
            if not mtab and hasattr(self.parent(), 'missions_tab'):
                mtab = self.parent().missions_tab
                
            if mtab:
                row = mtab._find_rotation_row(fname)
                if row >= 0:
                    self.log(f"Winner declared: {display} (Found at Row {row})")
                    self.server.send(f"MISSION SETNEXT {row}")
                else:
                    self.log(f"Winner declared: {display} (Not in rotation, adding first)")
                    import time
                    # Map not in server rotation anymore, add it to the end
                    mtab._send_mission_add_to_server(self.server, fname, 1)
                    time.sleep(0.5)
                    # Its new index will be the current length of the rotation list
                    idx = len(mtab._rotation_maps)
                    self.server.send(f"MISSION SETNEXT {idx}")
                    # Request a refresh so the UI updates with the newly added map
                    self.server.send("mission list")
            else:
                # Fallback if we can't access MissionsTab (shouldn't happen)
                self.log(f"Winner declared: {display} (Fallback queueing)")
                # Just guess it's at the end
                self.server.send(f"MISSION SETNEXT 999")
            # ---------------------------------------------------------------
            
            self._recently_played.append(fname)'''

# Normalize newlines
old_block_norm = old_block.replace('\r\n', '\n')
content_norm = content.replace('\r\n', '\n')

if old_block_norm in content_norm:
    content_new = content_norm.replace(old_block_norm, new_block)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content_new)
    print("SUCCESS: Code updated")
else:
    print("ERROR: Could not find block to replace. Here is the context:")
    print(content_norm[content_norm.find('row_idx, fname = self._map_choices'):content_norm.find('self._recently_played.append(fname)')+50])
