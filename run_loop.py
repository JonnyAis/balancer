import os, time, traceback, yaml
from balancer.main import run

def loop_settings():
    path = os.path.join(os.path.dirname(__file__), "balancer", "config.yaml")
    if not os.path.exists(path):
        return False, 30
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    loop_cfg = data.get("loop", {}) or {}
    return bool(loop_cfg.get("enabled", False)), int(loop_cfg.get("interval_minutes", 30))

def main():
    enabled, interval = loop_settings()
    if not enabled:
        print("[Loop] Disabled. Single run.")
        run()
        return
    print(f"[Loop] Enabled interval={interval}m")
    while True:
        start = time.time()
        run()
        elapsed = time.time() - start
        sleep_for = max(5, interval*60 - elapsed)
        print(f"[Loop] Sleep {int(sleep_for)}s")
        time.sleep(sleep_for)

if __name__ == "__main__":
    main()