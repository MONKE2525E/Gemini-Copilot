# Contributing to Gemini Desktop Copilot

First off, thank you for considering contributing to Gemini Copilot! It's people like you that make this tool great.

## Development Workflow

Gemini Copilot relies on a specialized build pipeline due to its multi-language architecture. The application bundles a standalone Python executable inside a Rust/Tauri wrapper.

### Prerequisites

To build and test the app locally, you will need:
1.  [Node.js](https://nodejs.org/) (v20 or higher)
2.  [Rust](https://www.rust-lang.org/tools/install)
3.  [Python](https://www.python.org/) (3.10 or higher)

### 1. Setting up the Python Sidecar

The "Engine" layer of the app relies on a Python script (`main.py`) that must be compiled into a `.exe` using PyInstaller before Tauri can bundle the application. **You must recompile the Python executable any time you change Python code.**

```bash
cd python-sidecar
python -m venv .venv

# Windows (Powershell)
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt
pyinstaller --onefile main.py
```
This generates `python-sidecar/dist/main.exe`.

### 2. Setting up the Frontend

The UI layer is built with pure HTML/JS/CSS to remain lightweight.
```bash
# Return to the root directory
cd ..
npm install
```

### 3. Running the App in Development Mode

You can launch the Tauri development server to test changes to the Rust bridge or the frontend UI in real-time:
```bash
npm run tauri dev
```
*Note: Tauri dev will automatically look for the compiled Python sidecar in the `python-sidecar/dist/` directory. If the sidecar is missing, the app will fail to launch.*

## Submitting Pull Requests

1.  **Check the Engineering Log:** Before making architectural changes, please review the `ENGINEERING_LOG.md` to ensure you aren't reverting a known bug fix (like the IPC pipe deadlock fix).
2.  **Describe the Change:** Clearly state what your PR fixes or adds. Include screenshots if you've altered the UI.
3.  **Compile the Sidecar:** Ensure that you do **not** commit your local Python `.venv` or `.pyc` cache files. However, if you've altered the Python code, make sure it compiles cleanly using `pyinstaller`.

## Architectural Overview
Please see `ENGINEERING_LOG.md` for a comprehensive breakdown of how the UI, Rust Bridge, Python Sidecar, and Gemini CLI interact.