import re

fpath = r'C:\Users\Badger\.openclaw\workspace\wolfcontrol\WolfRAT2_Handoff\source_code\wolfrat\app.py'
with open(fpath, 'r', encoding='utf-8') as f:
    text = f.read()

# Make MessagesTab scrollable
old_messages_tab = """    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- Recurring messages ---"""

new_messages_tab = """    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        
        scroll_content = QWidget()
        layout = QVBoxLayout(scroll_content)
        layout.setContentsMargins(0, 0, 0, 0)

        # --- Recurring messages ---"""

if old_messages_tab in text:
    text = text.replace(old_messages_tab, new_messages_tab)
    
    # We need to change the final lines of MessagesTab._build_ui to add the scroll area to the main layout
    old_end = """        layout.addWidget(log_group)
        log_group.setVisible(False)"""
    
    new_end = """        layout.addWidget(log_group)
        log_group.setVisible(False)
        
        scroll_area.setWidget(scroll_content)
        main_layout.addWidget(scroll_area)"""
    
    text = text.replace(old_end, new_end)
    
    with open(fpath, 'w', encoding='utf-8') as f:
        f.write(text)
    print("Wrapped MessagesTab in QScrollArea.")
else:
    print("Could not find MessagesTab build_ui.")
