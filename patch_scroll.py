import re

fpath = r'C:\Users\Badger\.openclaw\workspace\wolfcontrol\WolfRAT2_Handoff\source_code\wolfrat\app.py'
with open(fpath, 'r', encoding='utf-8') as f:
    text = f.read()

old_assemble = """        # ============================================================
        # ASSEMBLE MAIN LAYOUT
        # ============================================================
        main_layout.addLayout(left_col, 2)
        main_layout.addLayout(center_col, 3)
        main_layout.addLayout(far_right_col, 0)"""

new_assemble = """        # ============================================================
        # ASSEMBLE MAIN LAYOUT
        # ============================================================
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        
        scroll_content = QWidget()
        scroll_layout = QHBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.addLayout(left_col, 2)
        scroll_layout.addLayout(center_col, 3)
        
        scroll_content.setMinimumWidth(800)
        scroll_content.setMinimumHeight(650)
        scroll_area.setWidget(scroll_content)

        main_layout.addWidget(scroll_area, 1)
        main_layout.addLayout(far_right_col, 0)"""

if old_assemble in text:
    text = text.replace(old_assemble, new_assemble)
    with open(fpath, 'w', encoding='utf-8') as f:
        f.write(text)
    print("Scroll area patch applied.")
else:
    print("Could not find assemble section.")
