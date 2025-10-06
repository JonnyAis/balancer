<img width="857" height="180" alt="image" src="https://github.com/user-attachments/assets/9482367a-89c9-4149-9d92-7c0bfd2f684e" />

# Balancer — Automatic E*TRADE Portfolio Rebalancer
> Keep retirement accounts aligned with your target allocation—safely and automatically.

Balancer connects to your E*TRADE accounts, computes trades to move toward your target ETF allocation, and (optionally) executes them.

## Why I built this
I wanted a simple and free portfolio rebalancer for retirement accounts without paying roboadvisor fees or accepting quarterly drift. I also needed a project to teach myself Python 🐍. I've been using versions of this for the last few years but am finally getting around to publishing for anyone to use.

## Features
- 🔒 **Safety-first**: `preview` → `confirm` → `auto`, plus a global kill switch.
- 🧮 **Configurable**: set your preferences for ETFs, target allocations, rebalance frequency, etc.
- 🔁 **Loop mode**: run on a schedule for hands-off upkeep.
- 🧰 **Designed for non-taxable accounts** (401k/IRA). Taxable version (with tax-loss harvesting + multi-ticker diversity) is on the roadmap.

> DISCLAIMERS:
> Personal finance software, not investment advice.
> Use at your own risk.
> Always start in `preview` mode and test in the E*TRADE sandbox before live trading.

---

## Quick Start

### 1. E*TRADE Developer Setup
1. Create a developer account + app on the E*TRADE developer portal: https://developer.etrade.com/home
2. Obtain consumer key & secret: https://us.etrade.com/etx/ris/apikey
3. Enable sandbox first.

### 2. Install
```bash
git clone https://github.com/JonnyAis/balancer.git
cd balancer
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell
# .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Credentials

**Create a .env file** (copy from template):
```bash
cp .env.example .env
# Edit the newly created .env file with your E*TRADE developer key and secret.
```

### 4. Configure
Edit `balancer/config.yaml` (targets, optimizer, trading mode, filters).

See inline comments in balancer/config.yaml for every field. Key sections include:
- classes — target % and representative ETF per class
- rebalance — heuristic thresholds and integer optimizer parameters
- accounts — include/exclude filters by account description
- trading — behavior (mode, caps, etc.)
- loop — interval and toggles for run_loop.py


Example excerpt:
```yaml
classes:
  Large:         { target_percent: 40, preferred_symbol: VOO }
  Small:         { target_percent: 12, preferred_symbol: VBR }
  International: { target_percent: 28, preferred_symbol: IXUS }
  Bonds:         { target_percent: 20, preferred_symbol: BND }

trading:
  mode: preview   # preview | confirm | auto
```

### 5. Run (Plan Only)
```bash
python -m balancer.main
```

### 6. Execute with Confirmation
Set `trading.mode: confirm`, rerun, answer `y`.

### 7. Fully Automatic
Set `trading.mode: auto` (use cautiously, especially if enabling loop mode).

---

## Configuration Reference

The `balancer/config.yaml` file controls all behavior. Key sections:

| Section | Purpose |
|---------|---------|
| classes | Target % allocation + single ETF per asset class |
| rebalance | Heuristic thresholds + integer optimizer settings |
| loop | Optional periodic execution |
| accounts | Include/exclude by description |
| trading | Execution behavior & safety |

### classes
Each asset class needs a target percentage and preferred ETF symbol:
```yaml
classes:
  Large:         { target_percent: 40, preferred_symbol: VOO }
  International: { target_percent: 28, preferred_symbol: IXUS }
  Bonds:         { target_percent: 20, preferred_symbol: BND }
  Small:         { target_percent: 12, preferred_symbol: VBR }
```
**Target percentages must sum to 100.**

### rebalance (integer optimizer settings)
- `min_percent_drift`: Ignore small % drifts from target allocation
- `min_notional_trade`: Skip trades smaller than this dollar amount
- `max_notional_trade`: Cap individual trade size (splits large trades)
- `account_min_turnover`: Skip rebalancing if total turnover below threshold
- `integer_optimizer`: Controls optimizer window and turnover penalty

---

## Funding & Execution Flow
1. Fetch positions, quotes, and cash balances
2. Optimize using integer optimization (whole-share allocation)
3. Print the rebalancing plan (per-class allocation table is always shown)
4. If in confirm mode, prompt user for approval
5. Execute sells, refresh cash, then execute buys (scaling if enabled)
6. No unfunded buys are submitted

---

## Optimization Algorithm
The rebalancer uses **integer optimization** to find the best whole-share allocation that minimizes:
```
Σ (allocated_value_c - target_value_c)² + turnover_penalty × Σ |Δshares_c|
```

Configure in `config.yaml`:
```yaml
rebalance:
  integer_optimizer:
    window: 4                # Share search radius (complexity scales exponentially)  
    turnover_penalty: 0.0    # Penalty for changing positions (reduces churn)
```

## How to Run

1. Open a terminal in VS Code.
2. Activate your virtual environment:
   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```
3. Run the program:
   ```powershell
   python run.py prod preview
   ```
   *(Use `confirm` or `auto` for other modes.)*

**Optional (one-liner for PowerShell):**
```powershell
.\.venv\Scripts\Activate.ps1; python run.py prod confirm
```
