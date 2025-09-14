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

# ---------- Safe imports with stubs ----------
try:
    from core.semantic_memory import Memory  # type: ignore
except Exception:
    class Memory:  # type: ignore
        def __init__(self, db_path: str) -> None:
            self.db_path = db_path
        def log(self, level: str, message: str) -> None:
            print(f"[{level}] {message}")

try:
    from core.planner import MetaAgent  # type: ignore
except Exception:
    class MetaAgent:  # type: ignore
        def __init__(self, mem: Memory) -> None:
            self.mem = mem
        def loop_once(self) -> None:
            self.mem.log("DEBUG", "Running MetaAgent.loop_once (stub)")


# ---------- Optional: Self-Improving Prompt Loop as a callable ----------
def run_self_improving_prompt_once(prompt_script_path: str, user_seed: Optional[str] = None) -> None:
    """
    Executes a single iteration of the self-improving loop by invoking the script
    with a seed input (non-interactive). If the script is not present, this is a no-op.
    """
    if not os.path.exists(prompt_script_path):
        return
    # Run a single iteration by setting MAX_ITERATIONS=1 and providing a seed via STDIN.
    env = os.environ.copy()
    env.setdefault("MAX_ITERATIONS", "1")
    # Use the mock client by default unless the user enables real LLM via env flags.
    cmd = f'"{sys.executable}" "{prompt_script_path}"'
    seed = (user_seed or "Calibrate on: produce a 3–5 step plan to improve orchestrator reliability.")
    try:
        import subprocess
        proc = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
        out, err = proc.communicate(seed + "\nexit\n", timeout=120)
        logging.getLogger("orchestrator").info("[SelfImproving] stdout:\n%s", out)
        if err:
            logging.getLogger("orchestrator").warning("[SelfImproving] stderr:\n%s", err)
    except Exception as e:
        logging.getLogger("orchestrator").warning("Self-improving prompt run failed: %s", e)


# ---------- Orchestrator ----------
@dataclass
class Config:
    db_path: str = "./data/income_ai.db"
    interval_min: int = 30
    auto_process_all: bool = False
    enable_llm: bool = False
    approvals_enforced: bool = True
    run_self_improving_each_loop: bool = False


def load_config() -> Config:
    load_dotenv()
    return Config(
        db_path=os.getenv("DB_PATH", "./data/income_ai.db"),
        interval_min=int(os.getenv("CHECK_INTERVAL_MIN", "30")),
        auto_process_all=os.getenv("AUTO_PROCESS_ALL", "false").lower() == "true",
        enable_llm=os.getenv("ENABLE_LLM", "false").lower() == "true" or os.getenv("USE_REAL_OPENAI", "false").lower() == "true",
        approvals_enforced=os.getenv("ENFORCE_APPROVALS", "true").lower() == "true",
        run_self_improving_each_loop=os.getenv("SELF_IMPROVE_EACH_LOOP", "false").lower() == "true",
    )


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("orchestrator")
    logger.setLevel(logging.INFO)
    os.makedirs("logs", exist_ok=True)
    fh = logging.FileHandler("logs/orchestrator.log", encoding="utf-8")
    ch = logging.StreamHandler(sys.stdout)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    fh.setFormatter(fmt); ch.setFormatter(fmt)
    logger.addHandler(fh); logger.addHandler(ch)
    return logger


def graceful_killer(logger: logging.Logger):
    stop = {"flag": False}
    def _handler(signum, frame):
        logger.info("Signal %s received, shutting down...", signum)
        stop["flag"] = True
    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)
    return stop


def _run_loop_once(meta: MetaAgent, mem: Memory, cfg: Config, logger: logging.Logger) -> None:
    try:
        meta.loop_once()
        if cfg.run_self_improving_each_loop:
            run_self_improving_prompt_once(os.path.join(os.getcwd(), "self_improving_prompt.py"))
    except Exception as exc:
        mem.log("ERROR", f"Loop error: {exc}")
        logger.exception("Loop error")


def main() -> None:
    cfg = load_config()
    logger = setup_logging()
    logger.info("INCOME-AI orchestrator starting (interval=%s min, LLM=%s, approvals=%s, self_improve=%s)",
                cfg.interval_min, cfg.enable_llm, cfg.approvals_enforced, cfg.run_self_improving_each_loop)

    # Instantiate Memory and MetaAgent
    mem = Memory(db_path=cfg.db_path)
    meta = MetaAgent(mem)

    # Schedule
    schedule.every(cfg.interval_min).minutes.do(lambda: _run_loop_once(meta, mem, cfg, logger))

    stop = graceful_killer(logger)
    logger.info("Running. Press Ctrl+C to exit.")
    try:
        while not stop["flag"]:
            schedule.run_pending()
            time.sleep(1)
    finally:
        mem.log("INFO", "Orchestrator stopped. Goodbye.")
        logger.info("Stopped.")

if __name__ == "__main__":
    main()
