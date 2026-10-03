#!/usr/bin/env python3
"""Launch the transactional QUQU v2 study with bulk public data adapters."""
from __future__ import annotations

import ququ_v2_ablation as core
import ququ_v2_bulk_data as bulk

# Replace network-per-ticker loaders before importing/running the safe driver.
core.download_prices = bulk.load_prices_hf
core.load_shares = bulk.load_shares_hf_plus_proxy
core.load_float_info = bulk.load_float_info_bulk

import ququ_v2_ablation_safe as safe


if __name__ == "__main__":
    safe.main()
