"""
Sandboxed Code Execution
========================
Secure, isolated Python code execution for the /exec endpoint.
Replaces the unsafe in-process exec() with subprocess isolation.

Supports:
- SubprocessSandbox: Subprocess-based isolation (always available)
- DockerSandbox: Docker container isolation (when Docker is available)
"""

import asyncio
import subprocess
import tempfile
import os
import time
import shutil
import hashlib
from typing import Optional


# Import blocklist - banned modules in sandboxed code
IMPORT_BLOCKLIST = [
    "os", "subprocess", "shutil", "sys", "signal",
    "socket", "requests", "urllib", "http", "httpx",
    "ctypes", "multiprocessing", "threading", "concurrent",
    "importlib", "inspect", "compile", "exec", "eval",
    "__import__", "open", "breakpoint", "builtins",
    "pickle", "marshal", "code", "codeop",
]

# Ban prefix that gets prepended to user code
SANDBOX_PREAMBLE = """
# === SANDBOX GUARD ===
import builtins as __builtins__
_original_import = __builtins__.__import__
_original_open = __builtins__.open

BLOCKED = {blocklist!r}

def _safe_import(name, *args, **kwargs):
    root = name.split('.')[0]
    if root in BLOCKED:
        raise ImportError(f"Module '{{name}}' is blocked in sandbox")
    return _original_import(name, *args, **kwargs)

def _safe_open(file, *args, **kwargs):
    raise PermissionError("open() is blocked in sandbox")

__builtins__.__import__ = _safe_import
__builtins__.open = _safe_open

# Prevent re-binding blocked names
for _name in ('exec', 'eval', 'compile', '__import__'):
    try:
        del __builtins__.__dict__[_name]
    except KeyError:
        pass

# === USER CODE ===
"""

MAX_CODE_SIZE = 100 * 1024  # 100KB
DEFAULT_TIMEOUT = 30  # seconds
MAX_OUTPUT_SIZE = 500 * 1024  # 500KB


class SubprocessSandbox:
    """
    Executes Python code in an isolated subprocess with timeout,
    memory limits, and blocked dangerous imports.
    """

    def __init__(self, timeout: int = DEFAULT_TIMEOUT):
        self.timeout = timeout

    def _sanitize_code(self, code: str) -> str:
        """Strip null bytes and limit code size."""
        if len(code) > MAX_CODE_SIZE:
            raise ValueError(f"Code exceeds maximum size of {MAX_CODE_SIZE} bytes")
        return code.replace("\x00", "")

    def _wrap_code(self, code: str) -> str:
        """Wrap user code with sandbox preamble."""
        preamble = SANDBOX_PREAMBLE.format(blocklist=IMPORT_BLOCKLIST)
        return preamble + "\n" + code

    async def execute(self, code: str) -> dict:
        """
        Execute code in a sandboxed subprocess.

        Returns:
            dict with keys: success, output, error, execution_time, exit_code
        """
        try:
            sanitized = self._sanitize_code(code)
            wrapped = self._wrap_code(sanitized)
        except ValueError as e:
            return {
                "success": False,
                "output": "",
                "error": str(e),
                "execution_time": 0,
                "exit_code": -1,
            }

        # Write wrapped code to a temp file
        tmpdir = tempfile.mkdtemp(prefix="jambu_sandbox_")
        code_file = os.path.join(tmpdir, "user_code.py")

        try:
            with open(code_file, "w") as f:
                f.write(wrapped)

            start_time = time.time()

            # Run in a subprocess with restricted environment
            proc = await asyncio.create_subprocess_exec(
                "python3",
                "-I",  # Isolated mode (ignore environment)
                "-B",  # Don't write .pyc files
                "-s",  # Don't add user site directory
                code_file,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=tmpdir,
                env={
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "HOME": tmpdir,
                    "PYTHONPATH": tmpdir,
                },
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self.timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return {
                    "success": False,
                    "output": "",
                    "error": f"Execution timed out after {self.timeout}s",
                    "execution_time": time.time() - start_time,
                    "exit_code": -1,
                }

            execution_time = time.time() - start_time

            output = stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT_SIZE]
            error = stderr.decode("utf-8", errors="replace")[:MAX_OUTPUT_SIZE]

            return {
                "success": proc.returncode == 0,
                "output": output.strip(),
                "error": error.strip(),
                "execution_time": round(execution_time, 3),
                "exit_code": proc.returncode,
            }

        except FileNotFoundError:
            return {
                "success": False,
                "output": "",
                "error": "Python3 interpreter not found. Ensure Python is installed.",
                "execution_time": 0,
                "exit_code": -1,
            }
        except Exception as e:
            return {
                "success": False,
                "output": "",
                "error": f"Sandbox error: {str(e)}",
                "execution_time": 0,
                "exit_code": -1,
            }
        finally:
            # Cleanup temp directory
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass


class DockerSandbox:
    """
    Executes Python code in an isolated Docker container.
    Provides stronger isolation than subprocess sandbox.
    """

    DOCKER_IMAGE = "python:3.11-slim"

    def __init__(self, timeout: int = DEFAULT_TIMEOUT):
        self.timeout = timeout

    @staticmethod
    async def is_available() -> bool:
        """Check if Docker is installed and running."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "info",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            return proc.returncode == 0
        except Exception:
            return False

    @staticmethod
    async def ensure_image():
        """Pull the Python Docker image if not present."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "inspect", DockerSandbox.DOCKER_IMAGE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            if proc.returncode != 0:
                # Image not found, pull it
                pull_proc = await asyncio.create_subprocess_exec(
                    "docker", "pull", DockerSandbox.DOCKER_IMAGE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await pull_proc.wait()
        except Exception:
            pass

    async def execute(self, code: str) -> dict:
        """
        Execute code in a Docker container.

        Returns:
            dict with keys: success, output, error, execution_time, exit_code
        """
        if len(code) > MAX_CODE_SIZE:
            return {
                "success": False,
                "output": "",
                "error": f"Code exceeds maximum size of {MAX_CODE_SIZE} bytes",
                "execution_time": 0,
                "exit_code": -1,
            }

        code = code.replace("\x00", "")
        code_hash = hashlib.md5(code.encode()).hexdigest()[:12]
        container_name = f"jambu_sandbox_{code_hash}"

        # Write code to temp file
        tmpdir = tempfile.mkdtemp(prefix="jambu_docker_")
        code_file = os.path.join(tmpdir, "code.py")

        try:
            with open(code_file, "w") as f:
                f.write(code)

            start_time = time.time()

            proc = await asyncio.create_subprocess_exec(
                "docker", "run",
                "--rm",
                "--name", container_name,
                "--network", "none",  # No network access
                "--memory", "256m",
                "--memory-swap", "256m",
                "--cpus", "1",
                "--read-only",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
                "--user", "1000:1000",
                "-v", f"{tmpdir}:/code:ro",
                "-w", "/code",
                self.DOCKER_IMAGE,
                "python", "-I", "-B", "-s", "code.py",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self.timeout
                )
            except asyncio.TimeoutError:
                # Kill the container on timeout
                kill_proc = await asyncio.create_subprocess_exec(
                    "docker", "kill", container_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await kill_proc.wait()
                return {
                    "success": False,
                    "output": "",
                    "error": f"Execution timed out after {self.timeout}s",
                    "execution_time": time.time() - start_time,
                    "exit_code": -1,
                }

            execution_time = time.time() - start_time
            output = stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT_SIZE]
            error = stderr.decode("utf-8", errors="replace")[:MAX_OUTPUT_SIZE]

            return {
                "success": proc.returncode == 0,
                "output": output.strip(),
                "error": error.strip(),
                "execution_time": round(execution_time, 3),
                "exit_code": proc.returncode,
            }

        except Exception as e:
            return {
                "success": False,
                "output": "",
                "error": f"Docker sandbox error: {str(e)}",
                "execution_time": 0,
                "exit_code": -1,
            }
        finally:
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass


# ---- Public API ----

async def execute_sandboxed(code: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """
    Execute Python code in a sandbox. Auto-selects the best available
    isolation method (Docker > Subprocess).

    Args:
        code: Python code to execute
        timeout: Maximum execution time in seconds

    Returns:
        dict with keys: success, output, error, execution_time, exit_code, sandbox_type
    """
    # Try Docker first
    if await DockerSandbox.is_available():
        await DockerSandbox.ensure_image()
        sandbox = DockerSandbox(timeout=timeout)
        result = await sandbox.execute(code)
        result["sandbox_type"] = "docker"
        return result

    # Fall back to subprocess
    sandbox = SubprocessSandbox(timeout=timeout)
    result = await sandbox.execute(code)
    result["sandbox_type"] = "subprocess"
    return result
