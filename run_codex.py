import subprocess
import shutil
import sys

# codex finden (Windows-kompatibel)
codex = shutil.which("codex") or shutil.which("codex.cmd")
if not codex:
    print("codex nicht im PATH gefunden")
    sys.exit(1)

prompt = (
    "Gib ausschließlich gültigen Python-Code aus. "
    "Keine Erklärungen, kein Markdown. "
    "Nur Code. "
    "Erstelle ein Hello-World-Programm."
)

# Codex starten
result = subprocess.run(
    [codex, "exec"],
    input=prompt,
    capture_output=True,
    text=True,
    encoding="utf-8"
)

code = result.stdout.strip()

# Datei schreiben
with open("hello.py", "w", encoding="utf-8") as f:
    f.write(code + "\n")

print("✅ hello.py wurde erstellt")