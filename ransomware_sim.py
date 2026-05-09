"""
ransomware_sim.py — Safe ransomware simulator for AXIOM OS Shield demo.
NO REAL ENCRYPTION. Simulates WannaCry enumeration/read/write pattern.
Target: C:\\DemoFiles (Windows) or ~/DemoFiles (Linux/Mac)
Rate: ~50 files/sec — triggers AXIOM distance drop within 10 seconds
"""
import os, sys, time, platform

TARGET = r"C:\DemoFiles" if platform.system() == "Windows" else os.path.expanduser("~/DemoFiles")
EXTENSIONS = {".docx", ".pdf", ".xlsx", ".txt"}
DELAY = 1.0 / 50  # 50 files/sec
STUB = b".demo_encrypted"
ts = lambda: time.strftime("%H:%M:%S", time.localtime())

def find_files():
    found = []
    for root, _, files in os.walk(TARGET):
        for f in files:
            if os.path.splitext(f)[1].lower() in EXTENSIONS:
                found.append(os.path.join(root, f))
    return found

def phase_enumerate(files):
    print(f"[{ts()}] === Phase 1: Enumeration ({len(files)} files) ===")
    deadline = time.time() + 10
    for p in files:
        if time.time() > deadline: break
        print(f"[{ts()}] Enumerating: {p}")
        time.sleep(DELAY)

def phase_read(files):
    print(f"\n[{ts()}] === Phase 2: Read (first 100 bytes) ===")
    deadline = time.time() + 5
    for p in files:
        if time.time() > deadline: break
        try:
            with open(p, "rb") as f: f.read(100)
            print(f"[{ts()}] Reading: {os.path.basename(p)}")
        except OSError: pass
        time.sleep(DELAY)

def phase_write(files):
    print(f"\n[{ts()}] === Phase 3: Write attempt (.demo_encrypted stub) ===")
    t0, deadline = time.time(), time.time() + 3
    for i, p in enumerate(files):
        if time.time() > deadline: break
        try:
            with open(p, "ab") as f: f.write(STUB)
            print(f"[{ts()}] Attempting encrypt: {os.path.basename(p)}")
        except OSError:
            print(f"[{ts()}] BLOCKED: {os.path.basename(p)}")
        elapsed = time.time() - t0
        if os.environ.get("AXIOM_SHIELD_ACTIVE") and elapsed > 1 and elapsed / max(1, i + 1) > 0.5:
            print(f"\n[{ts()}] AXIOM OS Shield detected — demo complete")
            return
        time.sleep(DELAY)

if __name__ == "__main__":
    if not os.path.isdir(TARGET):
        print(f"Target not found: {TARGET}")
        print("Create it with sample .txt/.docx/.pdf/.xlsx files for the demo.")
        sys.exit(1)
    print(f"[{ts()}] ransomware_sim.py — SAFE DEMO (no real encryption)")
    print(f"[{ts()}] Target: {TARGET}\n")
    files = find_files()
    if not files:
        print(f"No target files in {TARGET}"); sys.exit(1)
    phase_enumerate(files)
    phase_read(files)
    phase_write(files)
    print(f"\n[{ts()}] Simulation complete. {len(files)} files targeted.")
