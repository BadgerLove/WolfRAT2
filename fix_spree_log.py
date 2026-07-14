import re

fpath = r'C:\Users\Badger\.openclaw\workspace\wolfcontrol\WolfRAT2_Handoff\source_code\wolfrat\app.py'
with open(fpath, 'r', encoding='utf-8') as f:
    text = f.read()

# We want to remove the `log_group.setVisible(False)` from SpreeTab.
# Let's split by class definitions and modify only SpreeTab.

parts = text.split("class SpreeTab(QWidget):")
if len(parts) == 2:
    spree_tab_code = parts[1]
    spree_tab_code = spree_tab_code.replace("layout.addWidget(log_group)\n        log_group.setVisible(False)", "layout.addWidget(log_group)")
    text = parts[0] + "class SpreeTab(QWidget):" + spree_tab_code
    with open(fpath, 'w', encoding='utf-8') as f:
        f.write(text)
    print("Restored log_group visibility in SpreeTab.")
else:
    print("Could not find SpreeTab.")
