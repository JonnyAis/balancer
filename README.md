Automated portfolio rebalancer for E*TRADE accounts. It constructs a target allocation across asset classes and generates/executes BUY/SELL orders to match target allocation as closely as possible.

### Credentials Setup
Preferred: set environment variables  
  ETRADE_CONSUMER_KEY=...  
  ETRADE_CONSUMER_SECRET=...  

Fallback (local file):  
  1. Copy config_file_example.py to config_file.py  
  2. Add your real key/secret  
  3. Keep config_file.py untracked (already in .gitignore)  

Never commit real credentials.

## Quick Start
1. Copy credentials (see credentials_example) and authenticate once.
2. Edit `balancer/config.yaml` (targets + trading.mode).
3. Dry run (plan only):
   ```
   python -m balancer.main
   ```
4. Confirm mode:
   - Set `trading.mode: confirm` in config.
   - Run and answer `y` to execute.
5. Fully automatic:
   - Set `trading.mode: auto`
   - (Optional) enable loop for periodic execution.

## Configuration Reference (config.yaml)

### classes
Defines target percentage of total account value (including cash) and the single ETF (preferred_symbol) used to represent each class.

```
classes:
  Large: { target_percent: 40, preferred_symbol: VOO }
```

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