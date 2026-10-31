from __future__ import annotations

import logging
import os
import platform
import re
import sys
from datetime import datetime
from typing import Any, Callable, Optional
from unicodedata import combining, normalize

##################################
# Global Constants & Variables
##################################
# Known problem environments where ANSI/formatting issues occur
PROBLEM_ENVS = ["MOBAEXTRACTONTHEFLY", "MOBANOACL"]

# Global state tracked within the logging module namespace
# Environmental overrides for terminals struggling with Unicode glyphs such as ? (e.g., MobaXterm)
has_terminal_issues: bool = False

# ANSI color codes
RESET = '\033[0m' # Resets color to default

# Original values
RED = '\033[1;31;40m'    # Standard red text, black background, bold weight
GREEN = '\033[1;32m'     # Standard green text, default background, bold weight
YELLOW = '\033[33m'      # Standard yellow text, default background, normal weight
BLUE = '\033[94m'        # Bright blue text, default background, normal weight

# May not work on older/legacy terminals
#RED = '\033[91m'         # Bright red text, default background, normal weight
#GREEN = '\033[92m'       # Bright green text, default background, normal weight
#YELLOW = '\033[93m'      # Bright yellow text, default background, normal weight
#BLUE = '\033[94m'        # Bright blue text, default background, normal weight

# Supported by everything
#RED = '\033[1;31;40m'    # Standard red text, black background, bold weight
#GREEN = '\033[1;32;40m'  # Standard green text, black background, bold weight
#YELLOW = '\033[1;33;40m' # Standard yellow text, black background, bold weight
#BLUE = '\033[1;34;40m'   # Standard dark blue text, black background, bold weight

__all__ = [
    "log",
    "setup_hybrid_logging",
]


##################################
# Classes (Structural and Logging)
##################################
class EasyLogger:
    """
    A simplified logging manager wrapper providing standalone function shortcuts.

    Exposes functional hooks (such as 'log', 'log_warn', 'log_err') globally across the
    script module so that less-experienced developers don't have to manage raw,
    verbose logging object initializations.  This can be extended to other logging
    categories as desired by duplicating the redirect methods below
    """
    def __init__(self, logger_instance: logging.Logger) -> None:
        self._logger = logger_instance


    def set_logger(self, logger_instance: logging.Logger) -> None:
        """Updates the internal logger instance while preserving method references."""
        self._logger = logger_instance

    def __call__(self, message: Any, *args: Any, **kwargs: Any) -> None:
        """
        Maps log("text") directly to logger.info
        that is, define log("text") as a shorthand
        for log.info("text")
        """
        self._logger.info(message, *args, **kwargs)


    def info(self, message: Any, *args: Any, **kwargs: Any) -> None:
        """Redirects log_warn("text") calls to logger.warning"""
        self._logger.info(message, *args, **kwargs)


    def warn(self, message: Any, *args: Any, **kwargs: Any) -> None:
        """Redirects log_warn("text") calls to logger.warning"""
        self._logger.warning(message, *args, **kwargs)


    def error(self, message: Any, *args: Any, **kwargs: Any) -> None:
        """Redirects log_err("text") calls to logger.error"""
        self._logger.error(message, *args, **kwargs)


class PrintRedirector:
    """
    Intercepts and routes standard Python print() streams directly into the log engine.

    Replaces sys.stdout. When a developer executes a raw print() statement, this
    handler intercepts the text stream, strips trailing line breaks to protect against
    empty blank rows, and channels content cleanly into the active root logging handlers.

    DESIGN NOTE: This is a redirection trick. It captures standard 'print()' statements
    and silently pipes them through our logger so they write to the terminal AND the text log file
    at the same time, without changing all 'print' statements to 'logging.info'.
    """
    def __init__(self, logger_func: Any, wrapped_stream: Optional[Any] = None) -> None:
        self.logger_func = logger_func
        self._wrapped_stream = wrapped_stream or sys.__stdout__


    def write(self, buf: str) -> None:
        # Python's print() appends content and trailing newlines sequentially.
        # Strip trailing line breaks to avoid logging empty string rows.
        content = buf.rstrip('\r\n')
        if content:
            self.logger_func(content)


    def flush(self) -> None:
        pass  # Standard log handlers manage their own flushing mechanics


class StripAnsiFilter(logging.Filter):
    """
    Removes terminal formatting expressions before records are written to disk.

    Filters out raw ANSI terminal color declarations (like '\033[1;31;40m') from
    outgoing text lines, keeping written plaintext files entirely safe and clean
    for cross-platform file reading.
    """
    ANSI_REGEX: re.Pattern[str] = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.ANSI_REGEX.sub('', record.msg)
        return True


##################################
# Fallback / Default Log Instances
##################################
# Pre-initialize fallback instances so module-level callers don't throw NameError or NoneType errors
_root_logger = logging.getLogger("royal_caribbean")
easy_log_instance = EasyLogger(_root_logger)

log: EasyLogger = easy_log_instance
log_warn: Callable[..., None] = easy_log_instance.warn
log_err: Callable[..., None] = easy_log_instance.error


def setup_hybrid_logging(log_file_path: Optional[str] = None) -> None:
    """Initializes tracking environment, ANSI terminal capabilities, file captures, and stdout redirection."""
    global has_terminal_issues

    # 1. Determine terminal safety
    has_terminal_issues = any(k in os.environ for k in PROBLEM_ENVS)

    # 2. Safely attempt stream reconfiguration and ANSI enablement on Windows
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except AttributeError:
            has_terminal_issues = True

        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                # 0x0004 = ENABLE_VIRTUAL_TERMINAL_PROCESSING
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass

    # 3. Construct and clear out active root logging context
    #    Use the dedicated package logger ("royal_caribbean") instead of bare root logger
    _root_logger.setLevel(logging.INFO)
    _root_logger.handlers.clear()

    # 4. Terminal Stream Handler (extract real stdout to prevent recursion loops)
    real_stdout = sys.stdout
    while isinstance(real_stdout, PrintRedirector):
        real_stdout = getattr(real_stdout, "_wrapped_stream", None) or sys.__stdout__

    console_handler = logging.StreamHandler(real_stdout)
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    if platform.system() == "iOS":
        console_handler.addFilter(StripAnsiFilter())

    _root_logger.addHandler(console_handler)

    # 5. Plain Text File Handler
    if log_file_path:
        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        delimiter = f"\n{'='*60}\n--- RUN STARTED: {timestamp_str} ---\n{'='*60}\n"

        if platform.system() == "iOS" and not log_file_path.startswith("/"):
            log_file_path = os.path.join(os.path.expanduser("~/Documents"), log_file_path)

        try:
            with open(log_file_path, "a", encoding="utf-8") as f:
                f.write(delimiter)

            file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter("%(message)s"))
            file_handler.addFilter(StripAnsiFilter())
            _root_logger.addHandler(file_handler)
        except IOError as e:
            sys.stderr.write(f"Warning: Could not open log file '{log_file_path}': {e}\n")

    # 6. Update logger reference on the existing wrapper instance
    easy_log_instance.set_logger(_root_logger)

    # 7. Intercept raw standard print statements system-wide with unwrapped real stream reference
    sys.stdout = PrintRedirector(_root_logger.info, wrapped_stream=real_stdout)


def sanitize_string(string_to_clean: str) -> str:
    cleaned_string = string_to_clean.lstrip()

    # TODO: should these all move under if has_terminal_issues?
    cleaned_string = cleaned_string.replace('\u00AE', '(R)')
    cleaned_string = cleaned_string.replace('\u200B', ' ')
    cleaned_string = cleaned_string.replace('\u2013', '-')
    cleaned_string = cleaned_string.replace('\u2014', '-')
    cleaned_string = cleaned_string.replace('\u2018', '`')
    cleaned_string = cleaned_string.replace('\u2019', '\'')
    cleaned_string = cleaned_string.replace('\u201C', '"')
    cleaned_string = cleaned_string.replace('\u201D', '"')
    cleaned_string = cleaned_string.replace('\u2120', '(SM)')

    if has_terminal_issues:
        cleaned_string = cleaned_string.replace('\u2191', '^')
        cleaned_string = cleaned_string.replace('\u2193', 'v')

    cleaned_string = normalize('NFKD', cleaned_string)
    return ''.join([c for c in cleaned_string if not combining(c)])
