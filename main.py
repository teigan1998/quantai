"""
INCOME‑AI Orchestrator
======================

This script bootstraps a periodic orchestration loop for the INCOME‑AI
system.  It loads environment variables from a ``.env`` file,
instantiates a ``Memory`` database and a ``MetaAgent`` planner, and
executes the planner at a configurable cadence using the ``schedule``
library.  If the required modules are not available, simple stub
implementations are provided so that the orchestrator can still run
for demonstration purposes.

Configuration
-------------
The following environment variables can be set to customise
behaviour:

``DB_PATH``
    Path to the SQLite database file used by ``Memory``.  Defaults
    to ``./data/income_ai.db``.

``CHECK_INTERVAL_MIN``
    Number of minutes between planner iterations.  Defaults to
    ``30``.

The environment variables are loaded from a ``.env`` file in the
current directory if present via ``python‑dotenv``.

Graceful shutdown is supported via ``Ctrl+C``.
"""

from __future__ import annotations
import os
import sys
import time
import json
import signal
import logging
from dataclasses import dataclass
from typing import Optional, Callable
from dotenv import load_dotenv
import schedule

# --------------------------------------------------------------------------- #
#  Optional self‑improving prompt execution
def run_self_improving_prompt_once(prompt_script_path: str, user_seed: Optional[str] = None) -> None:
    """
    Execute a single iteration of a self‑improving prompt loop.  This helper
    allows the orchestrator to call an external prompt script non‑interactively.

    The external script is expected to honour the ``MAX_ITERATIONS`` environment
    variable and exit after one iteration when it is set to ``1``.  A seed
    prompt may be provided via standard input to prime the prompt.
    """
    if not os.path.exists(prompt_script_path):
        return
    env = os.environ.copy()
    env.setdefault("MAX_ITERATIONS", "1")
    cmd = f'"{sys.executable}" "{prompt_script_path}"'
    seed = (user_seed or "Calibrate on: produce a 3–5 step plan to improve orchestrator reliability.")
    try:
        import subprocess
        proc = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
        out, err = proc.communicate(seed + "\nexit\n", timeout=120)
        logging.getLogger("income_ai.orchestrator").info("[SelfImproving] stdout:\n%s", out)
        if err:
            logging.getLogger("income_ai.orchestrator").warning("[SelfImproving] stderr:\n%s", err)
    except Exception as e:
        logging.getLogger("income_ai.orchestrator").warning("Self-improving prompt run failed: %s", e)

# --------------------------------------------------------------------------- #
#  Fallback stubs when real modules are unavailable
try:
    from core.semantic_memory import Memory  # type: ignore
except Exception:
    class Memory:  # type: ignore
        """Fallback memory stub that logs to stdout."""
        def __init__(self, db_path: str) -> None:
            self.db_path = db_path
        def log(self, level: str, message: str) -> None:
            print(f"[{level}] {message}")

try:
    from core.planner import MetaAgent  # type: ignore
except Exception:
    class MetaAgent:  # type: ignore
        """Fallback planner stub that demonstrates scheduling."""
        def __init__(self, mem: Memory) -> None:
            self.mem = mem
        def loop_once(self) -> None:
            self.mem.log("DEBUG", "Running MetaAgent.loop_once (fallback)")

@dataclass
class Config:
    db_path: str = "./data/income_ai.db"
    interval_min: int = 30
    enable_llm: bool = False
    approvals_enforced: bool = True
    self_improve_each_loop: bool = False

def load_config() -> 'Config':
    """
    Load runtime configuration from environment variables.  Values are read
    from the process environment; see the documentation at the top of this
    module for supported variables.
    """
    load_dotenv()
    return Config(
        db_path=os.getenv("DB_PATH", "./data/income_ai.db"),
        interval_min=int(os.getenv("CHECK_INTERVAL_MIN", "30")),
        enable_llm=os.getenv("ENABLE_LLM", "false").lower() == "true",
        approvals_enforced=os.getenv("ENFORCE_APPROVALS", "true").lower() != "false",
        self_improve_each_loop=os.getenv("SELF_IMPROVE_EACH_LOOP", "false").lower() == "true",
    )

def setup_logging() -> logging.Logger:
    """
    Configure the orchestrator logger.  Logs will be written to a file
    specified via ``LOG_FILE`` if present, otherwise to stderr.  This
    function is idempotent and will only add handlers on the first call.
    """
    logger = logging.getLogger("income_ai.orchestrator")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    log_file = os.getenv("LOG_FILE")
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        fh = logging.FileHandler(log_file)
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    else:
        ch = logging.StreamHandler()
        ch.setFormatter(formatter)
        logger.addHandler(ch)
    return logger

def graceful_killer(logger: logging.Logger):
    """
    Install signal handlers for SIGINT and SIGTERM to allow graceful shutdown.
    Returns a mutable dictionary whose 'flag' key will be set to True on signal.
    """
    stop = {"flag": False}
    def _handler(signum, frame):
        logger.info("Signal %s received, shutting down...", signum)
        stop["flag"] = True
    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)
    return stop

def _run_loop_once(meta: MetaAgent, mem: Memory, cfg: Config, logger: logging.Logger) -> None:
    """
    Execute a single planner iteration with error handling and optional
    self‑improvement.
    """
    try:
        meta.loop_once()
        if cfg.self_improve_each_loop:
            run_self_improving_prompt_once(os.path.join(os.getcwd(), "self_improving_prompt.py"))
    except Exception as exc:
        mem.log("ERROR", f"Loop error: {exc}")
        logger.exception("Planner loop error")

def main() -> None:
    """
    Entry point for the orchestrator.  Loads configuration, initialises
    dependencies and enters a scheduling loop.
    """
    cfg = load_config()
    logger = setup_logging()

    mem = Memory(db_path=cfg.db_path)
    meta = MetaAgent(mem)
    logger.info("INCOME-AI orchestrator starting (interval=%s min, LLM=%s, self_improve=%s)",
                cfg.interval_min, cfg.enable_llm, cfg.self_improve_each_loop)
    schedule.every(cfg.interval_min).minutes.do(lambda: _run_loop_once(meta, mem, cfg, logger))

    stop = graceful_killer(logger)
    try:
        while not stop["flag"]:
            schedule.run_pending()
            time.sleep(1)
    finally:
        mem.log("INFO", "Orchestrator stopped. Goodbye.")
        logger.info("Stopped.")


if __name__ == "__main__":
    main()