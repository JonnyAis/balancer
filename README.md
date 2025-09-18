# Balancer — Automatic E*TRADE Portfolio Rebalancer
> Keep your retirement accounts on-target—safely and automatically.

Balancer connects to your E*TRADE accounts, computes trades to hit your target asset-class allocation, and (optionally) executes those trades.

## Why I built this
I wanted a simple, ETF-based rebalancer for my retirement accounts that didn’t require moving assets to a robo-advisor. I found the robo-advisers were charging way too much for simple ETF balancing, and doing infrequently (i.e., quarterly). I also needed a project to team myself python :) 

## Features

- **Configurable**: lets you choose your preferred ETFs, allocations, rebalance frequency, etc.
- 🔒 **Safety-first**: `preview` → `confirm` → `auto`, plus a global kill switch.
- 🔁 **Loop mode**: run on a schedule for hands-off upkeep.
- 🧰 **Designed for non-taxable accounts** (401k/IRA). Taxable version (with tax-loss harvesting + multi-ticker diversity) is on the roadmap.

> **DISCLAIMER**: This is personal finance software. This is not investing advice. Use at your own risk. Start in `preview` mode and test in the **E*TRADE sandbox** before enabling live trading.  
> E*TRADE developer signup: https://developer.etrade.com/home

---

## Quick Start

### 1) E*TRADE developer setup
1. Create a developer account and app: **E*TRADE Developer** → “Register / Sign in” → create keys.  
2. Get your **consumer key/secret** and enable the **sandbox** to test first.  
   _Docs:_ https://developer.etrade.com/home

### 2) Install
```bash
git clone https://github.com/JonnyAis/balancer.git
cd balancer
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt


### 3) Credentials Setup
Preferred: set environment variables  
  ETRADE_CONSUMER_KEY=...  
  ETRADE_CONSUMER_SECRET=...  

Alternative: (local file):  
  1. Copy config_file_example.py to config_file.py  
  2. Add your real key/secret  
  3. Keep config_file.py untracked (already in .gitignore)  

Never commit real credentials! 

### 4) Configure Balancer 
Edit `balancer/config.yaml` with your preferences

### asset classes
Defines target percentage of total account value (including cash) and the single ETF (preferred_symbol) used to represent each class. The below is the default but modify to your preferences. 

```
classes:
  Large: { target_percent: 40, preferred_symbol: VOO }
  Large: { target_percent: 12, preferred_symbol: VOO }
  Intl:  { target_percent: 28, preferred_symbol: IXUS }
  Bonds: { target_percent: 20, preferred_symbol: BND }```

### rebalance
Heuristic thresholds and the integer optimizer settings.

| Field | Meaning |
|-------|---------|
| min_percent_drift | Minimum percent deviation before a class is considered (heuristic only). |
| min_notional_trade | Skip any single trade smaller than this dollar amount. |
| max_notional_trade | Cap a single trade notional (heuristic only). |
| account_min_turnover | Require this total turnover (sum buys + sells) per account or skip. |

#### integer_optimizer
Replaces heuristic drift logic when enabled (recommended).

| Field | Meaning |
|-------|---------|
| enabled | true = use enumeration-based whole-share optimizer. |
| window | Share search radius around continuous target shares (complexity ~(2w+1)^classes). |
| turnover_penalty | Adds penalty * total absolute share changes (stabilizes against trivial improvements). |
| cash_buffer | Leaves this much cash uninvested (per account). |

Objective minimized:
Sum_over_classes( (allocated_value - target_value)^2 ) + turnover_penalty * total_share_changes

### loop
Optional periodic execution.

| Field | Meaning |
|-------|---------|
| enabled | true runs continuously. Use only with `trading.mode: auto`. |
| interval_minutes | Sleep time between cycles. |

### accounts
Account include/exclude filters by description (case-insensitive).

| Field | Meaning |
|-------|---------|
| exclude_if_desc_contains | Skip if description contains any substring in list. |
| include_only_descriptions | If non-empty, only include matching descriptions (exact, case-insensitive). |

### trading
Execution behavior.

| Field | Meaning |
|-------|---------|
| mode | preview = plan only; confirm = prompt before orders; auto = execute immediately. |
| max_orders_per_account | Hard guardrail; skip account if exceeded. |
| price_type | Reserved (currently MARKET only). |
| allow_partial_funding_scale | If buys exceed available cash post-sells: true = scale down; false = drop all buys. |

### Funding Enforcement
- Buys are dropped (or scaled if allowed) if they would exceed cash + proceeds from planned sells.
- No unfunded orders are submitted.

### Execution Flow (auto / confirm)
1. Collect positions & quotes.
2. Allocate (integer optimizer if enabled).
3. Print plan (optionally with detailed allocation table if `REBALANCER_DETAILED_PLAN=1`).
4. (confirm mode) Prompt y/N.
5. Execute sells first; refresh cash; execute buys (scaling if enabled).

### Environment Toggles
| Variable | Effect |
|----------|--------|
| REBALANCER_DETAILED_PLAN=1 | Show per-class before/after table. |
| REBALANCER_DEBUG_TRADES=1  | Dump raw trades JSON (optional). |

### Safety Model
Single lever via `trading.mode`:
- preview: zero order traffic.
- confirm: human in the loop.
- auto: immediate execution (use cautiously; pair with loop if desired).

### Optimizer vs Heuristic
- Optimizer: global integer solution close to targets; stable with window ≤ 8 and few classes.
- Heuristic: drift-based independent per-class adjustments (used only if optimizer disabled).

### Typical Workflow
1. Start with `mode: preview` to validate.
2. Switch to `confirm` during initial rollout.
3. Move to `auto` for unattended or loop operation.

### Extending
Potential future enhancements:
- Tax-lot aware logic (irrelevant for non-taxable accounts now).
- Multi-symbol per class.
- Cross-account coordination.

## Emergency Stop
Set an environment variable `REBALANCER_KILL=1` before running to force an immediate safe exit.

## License
MIT License (see MIT License.txt).
```