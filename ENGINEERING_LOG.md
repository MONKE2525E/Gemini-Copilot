# Gemini Desktop Copilot: Engineering & Incident Log

This document serves as both a detailed architectural map of the system and a historical log of bugs encountered and their exact fixes. When maintaining or expanding the project, reference this log to understand *why* certain non-obvious engineering decisions were made.

---

## 1. Detailed System Architecture

The application follows a four-layer architecture to ensure modularity and crash resilience. This is essentially an IPC (Inter-Process Communication) pipeline spanning from the browser DOM down to the terminal.

### 1. UI Layer (HTML5/JS/CSS)
*   **Location:** `src/` (Specifically `src/index.html` and `src/main.js`)
*   **Role:** A slim, static Tauri-based web view. It handles the glassmorphism aesthetic, user input, and real-time activity streaming (activity bubbles). 
*   **Key Behavior:** The UI interacts with the system exclusively through Tauri `invoke` calls (e.g., `invoke('query_gemini')`). It relies heavily on vanilla JavaScript and CSS to keep the bundle size small and performance high.

### 2. Bridge Layer (Rust/Tauri)
*   **Location:** `src-tauri/src/lib.rs`
*   **Role:** The core system orchestrator. Rust handles native OS integrations that the browser engine cannot.
*   **Key Behaviors:**
    *   **Sidecar Management:** Spawns the compiled Python executable (`main.exe`) as a child process and attaches to its `STDIN` and `STDOUT` pipes.
    *   **Single-Instance Enforcement:** Prevents ghost processes/memory leaks. If a user presses the shortcut, it focuses the existing instance instead of booting a new one.
    *   **Global Shortcuts:** Uses the `tauri-plugin-global-shortcut` to listen for `Alt+Space` globally on the OS.
    *   **Async Dispatch:** Translates asynchronous Tauri invokes into JSON packets, writes them to the Python process's STDIN, and uses `oneshot` channels to link the eventual STDOUT response back to the correct frontend promise.
    *   **File Interception:** Bypasses Tauri v2's strict local `asset://` Content Security Policy by intercepting image paths and converting them into lightweight Base64 strings before returning them to the UI.

### 3. Engine Layer (Python Sidecar)
*   **Location:** `python-sidecar/` (Specifically `python-sidecar/main.py`)
*   **Role:** A stateful, robust wrapper for the Gemini CLI. It acts as the "brawn" of the operation.
*   **Why Python?** Python provides unparalleled access to robust screenshot libraries (`mss`) and native subprocess management across platforms without fighting Rust's strict safety rules for quick CLI orchestration.
*   **Key Behaviors:**
    *   **Vision:** Executes high-speed screen capture using the `mss` library, saving it to disk.
    *   **Orchestration:** Reads the JSON from STDIN, constructs the proper command-line arguments for the `gemini.cmd` application, executes it, and streams the output back to Rust.
    *   **Compilation:** This layer is bundled into a standalone `main.exe` using PyInstaller. **Users do not need Python installed on their machines**, as PyInstaller bundles the Python runtime directly into the executable.

### 4. Intelligence Layer (Gemini CLI)
*   **Role:** The raw `@google/gemini-cli`. 
*   **Key Behaviors:** Executes the LLM model, handles MCP tool calls, and performs web searches. The sidecar interacts with this layer purely through standard shell execution.

---

## 2. Communication Protocol (The "Pipes")
Interaction between layers is strictly asynchronous and uses **JSON Lines** over `STDIN`/`STDOUT`.

### Workflow of a Query:
1.  **Frontend**: `invoke('query_gemini', { prompt, image })`.
2.  **Rust**: Generates a unique `UUID` for the request.
3.  **Rust → Python (STDIN)**: Writes `{"type": "query", "request_id": "...", "prompt": "...", "image": "..."}`.
4.  **Python**: Executes `gemini.cmd` and streams its output.
5.  **Python → Rust (STDOUT)**: 
    *   Streams `activity` packets: `{"type": "activity", "kind": "thought", "text": "..."}`
    *   Final response: `{"type": "query", "success": true, "response": "..."}`
6.  **Rust**: Resolves the `oneshot` channel associated with the `UUID` and returns it to the frontend.
7.  **Frontend**: Receives the object and renders the Markdown.

---

## 3. Incident Log & Key Engineering Fixes

### A. Pipe Deadlock Mitigation (Unclogged Pipes)
**Problem**: Sending 30MB+ of base64-encoded pixels (4K screenshots) through standard OS STDIN/STDOUT pipes caused the Python engine's buffer to block, leading to infinite "Thinking" hangs.
**Solution**: Switched to **File-Based Ingestion**. 
- The sidecar saves the screenshot to `~/.gemini/tmp/gemini-copilot-1/last_screenshot.png`.
- The sidecar passes the *file path* to Tauri and the CLI instead of raw pixels.
- This reduces pipe traffic by 99.9%, ensuring the "Thinking" state triggers instantly.

### B. Single-Instance Enforcement
**Problem**: Multiple app instances launched side-by-side led to conflicting logs and CLI sessions.
**Solution**: Integrated `tauri-plugin-single-instance`. Launching a second instance now simply focuses the existing window and exits the new process immediately.

### C. Persistent Session Memory
**Problem**: Each query felt like a new conversation, dropping prior context.
**Solution**: Implemented `--resume latest` logic in the sidecar. The sidecar tracks an internal `session_active` boolean and automatically tells the CLI to append new messages to the existing thread until the "Reset Session" UI button is clicked.

### D. Whitelisted, Space-Free Paths
**Problem**: Windows paths with spaces (e.g., `Program Files`) often break CLI argument parsing.
**Solution**: All temporary files and logs are hardcoded to store in `~/.gemini/`, which is consistently whitelisted by the CLI and generally free of problematic character sequences.

### E. Suggestion Chip "Thinking" Loop
**Problem**: Clicking quick action buttons ("Extract text", "Explain this") caused the UI to enter a permanent "Thinking..." state. The state lock was enabled before `handleSubmit` was called, which then returned early because the lock was active.
**Solution**: Temporarily unlocked the processing state right before calling `handleSubmit()` in `main.js` to ensure the form submission sequence could proceed.

### F. Screenshot Missing Module Binding
**Problem**: The screenshot function crashed because the `mss` local module was shadowed by a local `import mss.tools` statement inside the function block, causing a `UnboundLocalError`.
**Solution**: Changed the import syntax to `from mss import tools` to safely load the module without overriding the parent `mss` scope.

### G. Image Context Syntax
**Problem**: Screenshots were failing to process because they were sent to the Gemini CLI as a positional string argument or with phantom flags (`--image`/`--context`).
**Solution**: Modified the Python sidecar to use the CLI's native `@path` file inclusion syntax directly within the prompt string (e.g., `\n\n@C:/path/to/screenshot.png`).

### H. UTF-8 Output Decoding (Windows)
**Problem**: The "Extract text" command often crashed the Python engine on Windows with a `charmap codec can't decode byte` error because Python defaulted to CP1252 encoding when reading standard output.
**Solution**: Enforced `encoding='utf-8'` on the `subprocess.Popen` execution to correctly handle rich CLI text/emojis.

### I. Tauri Local Asset Policy
**Problem**: Tauri v2 restricts local file protocol access (`asset://`) for the UI, preventing the screenshot from rendering.
**Solution**: Instead of dealing with complex CSP scopes, the Rust backend (`lib.rs`) automatically intercepts the screenshot path, encodes the file to a lightweight Base64 string, and injects it back into the JSON payload for immediate UI rendering.

### J. Workspace Whitelist Path Error
**Problem**: The Gemini CLI rejected file processing requests with a `Path not in workspace` error. The CLI's security model automatically whitelists the project root and `~/.gemini/tmp/[PROJECT_NAME]`, but the sidecar was hardcoded to save screenshots to a mismatched `gemini-copilot-1` directory.
**Solution**: Renamed the sidecar's temporary directory to exactly match the project name (`gemini-copilot`), ensuring it falls within the CLI's default temporary whitelist zone, and explicitly injected the `--include-directories` flag into the `gemini.cmd` execution sequence.

### K. Headless `@` File Inclusion Parser
**Problem**: The Gemini CLI's custom `@` file inclusion syntax does not parse if the `@` tag is piped via Standard Input (`STDIN`) during headless mode. This caused the CLI to interpret the image path as literal text rather than an image attachment, leading to responses like "I cannot see the image". Furthermore, Windows paths containing spaces and nested quotes caused severe command-line mangling due to `cmd.exe` list-to-string coercion.
**Solution**: Migrated the image path out of the `STDIN` text blob and into the `-p` (prompt) flag directly on the `cmd_body` list (e.g., `-p @C:/path.png`), omitting double quotes to let Python's `subprocess.list2cmdline` safely escape Windows paths without triggering Node.js parser failures.