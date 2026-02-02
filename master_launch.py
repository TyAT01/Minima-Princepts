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
    # This is a bit OS dependent. On Linux/Mac we can use pgrep
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
    """Creates or activates a virtual environment."""
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

def select_version():
    """Asks the user to select which version to run."""
    if "--chroma" in sys.argv:
        return "Aurelia_chroma"
    if "--pp" in sys.argv:
        return "aurelia_chroma_PP"

    print("\n--- 🌸 Select Aurelia Version 🌸 ---")
    print("1) Aurelia Chroma (Standard)")
    print("2) Aurelia Chroma PP (Personaplex)")
    print("-----------------------------------")

    while True:
        choice = input("Enter choice (1 or 2): ").strip()
        if choice == "1":
            return "Aurelia_chroma"
        elif choice == "2":
            return "aurelia_chroma_PP"
        else:
            print("Invalid choice. Please enter 1 or 2.")

def check_dependencies(venv_python, version_dir):
    """Installs dependencies and checks for .env file."""
    print(f"\n📦 Checking dependencies for {version_dir}...")
    req_file = Path(version_dir) / "requirements.txt"
    if req_file.exists():
        subprocess.run([str(venv_python), "-m", "pip", "install", "-r", str(req_file)], check=True)
        print("✅ Dependencies up to date.")
    else:
        print(f"⚠️  No requirements.txt found in {version_dir}")

    # Check for .env file
    env_file = Path(version_dir) / ".env"
    env_example = Path(version_dir) / ".env.example"
    if not env_file.exists() and env_example.exists():
        print(f"📄 .env file missing in {version_dir}. Copying from .env.example...")
        shutil.copy(env_example, env_file)
        print("✅ .env file created. Please edit it with your credentials later.")
    elif not env_file.exists():
        print(f"⚠️  No .env or .env.example found in {version_dir}.")
    else:
        print(f"✅ .env file found in {version_dir}.")

def launch_app(venv_python, version_dir):
    """Launches the AI application and the web dashboard."""
    print(f"\n🎨 Launching Aurelia Vale from {version_dir}...")

    # Try to detect port from .env or config, default to 8000
    port = 8000
    env_path = Path(version_dir) / ".env"
    if env_path.exists():
        with open(env_path, "r") as f:
            for line in f:
                if "AURELIA_VALE_WEB_PORT=" in line.upper():
                    try:
                        port = int(line.split("=")[1].strip())
                    except ValueError:
                        pass

    # Run browser in a separate thread
    def open_browser():
        time.sleep(10) # Give the server some time to start
        print(f"\n🌐 Opening Web Dashboard at http://localhost:{port}")
        webbrowser.open(f"http://localhost:{port}")

    Thread(target=open_browser, daemon=True).start()

    # Change to version directory and run app.py
    os.chdir(version_dir)
    subprocess.run([str(venv_python), "app.py"])

if __name__ == "__main__":
    if "--help" in sys.argv:
        print("🌸 Aurelia Vale Master Launcher 🌸")
        print("Usage: python3 master_launch.py [options]")
        print("Options:")
        print("  --chroma    Launch Aurelia Chroma (Standard)")
        print("  --pp        Launch Aurelia Chroma PP (Personaplex)")
        print("  --help      Show this help message")
        sys.exit(0)

    print("🌸 Starting Aurelia Vale Master Setup 🌸")

    # 1. Start Ollama
    start_ollama()

    # 2. Setup Venv
    venv_python = setup_venv()

    # 3. Select Version
    version_dir = select_version()

    # 4. Check dependencies and .env
    check_dependencies(venv_python, version_dir)

    # 5. Launch
    launch_app(venv_python, version_dir)
