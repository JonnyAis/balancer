#!/usr/bin/env python3
"""
Cross-platform launcher for the balancer.
Usage: 
  python run.py          # Use current config
  python run.py preview  # Force preview mode
  python run.py confirm  # Force confirm mode
  python run.py auto     # Force auto mode
"""
import os
import sys
import subprocess
import yaml
from pathlib import Path

def find_python():
    """Find the best Python executable to use."""
    script_dir = Path(__file__).parent.absolute()
    
    # Try venv python first (most reliable)
    if sys.platform == "win32":
        venv_python = script_dir / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = script_dir / ".venv" / "bin" / "python"
    
    if venv_python.exists():
        return str(venv_python)
    
    # Fallback to py launcher on Windows
    if sys.platform == "win32":
        return "py"
    else:
        return "python"

def set_mode(mode):
    """Update config.yaml with the specified trading mode."""
    config_path = Path("balancer/config.yaml")
    if not config_path.exists():
        print(f"❌ Config file not found: {config_path}")
        return False
    
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        config['trading']['mode'] = mode
        
        with open(config_path, 'w') as f:
            yaml.dump(config, f)
        
        print(f"⚙️  Set trading mode to: {mode}")
        return True
    except Exception as e:
        print(f"❌ Error updating config: {e}")
        return False

def main():
    # Handle mode and environment arguments
    mode = None
    env_override = None
    
    for arg in sys.argv[1:]:
        arg_lower = arg.lower()
        if arg_lower in ['preview', 'confirm', 'auto']:
            mode = arg_lower
        elif arg_lower in ['sandbox', 'prod', 'production']:
            env_override = 'sandbox' if arg_lower == 'sandbox' else 'prod'
        else:
            print(f"❌ Invalid argument: {arg}")
            print("Valid arguments: preview|confirm|auto sandbox|prod")
            input("Press Enter to exit...")
            return 1
    
    # Set environment override if specified
    if env_override:
        sandbox_flag = "true" if env_override == "sandbox" else "false"
        os.environ["ETRADE_SANDBOX"] = sandbox_flag
        print(f"🔄 Environment override: {'SANDBOX' if env_override == 'sandbox' else 'PRODUCTION'}")
    
    # Set trading mode if specified
    if mode:
        if not set_mode(mode):
            input("Press Enter to exit...")
            return 1
    
    # Get the directory where this script is located
    script_dir = Path(__file__).parent.absolute()
    os.chdir(script_dir)
    
    # Find the best Python to use
    python_exe = find_python()
    
    print("🚀 Starting Portfolio Balancer...")
    print(f"📁 Working directory: {script_dir}")
    print(f"🐍 Using: {python_exe}")
    
    try:
        result = subprocess.run([
            python_exe, "-m", "balancer.main"
        ], cwd=script_dir)
        
        if result.returncode != 0:
            print(f"\n❌ Program exited with code {result.returncode}")
        else:
            print(f"\n✅ Program completed successfully")
            
    except KeyboardInterrupt:
        print(f"\n⏹️  Interrupted by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
    
    input("Press Enter to exit...")
    return result.returncode if 'result' in locals() else 1

if __name__ == "__main__":
    sys.exit(main())

def run():
    # Remove the REBALANCER_KILL check since we're removing mysterious env vars
    
    cfg = load_config()
    loop_cfg = cfg.get("loop", {}) or {}
    loop_enabled = bool(loop_cfg.get("enabled", False))
    interval_min = int(loop_cfg.get("interval_minutes", 30))

    # Always run at least once
    _one_cycle(cfg)

    if not loop_enabled:
        return

    # Need mode=auto for repeating meaningful cycles
    trading_mode = str(cfg["trading"].get("mode","preview")).lower()
    if trading_mode != "auto":
        print(f"[Loop] Enabled but trading.mode={trading_mode} != auto -> stopping after first cycle.")
        return

    print(f"[Loop] Continuous mode every {interval_min} minute(s). Ctrl+C to stop.")
    while True:
        try:
            # Remove the REBALANCER_RELOAD_CONFIG check since we're removing mysterious env vars
            
            next_run = time.time() + interval_min * 60
            while True:
                remaining = int(next_run - time.time())
                if remaining <= 0:
                    break
                if remaining % 30 == 0 or remaining <= 10:
                    print(f"[Loop] Next cycle in {remaining}s", end="\r")
                time.sleep(1)

            print("\n[Loop] ===== New Cycle =====")
            # Remove the REBALANCER_KILL check here too
            _one_cycle(cfg)

        except KeyboardInterrupt:
            print("\n[Loop] Interrupted by user.")
            break
        except Exception as e:
            print(f"[Loop][Error] {e}")
            traceback.print_exc()
            continue