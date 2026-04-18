import sys
import json
import base64
import subprocess
import os
import tempfile
import logging
from logging.handlers import RotatingFileHandler
import time
from pathlib import Path

try:
    import mss
    MSS_AVAILABLE = True
except ImportError:
    print("WARNING: mss not installed, screenshots disabled", file=sys.stderr)
    MSS_AVAILABLE = False

# Setup log directory
LOG_DIR = Path.home() / '.gemini' / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / 'sidecar.log'

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=5),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger('GeminiSidecar')

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

def cleanup_old_logs(days=30):
    """Delete log files older than the specified number of days"""
    try:
        now = time.time()
        for f in LOG_DIR.glob('*.log*'):
            if f.is_file() and (now - f.stat().st_mtime) > (days * 86400):
                logger.info(f"Deleting old log file: {f}")
                f.unlink()
    except Exception as e:
        logger.error(f"Failed to cleanup logs: {e}")

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

    def query(self, prompt, image=None, request_id=None):
        """Run gemini CLI in non-interactive headless mode and stream updates"""
        img_path = None
        is_temporary_path = False
        try:
            if image:
                # Target the project-specific temp directory which is ALREADY whitelisted and SPACE-FREE.
                workspace_tmp = Path.home() / '.gemini' / 'tmp' / 'gemini-copilot'
                workspace_tmp.mkdir(parents=True, exist_ok=True)
                
                # Check if 'image' is a file path or base64
                if isinstance(image, str) and (image.startswith("/") or (len(image) > 1 and image[1] == ":")):
                    # It's already a path!
                    img_path = image
                    is_temporary_path = False # Don't delete if it's the 'last_screenshot.png'
                else:
                    # It's base64 data (manual upload)
                    fd_img, img_path = tempfile.mkstemp(suffix='.png', dir=str(workspace_tmp))
                    with os.fdopen(fd_img, 'wb') as f:
                        f.write(base64.b64decode(image))
                    is_temporary_path = True
                
                # Verify the image was correctly written/exists
                if not os.path.exists(img_path) or os.path.getsize(img_path) == 0:
                    raise Exception("Failed to access screenshot or image is empty")

                # Move pixel context to the CLI via @ file inclusion in the -p flag
                cmd_body = [
                    'gemini.cmd', 
                    '--debug',
                    '-m', 'gemini-2.5-flash-lite',
                    '-o', 'stream-json',
                    '--include-directories', str(workspace_tmp),
                    '-p', f'@{img_path}',
                    '--yolo'
                ]
                full_prompt = f"{SYSTEM_PROMPT}\nINSTRUCTIONS: Please analyze the attached image and answer the request. Ignore any visible Gemini UI elements.\nUSER REQUEST: {prompt}"
            else:
                # Include the tmp dir even when no image is explicitly attached, just to be safe if a session requires it later.
                workspace_tmp = Path.home() / '.gemini' / 'tmp' / 'gemini-copilot'
                cmd_body = ['gemini.cmd', '--debug', '-m', 'gemini-2.5-flash-lite', '-o', 'stream-json', '--include-directories', str(workspace_tmp), '--yolo']
                full_prompt = f"{SYSTEM_PROMPT}\nUSER REQUEST: {prompt}"
            
            # Add session memory if active
            if self.session_active:
                cmd_body.append('--resume')
                cmd_body.append('latest')

            logger.info(f"START QUERY [ID: {request_id}]")
            logger.debug(f"Command: {' '.join(cmd_body)}")

            process = subprocess.Popen(
                cmd_body,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                bufsize=1,
                shell=True
            )
            
            process.stdin.write(full_prompt + "\n")
            process.stdin.flush()
            process.stdin.close()
            
            self.session_active = True
            full_response = ""

            def collect_stderr(pipe):
                for line in pipe:
                    l = line.strip()
                    if l:
                        logger.debug(f"CLI STDERR: {l}")

            import threading
            threading.Thread(target=collect_stderr, args=(process.stderr,), daemon=True).start()

            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line: continue
                logger.debug(f"CLI STDOUT: {line}")

                # Look for JSON in the output
                json_start = line.find('{')
                if json_start >= 0:
                    try:
                        data = json.loads(line[json_start:])
                        m_type = data.get('type')
                        
                        if m_type == 'message' and data.get('role') == 'assistant':
                            full_response += data.get('content', '')
                        elif m_type == 'tool_use':
                            self.emit_activity('tool', f"Using {data.get('tool_name')}", request_id)
                        elif m_type == 'thought' or m_type == 'status':
                            text = data.get('content', data.get('text', ''))
                            if text: self.emit_activity('thought', text, request_id)
                        elif m_type == 'result' and data.get('status') == 'error':
                            self.emit_activity('thought', f"Tool error: {data.get('message', 'Unknown')}", request_id)
                    except Exception as je:
                        logger.error(f"JSON Parse Error: {je} from line: {line}")
                else:
                    logger.debug(f"Non-JSON output ignored: {line}")

            process.wait(timeout=180)
            logger.info(f"CLI Exited with code: {process.returncode}")
            
            if full_response.strip():
                return full_response.strip()

            if process.returncode != 0:
                err_msg = f"Error: The Gemini CLI exited with code {process.returncode}. Check path or auth."
                logger.error(err_msg)
                return err_msg
            
            return "Error: No response generated. Try rephrasing."

        except subprocess.TimeoutExpired:
            process.kill()
            logger.error("Query timed out after 180 seconds")
            return "Error: Query timed out after 180 seconds."
        except Exception as e:
            logger.error(f"Sidecar Exception: {str(e)}")
            return f"Error: {str(e)}"
        finally:
            if img_path and is_temporary_path and os.path.exists(img_path):
                try:
                    os.remove(img_path)
                except: pass


def handle_command(command, gemini_mgr):
    cmd_type = command.get('type')
    request_id = command.get('request_id')

    if cmd_type == 'screenshot':
        try:
            workspace_tmp = Path.home() / '.gemini' / 'tmp' / 'gemini-copilot'
            workspace_tmp.mkdir(parents=True, exist_ok=True)
            img_path = workspace_tmp / "last_screenshot.png"

            with mss.mss() as sct:
                monitor = sct.monitors[1]
                sct_img = sct.grab(monitor)
                # Use mss efficient generator
                from mss import tools
                tools.to_png(sct_img.rgb, sct_img.size, output=str(img_path))
                
                logger.info(f"Screenshot saved to {img_path}")
                return {
                    'request_id': request_id,
                    'success': True,
                    'image_path': str(img_path),
                    'message': 'OK'
                }
        except Exception as e:
            logger.error(f"Screenshot failed: {e}")
            return {'request_id': request_id, 'success': False, 'message': str(e)}

    elif cmd_type == 'query':
        prompt = command.get('prompt', '')
        image = command.get('image') # Can be path or base64
        response = gemini_mgr.query(prompt, image, request_id)
        return {
            'request_id': request_id,
            'success': not response.startswith('Error:'),
            'response': response,
            'message': 'Done'
        }
    
    elif cmd_type == 'reset_session':
        gemini_mgr.session_active = False
        return {'request_id': request_id, 'success': True, 'message': 'Session reset'}

    return {'request_id': request_id, 'success': False, 'message': 'Unknown command'}


def to_png(data, size):
    """Helper - deprecated in favor of mss.tools.to_png direct to file"""
    import io
    from mss.tools import to_png
    return to_png(data, size)


def main():
    cleanup_old_logs()
    gemini_mgr = GeminiManager()
    logger.info("Python sidecar started")

    while True:
        line = sys.stdin.readline()
        if not line:
            break
        
        raw_line = line.strip()
        if not raw_line: continue
        logger.debug(f"RAW STDIN: {raw_line[:100]}...")
        
        try:
            command = json.loads(raw_line)
            result = handle_command(command, gemini_mgr)
            
            # Log a small summary of the response
            res_json = json.dumps(result)
            logger.debug(f"SIDE RESPONSE: {res_json[:100]}...")
            print(res_json, flush=True)
        except Exception as e:
            logger.error(f"Failed to handle command: {e}")
            print(json.dumps({'success': False, 'message': str(e)}), flush=True)


if __name__ == '__main__':
    main()


if __name__ == '__main__':
    main()
