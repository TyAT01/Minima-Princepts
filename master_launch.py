import os
import sys
import subprocess
import time
import shutil
import webbrowser
from pathlib import Path
from threading import Thread

def start_ollama():
    """Detects if Ollama is running and starts it if not."""
    print("🌸 Checking Ollama status...")
    # Try to see if ollama is in the path
    ollama_path = shutil.which("ollama")
    if not ollama_path:
        print("⚠️  Ollama not found in system path. Skipping Ollama startup.")
        return

    # Check if ollama serve is already running
    is_running = False
    try:
        if sys.platform == "win32":
            output = subprocess.check_output('tasklist', shell=True).decode()
            is_running = "ollama" in output.lower()
        else:
            subprocess.check_call(["pgrep", "ollama"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            is_running = True
    except (subprocess.CalledProcessError, Exception):
        is_running = False

    if is_running:
        print("✅ Ollama is already running.")
    else:
        print("🚀 Starting Ollama serve...")
        try:
            if sys.platform == "win32":
                subprocess.Popen(["ollama", "serve"], creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:
                with open("ollama_serve.log", "w") as log_file:
                    subprocess.Popen(["ollama", "serve"], stdout=log_file, stderr=log_file, preexec_fn=os.setpgrp)
            print("✅ Ollama serve started in the background.")
        except Exception as e:
            print(f"❌ Failed to start Ollama: {e}")

def setup_venv():
    """Creates or activates a virtual environment in the project root."""
    venv_dir = Path(".venv")
    if not venv_dir.exists():
        print("Creating virtual environment...")
        subprocess.run([sys.executable, "-m", "venv", ".venv"], check=True)
        print("✅ Virtual environment created.")
    else:
        print("✅ Existing virtual environment found.")

    # Return the absolute path to the venv's python executable
    if sys.platform == "win32":
        return (venv_dir / "Scripts" / "python.exe").resolve()
    else:
        return (venv_dir / "bin" / "python").resolve()

def check_dependencies(venv_python):
    """Installs dependencies from Aurelia-AI/requirements.txt."""
    print(f"\n📦 Checking dependencies for Aurelia-AI...")
    req_file = Path("Aurelia-AI") / "requirements.txt"
    if req_file.exists():
        subprocess.run([str(venv_python), "-m", "pip", "install", "-r", str(req_file)], check=True)
        print("✅ Dependencies up to date.")
    else:
        print(f"⚠️  No requirements.txt found in Aurelia-AI")

def launch_app(venv_python):
    """Launches the Aurelia AI application."""
    print(f"\n🎨 Launching Aurelia Vale from Aurelia-AI...")

    # Run browser in a separate thread
    def open_browser():
        time.sleep(10) # Give the server some time to start
        print(f"\n🌐 Opening Web Dashboard at http://localhost:7860")
        webbrowser.open(f"http://localhost:7860")

    Thread(target=open_browser, daemon=True).start()

    # Change to Aurelia-AI directory and run main.py
    os.chdir("Aurelia-AI")
    subprocess.run([str(venv_python), "main.py"])

if __name__ == "__main__":
    print("🌸 Starting Aurelia Vale Master Launcher 🌸")

    # 1. Start Ollama
    start_ollama()

    # 2. Setup Venv
    venv_python = setup_venv()

    # 3. Check dependencies
    check_dependencies(venv_python)

    # 4. Launch
    launch_app(venv_python)
