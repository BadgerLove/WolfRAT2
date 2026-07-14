path = r'C:\Users\Badger\.openclaw\workspace\wolfcontrol\WolfRAT2_Handoff\source_code\wolfrat\app.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

bad_line = 'self.server.send_chat(f"Vote Complete! {display} wins with {total} vote{\\"s\\" if total != 1 else \\"\\"}!")'
good_line = 'self.server.send_chat(f"Vote Complete! {display} wins with {total} vote{\'s\' if total != 1 else \'\'}!")'

if bad_line in content:
    content = content.replace(bad_line, good_line)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("SUCCESS: Fixed syntax error")
else:
    print("ERROR: Could not find bad line")
