import sys

FILE_PATH = r"C:\Users\Badger\.openclaw\workspace\wolfcontrol\WolfRAT2_Handoff\source_code\wolfrat\app.py"

with open(FILE_PATH, 'r', encoding='utf-8') as f:
    text = f.read()

replacement = """    WEAPON_LIST = [
        "M4A1", "M16A2", "AK-47", "AKS-74U", "G36C", "MP5", "MP5-SD",
        "P90", "UMP45", "Spas12", "USAS12", "M249", "RPK", "Dragunov",
        "Barrett", "M40", "PSG1", "SOCOM", "Glock18", "Beretta", "DEagle",
        "Colt", "M60", "FN FAL", "Steyr", "LR300", "G3A3", "SG552",
        "Binoculars", "C4", "Claymore", "Grenade", "Flashbang", "Smoke",
        "Knife", "MedKit", "Binocular", "RPG", "M203", "GP25",
    ]

    def __init__(self, server):
        super().__init__()
        self.server = server
        self._weapon_rows = {}
"""

target = """    def __init__(self, server):
        super().__init__()
        self.server = server
        
"""

if target in text:
    text = text.replace(target, replacement)
    with open(FILE_PATH, 'w', encoding='utf-8') as f:
        f.write(text)
    print("Fixed WeaponsTab.")
else:
    print("Could not find target text.")
