import sys
import json
import base64
import subprocess
import os
import tempfile
from pathlib import Path

try:
    import mss
    MSS_AVAILABLE = True
except ImportError:
    print("WARNING: mss not installed, screenshots disabled", file=sys.stderr)

# Patterns to filter from stderr noise
NOISE_PATTERNS = [
    "MCP issues detected",
    "Failed to connect to IDE companion",
    "To install the extension",
    "error during discovery",
    "connection closed",
    "[IDEClient]",
    "YOLO mode is enabled",
]

SYSTEM_PROMPT = """You are an expert AI assistant following up on a desktop request. 
Instructions:
1. ALWAYS think step-by-step before answering.
2. If you don't know something or need current info, proactively use the google_web_search tool.
3. Be concise but extremely helpful.
4. If the user provides an image or screen context, analyze it carefully.
"""

def is_noise(text):
    """Check if a line of stderr output is just CLI noise"""
    for pattern in NOISE_PATTERNS:
        if pattern in text:
            return True
    return False


class GeminiManager:
    def __init__(self):
        self.session_active = False

    def emit_activity(self, kind, text, request_id):
        """Send an activity update back to Tauri via stdout"""
        if not text or not text.strip():
            return
        packet = {
            'request_id': request_id,
            'type': 'activity',
            'kind': kind,
            'text': text.strip()
        }
        print(json.dumps(packet), flush=True)

    def query(self, prompt, image_data=None, request_id=None):
        """Run gemini CLI in non-interactive headless mode and stream updates"""
        img_path = None
        try:
            if image_data:
                workspace_tmp = Path(__file__).parent.parent / '.tmp'
                workspace_tmp.mkdir(exist_ok=True)
                fd_img, img_path = tempfile.mkstemp(suffix='.png', dir=str(workspace_tmp))
                with os.fdopen(fd_img, 'wb') as f:
                    f.write(base64.b64decode(image_data))
                # Tell the agent to inspect the file at the path and ignore the Gemini UI
                sys_prefix = f"{SYSTEM_PROMPT}\n" if not self.session_active else ""
                full_prompt = f"{sys_prefix}I have attached an image at this path: {img_path}\nPlease view the image file at that path and use it to answer the following request.\nIMPORTANT: the image is a screenshot of my screen. Please ignore the Gemini Desktop application UI (the purple pill, the 'Summarize screen' buttons, etc.) that might be visible in the screenshot. Do not describe or mention it.\nUSER REQUEST: {prompt}"
            else:
                sys_prefix = f"{SYSTEM_PROMPT}\n" if not self.session_active else ""
                full_prompt = f"{sys_prefix}USER REQUEST: {prompt}"
            
            # Use cmd.exe directly (shell=True) with a list of arguments and pass the empty -p
            # This triggers STDIN ingestion mode where we can freely pipe our prompt unquoted
            cmd_body = ['gemini.cmd', '-p', '', '-m', 'gemini-2.5-flash', '-o', 'stream-json', '--yolo']
            
            # Add session memory if active
            if self.session_active:
                cmd_body.append('--resume')
                cmd_body.append('latest')

            print(f"Executing directly: {' '.join(cmd_body)}", file=sys.stderr)

            process = subprocess.Popen(
                cmd_body,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                shell=True
            )
            
            # Write the complete full-length prompt into the STDIN pipe immediately and close it
            process.stdin.write(full_prompt + "\n")
            process.stdin.flush()
            process.stdin.close()
            
            self.session_active = True
            full_response = ""

            def is_noise_line(l):
                noise = ["DEBUG:", "INFO:", "Using model", "Warning:", "Sidecar:"]
                return any(n in l for n in noise)

            stderr_lines = []
            def collect_stderr(pipe):
                for l in iter(pipe.readline, ''):
                    l = l.rstrip()
                    if l:
                        stderr_lines.append(l)
                        print(f"CLI stderr: {l}", file=sys.stderr)

            import threading
            stderr_thread = threading.Thread(target=collect_stderr, args=(process.stderr,), daemon=True)
            stderr_thread.start()

            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue

                # Try to extract any embedded JSON object from the line
                json_start = line.find('{')
                parse_line = line[json_start:] if json_start > 0 else line

                try:
                    data = json.loads(parse_line)
                    msg_type = data.get('type')

                    if msg_type == 'message' and data.get('role') == 'assistant':
                        content = data.get('content', '')
                        if content:
                            full_response += content

                    elif msg_type == 'tool_use':
                        tool_name = data.get('tool_name', 'a tool')
                        self.emit_activity('tool', f"Using {tool_name}", request_id)

                    elif msg_type == 'tool_result':
                        if data.get('status') == 'error':
                            self.emit_activity('thought', f"Tool error: {data.get('tool_id')}", request_id)

                    elif msg_type in ['thought', 'status']:
                        text = data.get('content', data.get('text', ''))
                        if text:
                            self.emit_activity('thought', text, request_id)

                except json.JSONDecodeError:
                    if not is_noise_line(line):
                        print(f"Raw CLI output (non-JSON): {line}", file=sys.stderr)

            process.stdout.close()
            process.wait(timeout=60)
            stderr_thread.join(timeout=5)

            if full_response.strip():
                return full_response.strip()

            important_stderr = [l for l in stderr_lines if not is_noise_line(l)]
            if important_stderr:
                err_msg = important_stderr[-1][:200]
                return f"Error: CLI reported — {err_msg}"
            
            if process.returncode != 0:
                return f"Error: CLI exited with code {process.returncode}"
            
            return "Error: No response generated by Gemini."

        except subprocess.TimeoutExpired:
            process.kill()
            return "Error: Query timed out after 60 seconds."
        except Exception as e:
            return f"Error: {str(e)}"
        finally:
            if img_path and os.path.exists(img_path):
                os.remove(img_path)


def handle_command(command, gemini_mgr):
    cmd_type = command.get('type')
    request_id = command.get('request_id')

    if cmd_type == 'screenshot':
        from mss.tools import to_png
        if not MSS_AVAILABLE:
            return {'request_id': request_id, 'success': False, 'message': 'mss not installed'}
        try:
            with mss.mss() as sct:
                img = sct.grab(sct.monitors[1])
                return {
                    'request_id': request_id,
                    'success': True,
                    'image': base64.b64encode(to_png(img.rgb, img.size)).decode('utf-8'),
                    'message': 'OK'
                }
        except Exception as e:
            return {'request_id': request_id, 'success': False, 'message': str(e)}

    elif cmd_type == 'query':
        prompt = command.get('prompt', '')
        image = command.get('image')
        response = gemini_mgr.query(prompt, image, request_id)
        return {
            'request_id': request_id,
            'success': not response.startswith('Error:'),
            'response': response,
            'message': 'Done'
        }
    
    elif cmd_type == 'reset_session':
        gemini_mgr.session_active = False
        print("Session reset", file=sys.stderr)
        return {'request_id': request_id, 'success': True, 'message': 'Session reset'}

    return {'request_id': request_id, 'success': False, 'message': 'Unknown command'}


def main():
    gemini_mgr = GeminiManager()
    print("Python sidecar started", file=sys.stderr)

    while True:
        line = sys.stdin.readline()
        if not line:
            break
        try:
            command = json.loads(line.strip())
            result = handle_command(command, gemini_mgr)
            print(json.dumps(result), flush=True)
        except Exception as e:
            print(json.dumps({'success': False, 'message': str(e)}), flush=True)


if __name__ == '__main__':
    main()
