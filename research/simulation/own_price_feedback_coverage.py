"""Expected full-grid identities, derived from declared replication dimensions."""


def expected_coverage(seeds, cells, baskets, sessions, strategies, assets, background_per_asset):
    for value in (seeds, baskets, sessions, strategies, assets, background_per_asset):
        if type(value) is not int or value <= 0:
            raise ValueError("coverage dimensions must be positive integers")
    if (not isinstance(cells, list) or not cells or any(set(cell) != {"name", "background_anchor", "scale"} for cell in cells)
            or len({cell["name"] for cell in cells}) != len(cells)):
        raise ValueError("coverage needs distinct explicit control cells")
    scales = {cell["scale"] for cell in cells}
    anchors = {cell["background_anchor"] for cell in cells}
    if (scales != {0.0, 0.5, 1.0} or anchors != {"initial_inventory", "current_inventory"}
            or {(cell["background_anchor"], cell["scale"]) for cell in cells} != {(anchor, scale) for anchor in anchors for scale in scales}
            or len(cells) != len(anchors) * len(scales)):
        raise ValueError("coverage requires the complete anchor by scale grid")
    days = baskets * sessions
    baselines = len(anchors)
    receipt_positions = strategies * assets + background_per_asset * assets
    return {"paths": seeds * len(cells) * baskets,
            "full_path_ledger_records": seeds * len(cells) * days,
            "complete_original_daily_records_matched": baselines * days,
            "same_state_scaled_records": seeds * baselines * days * (len(scales) - 1),
            "same_state_strategy_allocations_audited": seeds * baselines * days * len(scales) * strategies,
            "same_state_original_decision_order_matches": seeds * baselines * days * strategies,
            "paired_private_receipt_values": seeds * (len(cells) - 1) * days * receipt_positions}
