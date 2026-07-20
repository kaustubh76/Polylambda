"""config/loader.py — model.yaml parsing, env precedence, and the lambda_star scale guard."""
import os

from config.loader import Config, _parse_simple_yaml, load_config


def _clear(keys):
    saved = {k: os.environ.pop(k, None) for k in keys}
    return saved


def _restore(saved):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


ENV_KEYS = ("MODE", "MAX_CAPITAL_USDC", "POSITIONING", "LAMBDA_STAR")


def test_parse_simple_yaml_flat_and_nested():
    y = _parse_simple_yaml(
        "gamma: 0.5   # comment\nlambda_v1: base_rate\ndata:\n  fill_limit: 5000\n  source: hf\nk: 5.0\n"
    )
    assert y["gamma"] == 0.5 and isinstance(y["gamma"], float)
    assert y["lambda_v1"] == "base_rate"
    assert y["data"]["fill_limit"] == 5000 and isinstance(y["data"]["fill_limit"], int)
    assert y["k"] == 5.0  # un-indented key after the block closes the section


def test_load_config_reads_model_yaml_and_fixed_lambda_star():
    saved = _clear(ENV_KEYS)
    try:
        cfg = load_config()
        assert cfg.quote.gamma == 0.5 and cfg.quote.k == 5.0 and cfg.quote.kappa == 1.0
        assert cfg.lambda_star == 0.002          # the scale-bug fix, straight from model.yaml
        assert cfg.mode == "paper" and cfg.positioning == "both"
        assert cfg.fill_limit == 5000 and cfg.control_ratio == 3
    finally:
        _restore(saved)


def test_env_overrides_win():
    saved = _clear(ENV_KEYS)
    try:
        os.environ["MODE"] = "paper-live"
        os.environ["LAMBDA_STAR"] = "0.01"
        os.environ["MAX_CAPITAL_USDC"] = "25"
        cfg = load_config()
        assert cfg.mode == "paper-live"
        assert cfg.lambda_star == 0.01
        assert cfg.max_capital_usdc == 25.0
    finally:
        _restore(saved)


def test_lambda_star_scale_guard_fires():
    saved = _clear(ENV_KEYS)
    try:
        os.environ["LAMBDA_STAR"] = "0.15"       # the old scale bug
        try:
            load_config()
            assert False, "expected ValueError on wrong-scale lambda_star"
        except ValueError as e:
            assert "wrong scale" in str(e)
    finally:
        _restore(saved)


def test_bad_mode_rejected():
    saved = _clear(ENV_KEYS)
    try:
        os.environ["MODE"] = "yolo"
        try:
            load_config()
            assert False, "expected ValueError on bad MODE"
        except ValueError as e:
            assert "MODE" in str(e)
    finally:
        _restore(saved)


def test_testnet_mode_accepted():
    saved = _clear(ENV_KEYS)
    try:
        os.environ["MODE"] = "testnet"
        assert load_config().mode == "testnet"
    finally:
        _restore(saved)


def test_risk_and_testnet_knobs_load_from_yaml():
    saved = _clear(ENV_KEYS)
    try:
        cfg = load_config()
        assert cfg.max_daily_loss_usd == 25.0
        assert cfg.portfolio_gross_cap == 200.0
        assert cfg.kill_switch_path == ".data_cache/risk/KILL"
        assert cfg.max_consecutive_errors == 5
        assert cfg.max_tx_per_day == 200
        assert cfg.max_gas_pol_per_day == 0.6
        assert cfg.min_requote_delta == 0.005
        assert cfg.max_quote_age_s == 900.0
        assert cfg.dispute_confirmations == 30
    finally:
        _restore(saved)


def test_defaults_when_yaml_missing():
    saved = _clear(ENV_KEYS)
    try:
        cfg = load_config(path="/nonexistent/model.yaml")
        assert isinstance(cfg, Config) and cfg.lambda_star == 0.002
    finally:
        _restore(saved)
