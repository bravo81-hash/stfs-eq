import json


def test_order_payload_includes_raw_atr_for_trailing_stop():
    from battle_card import _order_json

    payload = json.loads(_order_json({
        "ticker": "ROKU",
        "regime": "LIQUIDITY",
        "score": 6,
        "trio_pass": True,
        "quality": 0.52,
        "thin_history": True,
        "rs_pct": 9.8,
        "rsi": 60.0,
        "adx": 24.0,
        "atr": 4.404,
        "atr_pct": 3.9,
        "momentum_bonus": 0,
        "earnings_date": None,
        "factors": {},
        "backtest": {},
        "u_plan": {
            "entry": 106.15,
            "stop": 95.14,
            "target": 123.77,
            "is_breakout": False,
            "account_sizing": [
                {"account": "Borg", "shares": 18, "notional": 1910.70},
            ],
        },
    }))

    assert payload["signal"]["atr"] == 4.404


def test_realized_r_for_debit_option_uses_debit_at_risk():
    from log_outcome import _realized_r

    entry = {
        "order": {
            "type": "options",
            "structure": "long_call",
            "limit_price": 8.10,
            "net_debit": 8.10,
            "max_loss_per_contract": 810.0,
        }
    }

    assert _realized_r(entry, exit_price=12.15, result="partial") == 0.5


def test_realized_r_for_credit_spread_uses_max_loss_not_credit():
    from log_outcome import _realized_r

    entry = {
        "order": {
            "type": "options",
            "structure": "credit_spread",
            "limit_price": 1.80,
            "net_credit": 1.80,
            "max_loss_per_contract": 320.0,
        }
    }

    # Closing for 0.90 captures 0.90 on 3.20 risk = +0.281R.
    assert _realized_r(entry, exit_price=0.90, result="partial") == 0.281


def test_option_outcome_auto_classification_uses_option_target_value():
    from log_outcome import _hit_result

    debit_entry = {
        "order": {
            "type": "options",
            "structure": "long_call",
            "net_debit": 8.10,
            "target_value": 20.25,
        }
    }
    assert _hit_result(20.00, debit_entry, forced=None) == "target"

    credit_entry = {
        "order": {
            "type": "options",
            "structure": "credit_spread",
            "net_credit": 1.80,
            "target_value": 0.90,
            "max_loss_per_contract": 320.0,
        }
    }
    assert _hit_result(0.88, credit_entry, forced=None) == "target"


def test_net_option_mark_aggregates_spread_and_diagonal_legs():
    import portfolio_manager as pm

    debit_order = {
        "structure": "debit_spread",
        "expiry": "2026-06-18",
        "long_strike": 100.0,
        "short_strike": 105.0,
    }
    debit_marks = {
        ("C", "20260618", 100.0): 6.20,
        ("C", "20260618", 105.0): 2.10,
    }
    assert pm._net_option_mark(debit_order, debit_marks) == 4.10

    credit_order = {
        "structure": "credit_spread",
        "expiry": "2026-06-18",
        "long_strike": 95.0,
        "short_strike": 100.0,
    }
    credit_marks = {
        ("P", "20260618", 95.0): 1.00,
        ("P", "20260618", 100.0): 2.40,
    }
    assert pm._net_option_mark(credit_order, credit_marks) == 1.40

    diagonal_order = {
        "structure": "diagonal",
        "expiry": "2026-06-18",
        "expiry_front": "2026-05-15",
        "long_strike": 125.0,
        "short_strike": 125.0,
    }
    diagonal_marks = {
        ("C", "20260618", 125.0): 8.00,
        ("C", "20260515", 125.0): 2.50,
    }
    assert pm._net_option_mark(diagonal_order, diagonal_marks) == 5.50
