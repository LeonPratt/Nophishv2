"""Windows windowless startup. Launch with start_nophish.vbs, not a global Python."""

import ctypes
from ctypes import wintypes
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.request import build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parent


def configure_logging():
    directory = ROOT / ".data"
    directory.mkdir(exist_ok=True)
    handler = RotatingFileHandler(directory / "startup.log", maxBytes=1_000_000,
                                  backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # pythonw has no standard streams. Supply valid handles for third-party code.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")


def acquire_instance(name="Local\\NoPhish-127.0.0.1-8000"):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    ctypes.set_last_error(0)
    handle = kernel.CreateMutexW(None, False, name)
    if not handle:
        raise OSError("Cannot acquire startup instance")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS, including during model loading.
        kernel.CloseHandle(handle)
        return None
    return kernel, handle


def port_in_use():
    try:
        with socket.create_connection(("127.0.0.1", 8000), timeout=1):
            return True
    except OSError:
        return False


def ollama_ready():
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open("http://127.0.0.1:11434/api/version", timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


def wait_for_ollama(timeout=60):
    if ollama_ready():
        return True
    executable = shutil.which("ollama")
    if not executable:
        candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
        if candidate.is_file():
            executable = str(candidate)
    if executable:
        logging.info("Starting local Ollama in the background")
        subprocess.Popen([executable, "serve"], cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ollama_ready():
            return True
        time.sleep(1)
    return False


def main():
    os.chdir(ROOT)
    configure_logging()
    instance = None
    try:
        instance = acquire_instance()
        if instance is None:
            return
        if port_in_use():
            logging.info("Port 8000 already in use; no second server started")
            return
        if not wait_for_ollama():
            logging.error("Ollama did not become ready within 60 seconds. Start/install Ollama, then run start_nophish.vbs again.")
            return
        import uvicorn
        logging.info("Starting NoPhish; PID=%d", os.getpid())
        uvicorn.run("server.main:app", host="127.0.0.1", port=8000,
                    access_log=False, log_config=None)
    except Exception as error:
        # Do not serialize exception messages that could contain inference/input data.
        logging.error("NoPhish startup failed (%s). Check dependencies and the local server setup.", type(error).__name__)
    finally:
        if instance:
            kernel, handle = instance
            kernel.CloseHandle(handle)


if __name__ == "__main__":
    main()
