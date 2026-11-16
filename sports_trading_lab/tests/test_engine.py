"""Test del motore di backtesting (calcoli verificati a mano) e dell'integrita' del database."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import backtester as bt  # noqa: E402
from src import config as C  # noqa: E402
from src import update_data as ud  # noqa: E402


def make_sel(odds, won, close=None, p_close=None, dates=None):
    n = len(odds)
    return pd.DataFrame({
        "match_id": [f"m{i:03d}" for i in range(n)],
        "date": pd.to_datetime(dates or [f"2024-01-{i + 1:02d}" for i in range(n)]),
        "selection": "home", "odds": odds, "won": won,
        "close_odds": close if close is not None else [np.nan] * n,
        "p_close": p_close if p_close is not None else [np.nan] * n,
    })


# ------------------------------------------------------------------ settlement
def test_back_pnl_roi_winrate():
    t = bt.settle(make_sel([2.0, 3.0, 1.5, 4.0], [True, False, True, False]))
    assert t["pnl"].tolist() == pytest.approx([1.0, -1.0, 0.5, -1.0])
    m = bt.compute_metrics(t)
    assert m["trades"] == 4 and m["wins"] == 2
    assert m["win_rate_pct"] == pytest.approx(50.0)
    assert m["profit"] == pytest.approx(-0.5)
    assert m["staked"] == pytest.approx(4.0)
    assert m["roi_pct"] == pytest.approx(-12.5)
    assert m["avg_odds"] == pytest.approx(2.625)
    assert t["equity"].tolist() == pytest.approx([1.0, 0.0, 0.5, -0.5])


def test_back_commission_only_on_winnings():
    t = bt.settle(make_sel([2.0, 3.0], [True, False]), commission=0.05)
    assert t["pnl"].tolist() == pytest.approx([0.95, -1.0])


def test_lay_fixed_liability():
    # responsabilita' 1u: puntata del backer = 1/(quota-1)
    t = bt.settle(make_sel([3.0, 3.0, 1.5], [False, True, False]), side="lay", commission=0.05)
    assert t["risk"].tolist() == pytest.approx([1.0, 1.0, 1.0])
    assert t["pnl"].tolist() == pytest.approx([0.5 * 0.95, -1.0, 2.0 * 0.95])
    assert bt.compute_metrics(t)["roi_pct"] == pytest.approx((0.475 - 1 + 1.9) / 3 * 100)


def test_lay_fixed_backer_stake():
    t = bt.settle(make_sel([3.0, 3.0], [False, True]), side="lay", commission=0.02, lay_mode="stake")
    assert t["risk"].tolist() == pytest.approx([2.0, 2.0])
    assert t["pnl"].tolist() == pytest.approx([0.98, -2.0])
    assert bt.compute_metrics(t)["roi_pct"] == pytest.approx(-1.02 / 4 * 100)


def test_stake_scales_linearly():
    a = bt.settle(make_sel([2.2, 1.8, 5.0], [True, False, True]))
    b = bt.settle(make_sel([2.2, 1.8, 5.0], [True, False, True]), stake=2.5)
    assert (b["pnl"] / a["pnl"]).tolist() == pytest.approx([2.5] * 3)


def test_trades_sorted_chronologically():
    sel = make_sel([2.0, 2.0, 2.0], [True, False, True], dates=["2024-03-01", "2024-01-01", "2024-02-01"])
    t = bt.settle(sel)
    assert t["date"].is_monotonic_increasing
    assert t["pnl"].tolist() == [-1.0, 1.0, 1.0]


# ------------------------------------------------------------------ CLV
def test_clv_back_and_lay():
    sel = make_sel([2.10, 1.90], [True, False], close=[2.00, 2.00])
    back = bt.settle(sel)
    assert back["clv_pct"].tolist() == pytest.approx([5.0, -5.0])
    assert bt.compute_metrics(back)["clv_pct"] == pytest.approx(0.0)
    lay = bt.settle(sel, side="lay")
    assert lay["clv_pct"].tolist() == pytest.approx([-5.0, 5.0])


def test_clv_ignores_missing_close():
    t = bt.settle(make_sel([2.2, 2.0], [True, True], close=[2.0, np.nan]))
    m = bt.compute_metrics(t)
    assert m["clv_pct"] == pytest.approx(10.0)
    assert m["clv_coverage_pct"] == pytest.approx(50.0)


def test_ev_vs_no_vig_close():
    # p = 0.5 senza margine: back a 2.10 -> EV +5%; lay a 1.90 (resp. 1u) -> (0.5/0.9 - 0.5)/1 = +5.56%
    back = bt.settle(make_sel([2.10], [True], close=[1.95], p_close=[0.5]))
    assert back["ev_close_pct"][0] == pytest.approx(5.0)
    lay = bt.settle(make_sel([1.90], [True], close=[1.95], p_close=[0.5]), side="lay")
    assert lay["ev_close_pct"][0] == pytest.approx((0.5 / 0.9 - 0.5) * 100)


# ------------------------------------------------------------------ drawdown / streak
def test_max_drawdown_units_and_pct():
    pnl = np.array([1, 1, -1, -1, -1, 2, -1], dtype=float)  # equity: 1 2 1 0 -1 1 0
    units, pct = bt.max_drawdown(pnl, bankroll=10)
    assert units == pytest.approx(3.0)
    assert pct == pytest.approx(3 / 12 * 100)


def test_drawdown_from_initial_bankroll():
    units, pct = bt.max_drawdown(np.array([-1.0, -1.0, 1.0]), bankroll=20)
    assert units == pytest.approx(2.0) and pct == pytest.approx(10.0)


def test_drawdown_pct_capped_when_bankroll_is_wiped_out():
    units, pct = bt.max_drawdown(np.array([-3.0, -3.0]), bankroll=5)
    assert units == pytest.approx(6.0) and pct == pytest.approx(100.0)


def test_no_drawdown_when_only_winning():
    assert bt.max_drawdown(np.array([1.0, 0.5, 2.0])) == (0.0, 0.0)
    assert bt.max_drawdown(np.array([])) == (0.0, 0.0)


def test_longest_losing_streak():
    assert bt.longest_losing_streak(np.array([1, -1, -1, 1, -1, -1, -1, 1])) == 3
    assert bt.longest_losing_streak(np.array([-1, -1])) == 2
    assert bt.longest_losing_streak(np.array([1, 1])) == 0
    assert bt.longest_losing_streak(np.array([])) == 0


def test_metrics_match_bruteforce_on_random_data():
    rng = np.random.default_rng(7)
    n = 500
    odds = np.round(rng.uniform(1.2, 6.0, n), 2)
    won = rng.random(n) < 1 / odds
    dates = pd.date_range("2022-01-01", periods=n, freq="D").strftime("%Y-%m-%d").tolist()
    t = bt.settle(make_sel(odds.tolist(), won.tolist(), dates=dates), commission=0.03)
    m = bt.compute_metrics(t, bankroll=50)
    eq, peak, dd_u, dd_p, streak, best, profit = 50.0, 50.0, 0.0, 0.0, 0, 0, 0.0
    for o, w in zip(odds, won):
        p = (o - 1) * 0.97 if w else -1.0
        profit += p
        eq += p
        peak = max(peak, eq)
        dd_u, dd_p = max(dd_u, peak - eq), min(max(dd_p, (peak - eq) / peak * 100), 100.0)
        streak = 0 if w else streak + 1
        best = max(best, streak)
    assert m["profit"] == pytest.approx(profit)
    assert m["roi_pct"] == pytest.approx(profit / n * 100)
    assert m["max_dd_units"] == pytest.approx(dd_u)
    assert m["max_dd_pct"] == pytest.approx(dd_p)
    assert m["longest_losing_streak"] == best
    assert t["equity"].iloc[-1] == pytest.approx(profit)


def test_monthly_returns():
    sel = make_sel([2.0, 2.0, 3.0, 2.0], [True, False, True, False],
                   dates=["2024-01-05", "2024-01-20", "2024-02-01", "2024-02-10"])
    mo = bt.monthly_returns(bt.settle(sel))
    assert mo["month"].tolist() == ["2024-01", "2024-02"]
    assert mo["trades"].tolist() == [2, 2]
    assert mo["profit"].tolist() == pytest.approx([0.0, 1.0])
    assert mo["yield_pct"].tolist() == pytest.approx([0.0, 50.0])
    assert mo["profit"].sum() == pytest.approx(bt.compute_metrics(bt.settle(sel))["profit"])


def test_empty_backtest_is_safe():
    t = bt.settle(make_sel([], []))
    m = bt.compute_metrics(t)
    assert m["trades"] == 0 and m["profit"] == 0.0
    assert bt.monthly_returns(t).empty


# ------------------------------------------------------------------ selezioni e filtri
def tennis_frame():
    return pd.DataFrame({
        "match_id": ["a", "b", "c"], "match_date": pd.to_datetime(["2024-05-01", "2024-05-02", "2024-05-03"]),
        "season": 2024, "circuit": "ATP", "tourney_name": ["Roma", "Roma", "Halle"], "tourney_level": "M",
        "series": "Masters 1000", "surface": ["Clay", "Clay", "Grass"], "round": "R32", "best_of": 3.0,
        "status": ["Completed", "Walkover", "Retired"],
        "winner_name": ["A", "C", "E"], "loser_name": ["B", "D", "F"],
        "winner_rank": [5.0, 30.0, 80.0], "loser_rank": [40.0, 10.0, 20.0],
        "winner_form10": [0.7, 0.5, 0.4], "loser_form10": [0.4, 0.6, 0.8],
        "winner_age": 25.0, "loser_age": 27.0,
        "avg_w": [1.40, 2.50, 3.00], "avg_l": [2.90, 1.50, 1.36],
        "ps_w": [1.45, 2.60, 3.20], "ps_l": [2.95, 1.55, 1.40],
        "max_w": [1.50, 2.70, 3.40], "max_l": [3.10, 1.60, 1.42],
    })


def test_tennis_selections_two_rows_per_match_and_no_walkover():
    s = bt.tennis_selections(tennis_frame(), "avg")
    assert len(s) == 4 and "b" not in set(s["match_id"])
    a_w = s[(s["match_id"] == "a") & s["won"]].iloc[0]
    assert a_w["selection"] == "favourite" and a_w["odds"] == 1.40 and a_w["rank_adv"] == 35.0
    a_l = s[(s["match_id"] == "a") & ~s["won"]].iloc[0]
    assert a_l["selection"] == "underdog" and a_l["odds"] == 2.90 and a_l["close_odds"] == 2.95
    assert a_w["p_close"] + a_l["p_close"] == pytest.approx(1.0)
    assert a_w["p_close"] == pytest.approx((1 / 1.45) / (1 / 1.45 + 1 / 2.95))


def test_tennis_filters_and_backtest():
    s = bt.tennis_selections(tennis_frame(), "max")
    cfg = {"selection": ["favourite"], "filters": {"surface": {"in": ["Clay"]}, "rank": {"max": 10}}}
    r = bt.backtest_selections(s, cfg)
    assert r.metrics["trades"] == 1 and r.metrics["profit"] == pytest.approx(0.5)
    assert r.metrics["clv_pct"] == pytest.approx((1.50 / 1.45 - 1) * 100)
    # il favorito di 'c' ha perso per ritiro: incluso di default, escluso su richiesta
    assert bt.backtest_selections(s, {"selection": ["favourite"]}).metrics["trades"] == 2
    assert bt.backtest_selections(s, {"selection": ["favourite"], "exclude_retirements": True}).metrics["trades"] == 1


def football_frame():
    base = {
        "match_id": ["x", "y"], "match_date": pd.to_datetime(["2024-08-17", "2024-08-18"]), "season": "2024/25",
        "country": "Italia", "league": "Serie A", "home_team": ["Inter", "Lecce"], "away_team": ["Genoa", "Milan"],
        "ftr": ["H", "D"], "total_goals": [3.0, 2.0],
        "home_ppg5": [2.4, 0.8], "away_ppg5": [1.0, 2.0], "home_gf5": 1.5, "home_ga5": 1.0, "away_gf5": 1.2, "away_ga5": 1.1,
    }
    odds = {"h": [1.40, 4.00], "d": [4.80, 3.60], "a": [8.00, 1.95], "o25": [1.60, 1.90], "u25": [2.40, 1.95]}
    for book in C.FOOT_BOOKS:
        for k, v in odds.items():
            base[f"{book}_{k}"] = v
            base[f"{book}_c{k}"] = [x * 0.95 for x in v] if book == "ps" else [np.nan, np.nan]
    return pd.DataFrame(base)


def test_football_1x2_selections():
    s = bt.football_selections(football_frame(), "1x2", "avg")
    assert len(s) == 6
    assert s.groupby("match_id")["won"].sum().tolist() == [1, 1]
    assert s.groupby("match_id")["p_close"].sum().tolist() == pytest.approx([1.0, 1.0])
    x_home = s[(s["match_id"] == "x") & (s["selection"] == "home")].iloc[0]
    assert x_home["role"] == "favourite" and x_home["won"] and x_home["close_odds"] == pytest.approx(1.33)
    assert x_home["close_source"] == "ps"
    fav = bt.backtest_selections(s, {"selection": ["favourite"]})
    assert fav.trades["event"].tolist() == ["Inter - Genoa", "Lecce - Milan"]
    assert fav.metrics["profit"] == pytest.approx(0.40 - 1.0)
    draws = bt.backtest_selections(s, {"selection": ["draw"], "filters": {"ppg_diff": {"max": 0}}})
    assert draws.metrics["trades"] == 1 and draws.metrics["profit"] == pytest.approx(2.6)


def test_football_over_under_and_closing_price():
    s = bt.football_selections(football_frame(), "ou25", "avg")
    over = bt.backtest_selections(s, {"selection": ["over"]})
    assert over.metrics["trades"] == 2 and over.metrics["profit"] == pytest.approx(0.6 - 1.0)
    under = bt.backtest_selections(s, {"selection": ["under"], "side": "lay", "commission": 0.05})
    # lay under resp. 1u: x -> under perde: +1/(2.40-1)*0.95 ; y -> under vince: -1
    assert under.metrics["profit"] == pytest.approx(0.95 / 1.4 - 1.0)
    close = bt.football_selections(football_frame(), "1x2", "ps", timing="close")
    assert bt.backtest_selections(close, {"selection": ["home"]}).metrics["clv_pct"] == pytest.approx(0.0)


def test_odds_range_and_date_filters():
    s = bt.football_selections(football_frame(), "1x2", "avg")
    assert len(bt.apply_filters(s, {"odds_min": 1.9, "odds_max": 4.0})) == 3
    assert len(bt.apply_filters(s, {"date_from": "2024-08-18"})) == 3
    assert len(bt.apply_filters(s, {"date_to": "2024-08-17", "selection": "away"})) == 1
    assert len(bt.apply_filters(s, {"filters": {"league": {"in": ["Premier League"]}}})) == 0


# ------------------------------------------------------------------ pipeline dati
def test_football_parser_handles_bom_short_rows_and_bad_dates():
    raw = ("﻿Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,AvgH,AvgD,AvgA,PSCH,P>2.5,\n"
           "I1,24/08/2019,Parma,Juventus,0,1,A,10.4,5.1,1.31,9.6,1.78,\n"
           "I1,25/08/19,Roma,Genoa,3,3,D,1.5,4.2,6.0,,0\n"
           "I1,xx/08/2019,Bad,Date,1,0,H,2,3,4,2,2\n"
           "I1,26/08/2019,Wrong,Result,2,0,A,2,3,4,2,2\n"
           ",,,,,,,,,,,\n"
           "I1,27/08/2019,Short,Row,1,0,H\n").encode("utf-8")
    df, dropped = ud.parse_football(raw, "I1", "1920")
    assert df["home_team"].tolist() == ["Parma", "Roma", "Short"]
    assert dropped == 2
    assert df["match_date"].dt.strftime("%Y-%m-%d").tolist() == ["2019-08-24", "2019-08-25", "2019-08-27"]
    assert df.loc[0, "ps_ch"] == 9.6 and df.loc[0, "ps_o25"] == 1.78
    assert np.isnan(df.loc[1, "ps_o25"])  # quota 0 -> NULL
    assert df.loc[0, "season"] == "2019/20" and df["match_id"].is_unique


def test_outlier_odds_are_nulled_but_normal_dispersion_is_kept():
    d = pd.DataFrame({
        "ps_h": [2.50, 25.37, 1.19], "b365_h": [2.45, 17.0, 1.16], "max_h": [2.55, 25.37, 1.20],
        "avg_h": [2.48, 14.6, 17.0], "bfe_h": [1.01, np.nan, np.nan],
    })
    n = ud.drop_outlier_odds(d, ["ps_h", "b365_h", "max_h", "avg_h", "bfe_h"])
    assert n == 2
    assert np.isnan(d.loc[0, "bfe_h"]) and np.isnan(d.loc[2, "avg_h"])   # 1.01 non liquido, refuso 17.0
    assert d.loc[1].notna().sum() == 4                                    # sfavorito: dispersione reale, resta
    assert d.loc[0, "ps_h"] == 2.50


def test_football_parser_latin1():
    raw = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\nD1,01/09/2019,K\xf6ln,M'gladbach,0,1,A\n".encode("latin-1")
    df, _ = ud.parse_football(raw, "D1", "1920")
    assert df.loc[0, "home_team"] == "K\xf6ln"


def test_football_form_has_no_lookahead():
    n = 7
    df = pd.DataFrame({
        "match_id": [f"g{i}" for i in range(n)], "home_team": "A", "away_team": "B",
        "match_date": pd.date_range("2024-01-01", periods=n, freq="7D"),
        "ftr": ["H", "H", "D", "A", "H", "A", "H"], "fthg": [2, 1, 0, 0, 3, 0, 1], "ftag": [0, 0, 0, 1, 1, 2, 0],
    })
    f = ud.football_features(df)
    assert f["home_ppg5"].iloc[:5].isna().all()
    assert f.loc[5, "home_ppg5"] == pytest.approx((3 + 3 + 1 + 0 + 3) / 5)
    assert f.loc[6, "home_ppg5"] == pytest.approx((3 + 1 + 0 + 3 + 0) / 5)
    assert f.loc[5, "away_gf5"] == pytest.approx((0 + 0 + 0 + 1 + 1) / 5)


def test_tennis_name_keys_and_odds_matching():
    assert ud.td_surname_key("Auger-Aliassime F.") == "augeraliassime"
    assert ud.td_surname_key("Del Potro J.M.") == "delpotro"
    assert "delpotro" in ud.full_name_keys("Juan Martin del Potro")
    assert "oconnell" in ud.full_name_keys("Christopher O'Connell")
    sack = pd.DataFrame({
        "tourney_date": pd.to_datetime(["2024-02-12", "2024-02-19"]),
        "winner_name": ["John Isner", "John Isner"], "loser_name": ["Adrian Mannarino", "Adrian Mannarino"],
    })
    odds = pd.DataFrame({
        "wk": ["isner", "isner", "sinner"], "lk": ["mannarino", "mannarino", "alcaraz"],
        "match_date": pd.to_datetime(["2024-02-21", "2024-02-16", "2024-02-16"]),
    })
    # stessa coppia in due tornei consecutivi: ogni quota va al torneo giusto
    assert ud.match_odds(sack, odds).tolist() == [1.0, 0.0]


def test_tennis_form_has_no_lookahead():
    n = 7
    df = pd.DataFrame({
        "match_id": [f"t{i}" for i in range(n)], "match_date": pd.date_range("2024-01-01", periods=n, freq="D"),
        "round_order": 4.0,
        "winner_name": ["P", "P", "Q", "P", "P", "Q", "P"], "loser_name": ["Q", "Q", "P", "Q", "Q", "P", "Q"],
    })
    f = ud.tennis_features(df)
    assert f["winner_form10"].iloc[:5].isna().all()
    assert f.loc[5, "loser_form10"] == pytest.approx(4 / 5)   # P prima del match 5: V V S V V
    assert f.loc[6, "winner_form10"] == pytest.approx(4 / 6)  # P prima del match 6: V V S V V S


def test_tennis_h2h_rest_and_serve_features():
    df = pd.DataFrame({
        "match_id": ["a", "b", "c"], "match_date": pd.to_datetime(["2024-01-01", "2024-01-05", "2024-01-20"]),
        "round_order": 4.0, "surface": "Hard",
        "winner_name": ["P", "Q", "P"], "loser_name": ["Q", "P", "Q"],
        "w_svpt": [50.0, 60.0, 50.0], "w_1stWon": [30.0, 30.0, 30.0], "w_2ndWon": [10.0, 12.0, 10.0],
        "w_ace": [5.0, 3.0, 5.0], "w_df": [2.0, 1.0, 2.0],
        "l_svpt": [40.0, 50.0, 40.0], "l_1stWon": [20.0, 25.0, 20.0], "l_2ndWon": [4.0, 5.0, 4.0],
        "l_ace": [1.0, 2.0, 1.0], "l_df": [3.0, 4.0, 3.0],
    })
    f = ud.tennis_features(df).set_index("match_id")
    assert f.loc["a", "winner_h2h_wins"] == 0 and f.loc["a", "loser_h2h_wins"] == 0
    assert f.loc["b", "winner_h2h_wins"] == 0 and f.loc["b", "loser_h2h_wins"] == 1   # Q 0 vittorie, P 1
    assert f.loc["c", "winner_h2h_wins"] == 1 and f.loc["c", "loser_h2h_wins"] == 1
    assert np.isnan(f.loc["a", "winner_rest_days"])
    assert f.loc["b", "winner_rest_days"] == 4 and f.loc["c", "winner_rest_days"] == 15
    assert f.loc["a", "winner_matches14"] == 0 and f.loc["b", "loser_matches14"] == 1 and f.loc["c", "winner_matches14"] == 0
    assert f["winner_srv_won10"].isna().all()   # servono almeno 5 match precedenti


def test_tennis_serve_stats_use_only_previous_matches():
    n = 7
    df = pd.DataFrame({
        "match_id": [f"s{i}" for i in range(n)], "match_date": pd.date_range("2024-01-01", periods=n, freq="3D"),
        "round_order": 4.0, "surface": "Clay", "winner_name": "P", "loser_name": "Q",
        "w_svpt": 50.0, "w_1stWon": 30.0, "w_2ndWon": [10.0] * 5 + [0.0, 0.0], "w_ace": 5.0, "w_df": 2.0,
        "l_svpt": 40.0, "l_1stWon": 20.0, "l_2ndWon": 4.0, "l_ace": 1.0, "l_df": 3.0,
    })
    f = ud.tennis_features(df)
    assert f["winner_srv_won10"].iloc[:5].isna().all()
    assert f.loc[5, "winner_srv_won10"] == pytest.approx(40 / 50)             # i primi 5 match: (30+10)/50
    assert f.loc[6, "winner_srv_won10"] == pytest.approx((5 * 40 + 30) / 300)  # il sesto pesa, il settimo no
    assert f.loc[5, "winner_ret_won10"] == pytest.approx(1 - 24 / 40)
    assert f.loc[5, "winner_ace10"] == pytest.approx(0.1) and f.loc[5, "loser_df10"] == pytest.approx(3 / 40)
    assert f.loc[5, "winner_form_surf"] == pytest.approx(1.0)


def test_football_table_position_and_venue_form():
    rows, d = [], pd.Timestamp("2024-08-01")
    teams = ["A", "B", "C", "D"]
    for rnd in range(4):          # A vince sempre in casa contro B, C pareggia sempre con D
        rows.append(("A", "B", "H", 2, 0, d + pd.Timedelta(days=7 * rnd)))
        rows.append(("C", "D", "D", 1, 1, d + pd.Timedelta(days=7 * rnd)))
    df = pd.DataFrame(rows, columns=["home_team", "away_team", "ftr", "fthg", "ftag", "match_date"])
    df["match_id"] = [f"m{i}" for i in range(len(df))]
    df["div"], df["season_start"] = "X1", 2024
    f = ud.football_features(df).set_index(df["match_id"])
    last_ab, last_cd = f.loc["m6"], f.loc["m7"]
    assert np.isnan(f.loc["m4", "home_pos"])          # meno di 3 giornate giocate
    assert last_ab["home_pos"] == 1 and last_ab["away_pos"] == 4
    assert {last_cd["home_pos"], last_cd["away_pos"]} == {2, 3}
    assert last_ab["home_sppg"] == pytest.approx(3.0) and last_cd["away_sppg"] == pytest.approx(1.0)
    assert last_ab["home_rest_days"] == 7
    assert f["home_venue_ppg5"].isna().all()           # servono 5 partite in casa precedenti


def test_breakdown_segments_and_confidence():
    rng = np.random.default_rng(3)
    n = 800
    odds = np.round(rng.uniform(1.5, 3.0, n), 2)
    surface = np.where(np.arange(n) % 2 == 0, "Clay", "Hard")
    won = rng.random(n) < np.where(surface == "Clay", 1.15, 0.85) / odds   # vantaggio solo sulla terra
    sel = make_sel(odds.tolist(), won.tolist(), close=(odds * 0.98).tolist(),
                   dates=pd.date_range("2022-01-01", periods=n, freq="D").strftime("%Y-%m-%d").tolist())
    sel["surface"], sel["rank"] = surface, rng.integers(1, 200, n).astype(float)
    t = bt.settle(sel)
    by = bt.breakdown(t, "surface").set_index("surface")
    assert by["trades"].sum() == n and by["profit"].sum() == pytest.approx(t["pnl"].sum())
    clay = t[t["surface"] == "Clay"]
    assert by.loc["Clay", "roi_pct"] == pytest.approx(clay["pnl"].sum() / len(clay) * 100)
    assert bt.breakdown(t, "odds_bucket")["trades"].sum() == n
    scan = bt.segment_scan(t, "tennis", min_trades=100)
    assert scan.iloc[0]["segmento"] == "Clay" and scan.iloc[0]["rule"] == {"in": ["Clay"]}
    rank_rules = scan[scan["col"] == "rank"]["rule"].tolist()
    assert len(rank_rules) == 4 and sum(r["min"] is None for r in rank_rules) == 1
    # applicare la regola di un segmento riproduce esattamente le sue operazioni
    seg = scan[scan["col"] == "rank"].iloc[0]
    again = bt.backtest_selections(sel, {"filters": {"rank": seg["rule"]}})
    assert again.metrics["trades"] == seg["trades"] and again.metrics["roi_pct"] == pytest.approx(seg["roi_pct"])
    conf = bt.roi_confidence(t)
    roi = t["pnl"].sum() / len(t) * 100
    assert conf["lo"] < roi < conf["hi"] and 0 <= conf["p_positive"] <= 100
    cut, a, b = bt.split_metrics(t, 0.7)
    assert a["trades"] == 560 and b["trades"] == 240 and a["profit"] + b["profit"] == pytest.approx(t["pnl"].sum())
    assert cut == t["date"].iloc[560]


def test_old_database_schema_is_upgraded(tmp_path):
    import duckdb
    con = duckdb.connect(str(tmp_path / "old.duckdb"))
    con.execute("CREATE TABLE football_matches (match_id VARCHAR, home_ppg5 DOUBLE)")
    con.execute("CREATE TABLE tennis_matches (match_id VARCHAR)")
    assert ud.needs_feature_refresh(con)
    ud.init_db(con)
    cols = {r[0] for r in con.execute("DESCRIBE football_matches").fetchall()}
    assert set(C.FOOT_COLUMNS) <= cols
    assert set(C.TENNIS_COLUMNS) <= {r[0] for r in con.execute("DESCRIBE tennis_matches").fetchall()}
    ud.refresh_features(con)
    assert not ud.needs_feature_refresh(con)
    con.close()


def test_incremental_update_never_duplicates(tmp_path):
    import duckdb
    con = duckdb.connect(str(tmp_path / "t.duckdb"))
    ud.init_db(con)
    raw = b"Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\nI1,24/08/2019,Parma,Juventus,0,1,A\nI1,25/08/2019,Roma,Genoa,3,3,D\n"
    df, _ = ud.parse_football(raw, "I1", "1920")
    for _ in range(3):
        ud.replace_rows(con, "football_matches", df, "div = ? AND season_start = ?", ["I1", 2019])
    assert con.execute("SELECT count(*) FROM football_matches").fetchone()[0] == 2
    with pytest.raises(duckdb.Error):
        con.execute("INSERT INTO football_matches (match_id) VALUES (?)", [df.loc[0, "match_id"]])
    con.close()


# ------------------------------------------------------------------ database reale
needs_db = pytest.mark.skipif(not C.DB_PATH.exists(), reason="database non ancora creato: eseguire src/update_data.py")


@pytest.fixture(scope="module")
def con():
    c = bt.connect()
    yield c
    c.close()


@needs_db
def test_database_is_populated_and_clean(con):
    assert ud.validate(con) == []
    n_foot = con.execute("SELECT count(*) FROM football_matches").fetchone()[0]
    circuits = dict(con.execute("SELECT circuit, count(*) FROM tennis_matches GROUP BY 1").fetchall())
    assert n_foot > 30000 and circuits["ATP"] > 10000 and circuits["CHALLENGER"] > 20000
    seasons = con.execute("SELECT count(DISTINCT season_start) FROM football_matches").fetchone()[0]
    years = con.execute("SELECT count(DISTINCT season) FROM tennis_matches").fetchone()[0]
    assert seasons >= 5 and years >= 5


@needs_db
def test_database_odds_are_plausible(con):
    over_ps, fav = con.execute(
        "SELECT avg(1/ps_w + 1/ps_l), avg((ps_w < ps_l)::int) FROM tennis_matches WHERE ps_w IS NOT NULL AND ps_l IS NOT NULL"
    ).fetchone()
    assert 1.01 < over_ps < 1.06 and 0.62 < fav < 0.75
    over_f, home = con.execute(
        "SELECT avg(1/avg_ch + 1/avg_cd + 1/avg_ca), avg((ftr = 'H')::int) FROM football_matches WHERE avg_ch IS NOT NULL"
    ).fetchone()
    assert 1.03 < over_f < 1.10 and 0.38 < home < 0.48


@needs_db
def test_real_backtest_is_consistent(con):
    r = bt.run_backtest({"sport": "football", "market": "1x2", "selection": ["home"], "price": "avg",
                         "filters": {"league": {"in": ["Serie A"]}}}, con)
    m, t = r.metrics, r.trades
    assert m["trades"] > 1500
    assert m["profit"] == pytest.approx(t["pnl"].sum())
    assert m["roi_pct"] == pytest.approx(t["pnl"].sum() / len(t) * 100)
    assert r.monthly["profit"].sum() == pytest.approx(m["profit"])
    assert r.monthly["trades"].sum() == m["trades"]
    assert -15 < m["roi_pct"] < 5          # puntare alla cieca la media mercato non puo' rendere molto
    assert t["match_id"].is_unique and t["date"].is_monotonic_increasing
    # back e lay della stessa selezione senza commissioni e a puntata fissa sono speculari
    lay = bt.run_backtest({"sport": "football", "market": "1x2", "selection": ["home"], "price": "avg",
                           "side": "lay", "lay_mode": "stake", "filters": {"league": {"in": ["Serie A"]}}}, con)
    assert lay.metrics["profit"] == pytest.approx(-m["profit"])


@needs_db
def test_real_tennis_backtest(con):
    r = bt.run_backtest({"sport": "tennis", "selection": ["favourite"], "price": "ps"}, con)
    assert r.metrics["trades"] > 8000 and 60 < r.metrics["win_rate_pct"] < 80
    assert -8 < r.metrics["roi_pct"] < 2
    assert r.metrics["clv_pct"] == pytest.approx(0.0)  # prezzo di ingresso = chiusura Pinnacle


# ------------------------------------------------------------------ gestionale
from src import ledger as lg  # noqa: E402


def test_ledger_pnl_and_clv_formulas():
    assert lg.bet_pnl("back", 2.5, 10, "won", 0.05) == pytest.approx(10 * 1.5 * 0.95)
    assert lg.bet_pnl("back", 2.5, 10, "lost") == -10
    assert lg.bet_pnl("lay", 3.0, 20, "won", 0.02) == pytest.approx(20 / 2 * 0.98)   # responsabilita' 20 -> puntata banco 10
    assert lg.bet_pnl("lay", 3.0, 20, "lost") == -20
    assert lg.bet_pnl("back", 2.0, 10, "void") == 0.0 and lg.bet_pnl("back", 2.0, 10, "open") is None
    assert lg.clv_pct("back", 2.10, 2.00) == pytest.approx(5.0)
    assert lg.clv_pct("lay", 1.90, 2.00) == pytest.approx(5.0)
    assert np.isnan(lg.clv_pct("back", 2.0, None))


def test_ledger_accounts_bets_and_balances(tmp_path):
    from datetime import date
    with lg.open_ledger(tmp_path / "g.duckdb") as con:
        lg.add_account(con, "Exchange", "Exchange", 1000)
        lg.add_account(con, "Book", "Bookmaker")
        with pytest.raises(ValueError):
            lg.add_account(con, "exchange")
        lg.add_movement(con, "Book", "deposit", 200)
        lg.add_movement(con, "Exchange", "withdrawal", 100)
        kw = dict(placed_date=date(2026, 1, 10), sport="tennis", event="A vs B", selection="A")
        b1 = lg.add_bet(con, side="back", odds=2.0, stake=50, account="Exchange", strategy="S1", commission=0.05, **kw)
        b2 = lg.add_bet(con, side="back", odds=3.0, stake=20, account="Book", strategy="S1", status="lost", close_odds=2.8, **kw)
        b3 = lg.add_bet(con, side="lay", odds=1.5, stake=30, account="Exchange", strategy="", **kw)
        for bad in (dict(odds=1.0, stake=10), dict(odds=2.0, stake=0)):
            with pytest.raises(ValueError):
                lg.add_bet(con, side="back", account="Book", **kw, **bad)
        with pytest.raises(ValueError):
            lg.add_bet(con, side="back", odds=2.0, stake=10, account="Inesistente", **kw)

        acc = lg.accounts(con).set_index("conto")
        assert acc.loc["Exchange", "saldo"] == pytest.approx(900) and acc.loc["Exchange", "in_gioco"] == pytest.approx(80)
        assert acc.loc["Exchange", "disponibile"] == pytest.approx(820)
        assert acc.loc["Book", "saldo"] == pytest.approx(180)

        lg.settle_bet(con, b1, "won", close_odds=1.9)
        lg.settle_bet(con, b3, "won")
        acc = lg.accounts(con).set_index("conto")
        assert acc.loc["Exchange", "saldo"] == pytest.approx(900 + 47.5 + 60) and acc.loc["Exchange", "in_gioco"] == 0
        tot = lg.totals(con)
        assert tot["saldo"] == pytest.approx(1100 + 47.5 + 60 - 20) and tot["profitto"] == pytest.approx(87.5)

        df = lg.bets(con)
        t = lg.settled_trades(df)
        m = bt.compute_metrics(t, 1100)
        assert m["trades"] == 3 and m["profit"] == pytest.approx(87.5) and m["roi_pct"] == pytest.approx(87.5 / 100 * 100)
        assert df.set_index("id").loc[b1, "clv_pct"] == pytest.approx((2.0 / 1.9 - 1) * 100)
        s = lg.by_strategy(df, {"S1": {"metrics": {"roi_pct": 4.0, "clv_pct": 1.5}}}).set_index("strategia")
        assert s.loc["S1", "giocate"] == 2 and s.loc["S1", "roi_pct"] == pytest.approx(27.5 / 70 * 100)
        assert s.loc["S1", "roi_backtest_pct"] == 4.0 and np.isnan(s.loc["(senza strategia)", "roi_backtest_pct"])

        lg.settle_bet(con, b1, "open")           # riapertura: il profitto torna a zero
        assert lg.totals(con)["profitto"] == pytest.approx(40.0)
        lg.delete_bet(con, b2)
        assert lg.totals(con)["profitto"] == pytest.approx(60.0)
        with pytest.raises(ValueError):
            lg.delete_account(con, "Exchange")

        signals = pd.DataFrame({"strategia": ["S1", "S1"], "data": ["2026-02-01", "2026-02-01"], "evento": ["C vs D", "E vs F"],
                                "selezione": ["C", "E"], "lato": ["PUNTA", "BANCA"], "quota": [1.8, 2.2],
                                "importo_rischio": [10.0, 0.0]})
        assert lg.add_signals(con, signals, "Book", "tennis", {"S1": {"config": {"commission": 0.02}}}) == 1
        last = lg.bets(con, "open").iloc[-1]
        assert last["event"] == "C vs D" and last["commission"] == 0.02 and last["account"] == "Book"


# ------------------------------------------------------------------ roadmap
def test_roadmap_state_is_saved_and_merged(tmp_path):
    from src import roadmap as rm
    p = tmp_path / "roadmap.json"
    base = rm.items(p)
    assert len(base) == len(rm.DEFAULT_ITEMS) and all(i["status"] == "todo" for i in base)
    assert {i["quarter"] for i in base} == set(rm.QUARTERS) and {i["track"] for i in base} == set(rm.TRACKS)
    assert len({i["id"] for i in base}) == len(base)
    rm.update_item("q1-paper", status="doing", note="partito il 7 gennaio", path=p)
    rm.update_item("q1-rules", status="done", path=p)
    mine = rm.add_item("Q2", "Metodo", "Aprire conto exchange", "con verifica documenti", path=p)
    rm.update_item(mine, status="done", path=p)
    now = {i["id"]: i for i in rm.items(p)}
    assert now["q1-paper"]["status"] == "doing" and now["q1-paper"]["note"] == "partito il 7 gennaio"
    assert now[mine]["custom"] and now[mine]["status"] == "done" and now[mine]["quarter"] == "Q2"
    prog = rm.progress(list(now.values()))
    assert prog["all"] == (2, len(base) + 1) and prog["Q1"][0] == 1 and prog["Q2"][0] == 1
    with pytest.raises(ValueError):
        rm.update_item("q1-paper", status="boh", path=p)
    with pytest.raises(ValueError):
        rm.add_item("Q9", "Metodo", "x", path=p)
    rm.delete_item(mine, path=p)
    assert mine not in {i["id"] for i in rm.items(p)}
    p.write_text("{rotto", encoding="utf-8")       # file danneggiato: si riparte dalle voci predefinite
    assert len(rm.items(p)) == len(rm.DEFAULT_ITEMS)


# ------------------------------------------------------------------ partite in programma e aggiornamento
from src import fixtures as fx  # noqa: E402
from src import strategies as sv  # noqa: E402


def test_fixtures_parser_and_book_fallback():
    raw = ("﻿Div,Date,Time,HomeTeam,AwayTeam,AvgH,AvgD,AvgA,MaxH,MaxD,MaxA,Avg>2.5,Avg<2.5,Max>2.5,Max<2.5,AHh\n"
           "I1,21/11/2026,20:45,Inter,Genoa,1.38,4.9,8.5,1.42,5.1,,1.62,2.3,1.66,2.36,-1.5\n"
           "EC,22/11/2026,15:00,Woking,Altrincham,2.1,3.4,3.3,2.2,3.5,3.5,1.9,1.9,1.95,1.95,-0.25\n"
           ",,,,,,,,,,,,,,,\n").encode()
    avg, mx = fx.parse_football_fixtures(raw, "avg"), fx.parse_football_fixtures(raw, "max")
    assert avg["home_team"].tolist() == ["Inter", "Woking"] and avg["date"].tolist() == ["2026-11-21", "2026-11-22"]
    assert avg.loc[0, "league"] == "Serie A" and avg.loc[1, "league"] == "EC"      # campionato fuori database: resta il codice
    assert avg.loc[0, "odds_home"] == 1.38 and mx.loc[0, "odds_home"] == 1.42
    assert mx.loc[0, "odds_away"] == 8.5                                             # massimo assente -> media mercato
    assert mx.loc[0, "odds_over25"] == 1.66 and avg.loc[0, "ah_line"] == -1.5
    assert sv.detect_sport(avg) == "football"
    assert fx.parse_football_fixtures(b"niente", "avg").empty


def test_odds_api_parser_and_fetch_without_network():
    events = [{"commence_time": "2026-11-17T13:00:00Z", "home_team": "Jannik Sinner", "away_team": "Alex de Minaur", "bookmakers": [
        {"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": [{"name": "Jannik Sinner", "price": 1.25}, {"name": "Alex de Minaur", "price": 4.4}]}]},
        {"key": "other", "markets": [{"key": "h2h", "outcomes": [{"name": "Alex de Minaur", "price": 4.0}, {"name": "Jannik Sinner", "price": 1.21}]},
                                     {"key": "spreads", "outcomes": [{"name": "Jannik Sinner", "price": 9.9}]}]},
        {"key": "broken", "markets": [{"key": "h2h", "outcomes": [{"name": "Jannik Sinner", "price": 1.3}]}]}]},
        {"commence_time": "x", "home_team": "A", "away_team": "B", "bookmakers": []}]
    rows = fx.parse_odds_api_events(events, "Paris Masters")
    assert len(rows) == 1 and rows[0]["bookmakers"] == 2
    assert rows[0]["odds1"] == pytest.approx(1.23) and rows[0]["max2"] == 4.4 and rows[0]["ps1"] == 1.25
    assert rows[0]["date"] == "2026-11-17" and rows[0]["time"] == "14:00"            # ora italiana

    class Resp:
        def __init__(self, data, status=200):
            self._d, self.status_code, self.headers = data, status, {"x-requests-remaining": "480"}
        def json(self):
            return self._d
        def raise_for_status(self):
            pass

    class Fake:
        calls = []
        def get(self, url, params=None, timeout=None):
            Fake.calls.append(url)
            if url.endswith("/sports/"):
                return Resp([{"key": "tennis_atp_paris_masters", "title": "ATP Paris Masters", "active": True},
                             {"key": "tennis_wta_x", "title": "WTA X", "active": True},
                             {"key": "tennis_atp_old", "title": "ATP Old", "active": False}])
            return Resp(events)

    df, msg = fx.fetch_tennis_odds("chiave", "max", session=Fake())
    assert len(Fake.calls) == 2 and "tennis_atp_paris_masters" in Fake.calls[1]      # solo tornei ATP attivi: 1 credito
    assert df.loc[0, "tournament"] == "Paris Masters" and df.loc[0, "odds2"] == 4.4 and "480" in msg
    assert sv.detect_sport(df) == "tennis"
    assert fx.fetch_tennis_odds("", "avg")[0].empty

    class Denied(Fake):
        def get(self, url, params=None, timeout=None):
            return Resp([], 401)
    assert "Chiave" in fx.fetch_tennis_odds("sbagliata", session=Denied())[1]


def test_settings_roundtrip(tmp_path):
    p = tmp_path / "settings.json"
    assert fx.load_settings(p) == fx.DEFAULT_SETTINGS
    fx.save_settings({"auto_update_hours": 24}, p)
    assert fx.load_settings(p)["auto_update_hours"] == 24 and fx.load_settings(p)["auto_update"] is True
    p.write_text("rotto")
    assert fx.load_settings(p) == fx.DEFAULT_SETTINGS


def test_fetch_reports_unchanged_files(tmp_path, monkeypatch):
    cache = tmp_path / "f.csv"
    served = {"body": b"Div,Date\nI1,01/01/2026\n"}
    monkeypatch.setattr(ud, "http_get", lambda url, retries=3: served["body"])
    ok = lambda b: b"Date" in b
    assert ud.fetch(cache, ["u"], True, ok) == (served["body"], "u", True)       # primo download
    assert ud.fetch(cache, ["u"], True, ok)[2] is False                          # identico: niente da ricaricare
    assert ud.fetch(cache, ["u"], False, ok)[1] == "cache"
    served["body"] += b"I1,02/01/2026\n"
    assert ud.fetch(cache, ["u"], True, ok)[2] is True and cache.read_bytes() == served["body"]
    served["body"] = None                                                         # sito irraggiungibile: resta la cache
    raw, origin, changed = ud.fetch(cache, ["u"], True, ok)
    assert raw is not None and "fallito" in origin and changed is False
    assert ud.parallel({i: (lambda i=i: i * 2) for i in range(5)}) == {i: i * 2 for i in range(5)}
    from datetime import date
    assert ud.refresh_window(date(2026, 11, 16)) == (1, 1) and ud.refresh_window(date(2027, 1, 10)) == (1, 2)
    assert ud.refresh_window(date(2027, 7, 1)) == (2, 1)


def test_player_spellings_are_unified(tmp_path):
    import duckdb
    con = duckdb.connect(str(tmp_path / "n.duckdb"))
    ud.init_db(con)
    rows = [("a", "ATP", "Felix Auger-Aliassime", "X Y"), ("b", "ATP", "Felix Auger-Aliassime", "X Y"),
            ("c", "CHALLENGER", "Felix Auger Aliassime", "X Y"), ("d", "CHALLENGER", "X Y", "Felix Auger Aliassime"),
            ("e", "CHALLENGER", "X Y", "Felix Auger Aliassime")]
    for mid, circuit, w, l in rows:
        con.execute("INSERT INTO tennis_matches (match_id, circuit, winner_name, loser_name, match_date) VALUES (?, ?, ?, ?, DATE '2026-01-01')", [mid, circuit, w, l])
    assert ud.canonicalize_tennis_names(con) == 1
    names = {r[0] for r in con.execute("SELECT winner_name FROM tennis_matches UNION SELECT loser_name FROM tennis_matches").fetchall()}
    assert names == {"Felix Auger-Aliassime", "X Y"}                                # vince la grafia del circuito ATP
    assert ud.canonicalize_tennis_names(con) == 0
    con.close()


@needs_db
def test_upcoming_match_features_equal_backtest_features(con):
    """Le variabili calcolate per una partita 'in programma' devono coincidere con quelle usate dal backtest."""
    h = con.execute("SELECT * FROM football_matches WHERE home_pos IS NOT NULL ORDER BY match_date DESC LIMIT 200").df().iloc[60:90]
    up = h.rename(columns={"match_date": "date", "avg_h": "odds_home", "avg_d": "odds_draw", "avg_a": "odds_away"})[
        ["date", "div", "home_team", "away_team", "odds_home", "odds_draw", "odds_away"]]
    s = sv.football_upload_selections(up, "1x2", con)
    a = s[s["selection"] == "home"].reset_index(drop=True)
    for k in ("home_ppg5", "away_pos", "home_sppg", "away_venue_ppg5", "home_sot5", "away_rest_days"):
        assert np.allclose(a[k].to_numpy(dtype=float), h[k].to_numpy(dtype=float), equal_nan=True), k
    assert np.allclose(a["pos_diff"], (h["away_pos"] - h["home_pos"]).to_numpy(), equal_nan=True)

    t = con.execute("SELECT * FROM tennis_matches WHERE has_odds AND match_id NOT LIKE 'TD:%' ORDER BY match_date DESC LIMIT 20").df()
    tup = pd.DataFrame({"date": t["match_date"], "tournament": t["tourney_name"], "player1": t["loser_name"].str.upper(),
                        "player2": t["winner_name"], "odds1": t["avg_l"], "odds2": t["avg_w"]})
    ts = sv.tennis_upload_selections(tup, con).iloc[:len(t)].reset_index(drop=True)
    assert (ts["player"] == t["loser_name"]).all()                                   # nomi riallineati al database
    assert (ts["surface"] == t["surface"]).all()                                     # superficie ricavata dal torneo
    for k, col, mult in (("form10", "loser_form10", 100), ("h2h_wins", "loser_h2h_wins", 1), ("h2h_losses", "winner_h2h_wins", 1),
                         ("srv_won10", "loser_srv_won10", 100), ("opp_form_surf", "winner_form_surf", 100)):
        assert np.allclose(ts[k].to_numpy(dtype=float), t[col].to_numpy(dtype=float) * mult, equal_nan=True), k


@needs_db
def test_future_fixture_does_not_count_as_played(con):
    last = con.execute("SELECT home_team, away_team, div, max(match_date) AS d FROM football_matches WHERE div = 'I1' GROUP BY ALL ORDER BY d DESC LIMIT 1").df()
    home, away = last.loc[0, "home_team"], last.loc[0, "away_team"]
    when = pd.Timestamp(last.loc[0, "d"]) + pd.Timedelta(days=7)
    up = pd.DataFrame({"date": [when, when + pd.Timedelta(days=4)], "div": "I1", "home_team": [home, away], "away_team": [away, home],
                       "odds_home": 2.0, "odds_draw": 3.3, "odds_away": 3.8})
    s = sv.football_upload_selections(up, "1x2", con)
    a = s[s["selection"] == "home"].reset_index(drop=True)
    assert a.loc[0, "home_rest_days"] == 7 and a.loc[1, "away_rest_days"] == 4       # la seconda vede la prima come impegno precedente
    assert a.loc[0, "home_sppg"] == pytest.approx(a.loc[1, "away_sppg"])            # ma non come partita giocata: punti invariati
    assert a.loc[0, "home_pos"] == a.loc[1, "away_pos"]


# ------------------------------------------------------------------ cash out
from src import cashout as co  # noqa: E402


def test_cashout_value_formulas():
    t = pd.DataFrame({"odds": [2.0, 2.0, 3.0, 3.0, 2.0], "risk": [10.0, 10.0, 20.0, 20.0, 10.0],
                      "side": ["back", "back", "lay", "lay", "back"]})
    p = np.array([0.8, 0.2, 0.1, 0.9, 0.0])
    v = co.cashout_value(t, p, margin=0.05, commission=0.0)
    assert v[0] == pytest.approx(10 * (2.0 * 0.8 * 0.95 - 1))          # back in profitto: +5.2
    assert v[1] == pytest.approx(10 * (2.0 * 0.2 * 0.95 - 1))          # back in perdita: -6.2
    assert v[2] == pytest.approx(10 * (1 - 3.0 * 0.1 * 1.05))          # lay (banco 10): selezione crollata -> +6.85
    assert v[3] == pytest.approx(10 * (1 - 3.0 * 0.9 * 1.05))          # lay andato male: -18.35
    assert v[4] == pytest.approx(-10.0)                                # mai peggio della perdita totale
    net = co.cashout_value(t, p, margin=0.05, commission=0.02)
    assert net[0] == pytest.approx(v[0] * 0.98) and net[1] == pytest.approx(v[1])   # commissione solo sui profitti


def test_tennis_set_model(monkeypatch):
    monkeypatch.setattr(co, "SET_MOMENTUM", 0.0)
    s = co.set_prob_from_match(np.array([0.5, 0.784, 0.5]), np.array([3, 3, 5]))
    assert s == pytest.approx([0.5, 0.7, 0.5], abs=2e-3)               # 0.7^2 * (3 - 1.4) = 0.784
    ahead = co.match_prob_after_first_set(np.array([0.7, 0.5]), np.array([3, 5]), np.array([True, True]))
    behind = co.match_prob_after_first_set(np.array([0.7, 0.5]), np.array([3, 5]), np.array([False, False]))
    assert ahead == pytest.approx([0.7 * 1.3, 0.25 * (1 + 1 + 0.75)])  # bo3: s(2-s); bo5: vincere 2 set su 4
    assert behind == pytest.approx([0.49, 0.125 * 2.5])                # bo3: s^2;   bo5: vincere 3 set su 4
    # coerenza: probabilita' prima = media pesata delle probabilita' dopo il primo set
    assert 0.7 * ahead[0] + 0.3 * behind[0] == pytest.approx(0.784)
    monkeypatch.setattr(co, "SET_MOMENTUM", 0.2)
    more = co.match_prob_after_first_set(np.array([0.7]), np.array([3]), np.array([True]))
    assert more[0] > ahead[0]


def test_football_halftime_model():
    base = {"odds_home": 2.0, "odds_draw": 3.5, "odds_away": 4.0, "odds_over": 1.9, "odds_under": 1.9}
    rows = []
    for ht in ((0, 0), (3, 0), (0, 3), (2, 2)):
        for sel in ("home", "draw", "away", "over", "under"):
            rows.append({**base, "ht_home": ht[0], "ht_away": ht[1], "selection": sel})
    t = pd.DataFrame(rows)
    p = co.football_checkpoint_prob(t)
    g = t.assign(p=p).set_index(["ht_home", "ht_away", "selection"])["p"]
    for ht in ((0, 0), (3, 0), (0, 3), (2, 2)):
        assert g[ht[0], ht[1], "home"] + g[ht[0], ht[1], "draw"] + g[ht[0], ht[1], "away"] == pytest.approx(1.0, abs=1e-6)
        assert g[ht[0], ht[1], "over"] + g[ht[0], ht[1], "under"] == pytest.approx(1.0)
    assert g[3, 0, "home"] > 0.95 and g[0, 3, "home"] < 0.03
    assert g[0, 0, "home"] > g[0, 0, "away"]                            # la favorita resta favorita sullo 0-0
    assert g[0, 0, "draw"] > 1 / 3.5 / (1 / 2 + 1 / 3.5 + 1 / 4)        # sullo 0-0 il pareggio e' piu' probabile che a inizio gara
    assert g[3, 0, "over"] == pytest.approx(1.0) and g[2, 2, "under"] == pytest.approx(0.0)
    assert 0.1 < g[0, 0, "over"] < 0.3                                   # 0-0 all'intervallo: servono 3 gol in un tempo
    lam = co.fit_goal_rates(np.array([0.45]), np.array([0.27]))
    assert lam[0, 0] > lam[0, 1] and 2.0 < lam.sum() < 3.5
    assert np.isnan(co.football_checkpoint_prob(t.assign(ht_home=np.nan))).all()


def test_smart_cashout_triggers_and_keeps_hold_result(monkeypatch):
    monkeypatch.setattr(co, "SET_MOMENTUM", 0.0)
    sel = make_sel([2.0, 2.0, 2.0, 2.0], [True, False, True, False])
    sel["opp_odds"], sel["best_of"], sel["set1_won"] = 2.0, 3.0, [1.0, 1.0, 0.0, np.nan]
    cfg = {"cashout": {"mode": "smart", "take_profit": True, "tp_pct": 30, "stop_loss": True, "sl_pct": 40, "margin_pct": 0}}
    r = bt.backtest_selections(sel, cfg)
    t = r.trades
    # quota 2.0 alla pari: dopo il primo set vinto p = 0.75 -> cash out +0.5; perso p = 0.25 -> -0.5
    assert t["cashout"].tolist() == ["tp", "tp", "sl", ""]
    assert t["pnl"].tolist() == pytest.approx([0.5, 0.5, -0.5, -1.0])
    assert t["pnl_hold"].tolist() == pytest.approx([1.0, -1.0, 1.0, -1.0])
    assert r.metrics["profit"] == pytest.approx(-0.5) and r.metrics["wins"] == 2
    assert t["equity"].tolist() == pytest.approx([0.5, 1.0, 0.5, -0.5])
    only_tp = bt.backtest_selections(sel, {"cashout": {"mode": "smart", "tp_pct": 60, "margin_pct": 0}}).trades
    assert (only_tp["cashout"] == "").all()                              # soglia non raggiunta: nessuna chiusura
    off = bt.backtest_selections(sel, {"cashout": {"mode": "off"}}).trades
    assert "cashout" not in off.columns and off["pnl"].tolist() == [1.0, -1.0, 1.0, -1.0]
    grid = co.threshold_grid(off, cfg)
    row = grid[(grid["incassa_da"] == "+30%") & (grid["taglia_da"] == "-30%")].iloc[0]
    assert row["profit"] == pytest.approx(-0.5) and row["chiuse_pct"] == pytest.approx(75.0)
    assert grid[(grid["incassa_da"] == "mai") & (grid["taglia_da"] == "mai")].iloc[0]["profit"] == pytest.approx(0.0)


def test_manual_cashout_rules_are_reproducible():
    rng = np.random.default_rng(5)
    n = 4000
    odds = np.round(rng.uniform(1.5, 3.0, n), 2)
    sel = make_sel(odds.tolist(), (rng.random(n) < 0.45).tolist(),
                   dates=pd.date_range("2015-01-01", periods=n, freq="D").strftime("%Y-%m-%d").tolist())
    rules = {"mode": "manual", "win_early_share": 30, "win_early_keep": 60, "win_panic_share": 10, "win_panic_loss": 25,
             "lose_saved_share": 20, "lose_saved_gain": 40, "lose_cut_share": 25, "lose_cut_recover": 50, "seed": 3}
    t = bt.backtest_selections(sel, {"cashout": rules}).trades
    won = t["pnl_hold"] > 0
    share = lambda k, mask: (t.loc[mask, "cashout"] == k).mean() * 100
    assert share("win_early", won) == pytest.approx(30, abs=3) and share("win_panic", won) == pytest.approx(10, abs=2.5)
    assert share("lose_saved", ~won) == pytest.approx(20, abs=3) and share("lose_cut", ~won) == pytest.approx(25, abs=3)
    e, pn, sv_, cu = (t[t["cashout"] == k] for k in ("win_early", "win_panic", "lose_saved", "lose_cut"))
    assert np.allclose(e["pnl"], e["pnl_hold"] * 0.60) and np.allclose(pn["pnl"], -0.25)
    assert np.allclose(sv_["pnl"], (sv_["odds"] - 1) * 0.40) and np.allclose(cu["pnl"], -0.50)
    untouched = t[t["cashout"] == ""]
    assert np.allclose(untouched["pnl"], untouched["pnl_hold"])
    again = bt.backtest_selections(sel, {"cashout": rules}).trades
    assert again["pnl"].equals(t["pnl"])                                  # stesso seme -> stesso risultato
    other = bt.backtest_selections(sel, {"cashout": {**rules, "seed": 4}}).trades
    assert not other["cashout"].equals(t["cashout"])
    # aggiungere un filtro non cambia la sorte delle giocate rimaste
    sub = bt.backtest_selections(sel, {"cashout": rules, "odds_max": 2.2}).trades
    assert sub.set_index("match_id")["cashout"].equals(t.set_index("match_id")["cashout"].reindex(sub["match_id"]))
    s = co.summary(t)
    assert s["giocate"].sum() == n and s["profitto"].sum() == pytest.approx(t["pnl"].sum())


def test_first_set_is_read_from_score():
    m = tennis_frame()
    m["score"] = ["6-4 6-2", "W/O", "3-6 7-6(4) 6-1"]
    s = bt.tennis_selections(m, "avg").set_index(["match_id", "won"])["set1_won"]
    assert s["a", True] == 1.0 and s["a", False] == 0.0
    assert s["c", True] == 0.0 and s["c", False] == 1.0                   # il vincitore del match ha perso il primo set


def test_ledger_manual_cashout(tmp_path):
    from datetime import date
    with lg.open_ledger(tmp_path / "c.duckdb") as con:
        lg.add_account(con, "Book", "Bookmaker", 500)
        kw = dict(placed_date=date(2026, 3, 1), sport="football", event="A - B", selection="1", side="back", account="Book")
        b1 = lg.add_bet(con, odds=2.5, stake=40, **kw)
        b2 = lg.add_bet(con, odds=1.8, stake=50, **kw)
        lg.settle_bet(con, b1, "cashout", cash_pnl=22.5)                  # chiusa in profitto
        lg.settle_bet(con, b2, "cashout", cash_pnl=-18.0)                 # chiusa in perdita
        for bad in (None, -60.0):
            with pytest.raises(ValueError):
                lg.settle_bet(con, b2, "cashout", cash_pnl=bad)
        assert lg.totals(con)["profitto"] == pytest.approx(4.5) and lg.totals(con)["in_gioco"] == 0
        t = lg.settled_trades(lg.bets(con))
        assert len(t) == 2 and t["trade_won"].tolist() == [True, False]
        assert bt.compute_metrics(t, 500)["roi_pct"] == pytest.approx(4.5 / 90 * 100)
        lg.settle_bet(con, b1, "open")
        assert lg.totals(con)["profitto"] == pytest.approx(-18.0)


@needs_db
def test_cashout_model_is_calibrated_on_real_matches(con):
    """La probabilita' stimata al punto di controllo deve essere in linea con la frequenza reale."""
    for cfg, tol in (({"sport": "football", "market": "1x2", "selection": ["home", "draw", "away"]}, 0.05),
                     ({"sport": "football", "market": "ou25", "selection": ["over", "under"]}, 0.03),
                     ({"sport": "tennis", "selection": ["favourite", "underdog"]}, 0.03)):
        t = bt.run_backtest(cfg, con).trades
        p, won = co.checkpoint_prob(t), t["won"].to_numpy(dtype=float)
        ok = np.isfinite(p)
        assert ok.mean() > 0.97
        for lo, hi in ((0.1, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.9)):
            k = ok & (p >= lo) & (p < hi)
            assert abs(p[k].mean() - won[k].mean()) < tol, (cfg["sport"], cfg.get("market"), lo)
    # senza margine il cash out e' quasi a somma zero; con il margine costa
    base = {"sport": "football", "market": "1x2", "selection": ["home"], "odds_min": 1.5, "odds_max": 2.6}
    hold = bt.run_backtest(base, con).metrics["roi_pct"]
    fair = bt.run_backtest({**base, "cashout": {"mode": "smart", "tp_pct": 20, "stop_loss": True, "sl_pct": 30, "margin_pct": 0}}, con).metrics["roi_pct"]
    costly = bt.run_backtest({**base, "cashout": {"mode": "smart", "tp_pct": 20, "stop_loss": True, "sl_pct": 30, "margin_pct": 10}}, con).metrics["roi_pct"]
    assert abs(fair - hold) < 2.0 and costly < fair


# ------------------------------------------------------------------ analisi operativa
from src import analysis as an  # noqa: E402


def test_hedge_calculator():
    h = an.hedge("back", 2.5, 40, 1.6)                       # quota scesa: profitto bloccato
    assert h["hedge_stake"] == pytest.approx(62.5) and h["profit"] == pytest.approx(22.5) and h["hedge_side"] == "banca"
    assert 40 * 1.5 - 62.5 * 0.6 == pytest.approx(62.5 - 40)  # stesso risultato nei due esiti
    assert an.hedge("back", 2.5, 40, 4.0)["profit"] == pytest.approx(-15.0)          # quota salita: perdita bloccata
    lay = an.hedge("lay", 3.0, 10, 6.0)
    assert lay["hedge_stake"] == pytest.approx(5.0) and lay["profit"] == pytest.approx(5.0) and lay["risk"] == 20.0
    c = an.hedge("back", 2.5, 40, 1.6, commission=0.05)
    win, lose = 40 * 1.5 - c["hedge_stake"] * 0.6, c["hedge_stake"] - 40
    assert win * 0.95 == pytest.approx(lose * 0.95, abs=1e-6) and c["profit"] == pytest.approx(22.5 * 0.95)
    with pytest.raises(ValueError):
        an.hedge("back", 1.0, 10, 2.0)


def test_cashout_breakeven_matches_simulation():
    rng = np.random.default_rng(11)
    n = 6000
    odds = np.round(rng.uniform(1.6, 2.6, n), 2)
    sel = make_sel(odds.tolist(), (rng.random(n) < 0.93 / odds).tolist(),
                   dates=pd.date_range("2010-01-01", periods=n, freq="D").strftime("%Y-%m-%d").tolist())
    t = bt.settle(sel)
    be = an.breakeven(t)
    assert be["roi_hold_pct"] < 0 and be["n_won"] + be["n_lost"] == n
    assert be["grid"].iloc[0, 0] == pytest.approx(be["roi_hold_pct"])                 # nessun taglio = tenere tutto
    need = be["need"].set_index("recupero_pct")["quota_perdenti_da_tagliare_pct"]
    assert need[80] < need[50] < need[20]                                             # piu' recuperi, meno devi tagliare
    assert an.breakeven_custom(be, need[50], 50) == pytest.approx(0.0, abs=1e-9)      # alla soglia il ROI atteso e' zero
    # il valore atteso coincide con la simulazione manuale (a meno della sorte)
    sim = bt.backtest_selections(sel, {"cashout": {"mode": "manual", "lose_cut_share": 40, "lose_cut_recover": 50,
                                                   "win_early_share": 30, "win_early_keep": 70}}).metrics["roi_pct"]
    assert an.breakeven_custom(be, 40, 50, 30, 70) == pytest.approx(sim, abs=1.0)
    winning = an.breakeven(bt.settle(make_sel([2.0, 2.0, 2.0], [True, True, False])))
    assert (winning["need"]["quota_perdenti_da_tagliare_pct"] == 0).all()
    hopeless = an.breakeven(bt.settle(make_sel([1.05] * 10, [True] + [False] * 9)))
    assert hopeless["need"].iloc[0].isna().any()                                      # col 20% di recupero non basta tagliarle tutte


def test_history_finds_similar_odds():
    sel = pd.DataFrame({"selection": ["home"] * 6 + ["draw"] * 2, "odds": [2.0, 2.05, 1.95, 2.1, 3.0, 2.0, 3.4, 3.5],
                        "won": [True, False, True, True, False, False, True, False],
                        "league": ["A", "A", "A", "B", "A", "B", "A", "A"]})
    h = an.History(sel)
    s = h.similar("home", 2.0, "A", tol=0.07, min_n=3)
    assert s["n"] == 3 and s["scope"] == "A" and s["hit_pct"] == pytest.approx(200 / 3)
    wide = h.similar("home", 2.0, "A", tol=0.07, min_n=10)                             # pochi nella lega: tutte le leghe
    assert wide["n"] == 5 and wide["scope"] == "tutte le leghe" and wide["hit_pct"] == pytest.approx(60.0)
    assert wide["roi_pct"] == pytest.approx((1.0 - 1 + 0.95 + 1.1 - 1) / 5 * 100)
    assert h.similar("home", 9.0)["n"] == 0 and h.similar("over", 2.0)["n"] == 0


@needs_db
def test_daily_board_and_strategy_monitor(con):
    last = con.execute("SELECT * FROM football_matches WHERE div = 'I1' AND avg_o25 IS NOT NULL ORDER BY match_date DESC LIMIT 10").df()
    fixtures = pd.DataFrame({"date": pd.to_datetime(last["match_date"]).dt.strftime("%Y-%m-%d"), "time": "20:45", "div": "I1",
                             "league": "Serie A", "home_team": last["home_team"], "away_team": last["away_team"],
                             "odds_home": last["avg_h"], "odds_draw": last["avg_d"], "odds_away": last["avg_a"],
                             "odds_over25": last["avg_o25"], "odds_under25": last["avg_u25"], "ah_line": last["ah_line"]})
    hist = an.History(bt.load_selections("football", "1x2", "avg", "open", con), bt.load_selections("football", "ou25", "avg", "open", con))
    vault = {"casa": {"config": {"sport": "football", "market": "1x2", "selection": ["home"], "price": "avg", "odds_min": 1.01, "odds_max": 50},
                      "metrics": {"roi_pct": 1.0}}}
    board, detail = an.daily_board(fixtures, con, hist, vault, min_n=100)
    assert len(board) == 10 and len(detail) == 50 and (board["segnali"] == "casa").all()
    assert np.allclose(board["pos_casa"], last["home_pos"], equal_nan=True)            # stesse variabili del backtest
    g = detail.groupby("row")
    assert np.allclose(detail[detail["selection"].isin(["home", "draw", "away"])].groupby("row")["implicita_pct"].sum(), 100)
    assert (detail["precedenti"] > 100).all() and detail["storica_pct"].between(0, 100).all()
    assert (detail["scarto_pct"].abs() < 20).all() and g.ngroups == 10                 # a quote simili la frequenza segue la quota
    recent = an.team_recent(con, last.loc[0, "home_team"])
    assert len(recent) == 6 and set(recent["esito"]) <= {"V", "N", "P"}

    table, pool = an.strategy_monitor(vault, lambda s, m, p, t: bt.load_selections(s, m, p, t, con))
    row = table.iloc[0]
    ref = bt.run_backtest(vault["casa"]["config"], con)
    assert row["operazioni"] == ref.metrics["trades"] and row["roi_pct"] == pytest.approx(ref.metrics["roi_pct"])
    assert row["n_90"] <= row["n_365"] <= row["operazioni"] and row["dd_attuale"] <= row["dd_massimo"] + 1e-9
    assert row["stato"] in ("in linea", "utile ma CLV negativo", "al massimo drawdown", "in calo", "poche operazioni recenti")
    assert len(pool) == row["operazioni"] and pool["equity"].iloc[-1] == pytest.approx(ref.metrics["profit"])


# ------------------------------------------------------------------ modello Elo, walk-forward, backup, avvisi
def test_elo_ratings_use_only_past_results():
    f = pd.DataFrame({"match_id": ["a", "b", "c"], "match_date": pd.to_datetime(["2024-01-01", "2024-01-08", "2024-01-15"]),
                      "div": "I1", "home_team": ["X", "Y", "X"], "away_team": ["Y", "X", "Y"],
                      "fthg": [3.0, 0.0, np.nan], "ftag": [0.0, 0.0, np.nan]})
    eh, ea = ud.football_elo(f)
    assert eh[0] == 1500 and ea[0] == 1500                               # prima partita: valori iniziali
    exp = 1 / (1 + 10 ** (-65 / 400))
    gain = 20 * 4 ** 0.5 * (1 - exp)
    assert ea[1] == pytest.approx(1500 + gain) and eh[1] == pytest.approx(1500 - gain)   # X ha vinto 3-0 in casa
    assert eh[2] > 1500 > ea[2] and eh[2] > ea[1]                        # pareggiare fuori casa vale piu' dell'atteso: X sale ancora
    t = pd.DataFrame({"match_id": ["a", "b", "c"], "match_date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
                      "round_order": 4.0, "surface": ["Clay", "Clay", "Hard"], "winner_name": ["P", "P", "Q"], "loser_name": ["Q", "Q", "P"],
                      "is_future": [False, False, True]})
    ew, el = ud.tennis_elo(t)
    assert ew[0] == el[0] == 1500 and ew[1] > 1500 > el[1]
    assert ew[2] < el[2]                                                  # match futuro: Q ha Elo piu' basso di P
    p = bt.football_model_probs([1600, 1500], [1500, 1500])
    assert np.allclose(p.sum(axis=1), 1) and p[0, 0] > p[1, 0] > p[1, 2] and 0.2 < p[1, 1] < 0.33
    assert bt.elo_win_prob(1700, 1500) == pytest.approx(1 / (1 + 10 ** -0.5))


def test_walk_forward_only_uses_previous_years():
    rng = np.random.default_rng(2)
    n = 3000
    odds = np.round(rng.uniform(1.5, 3.5, n), 2)
    dates = pd.date_range("2019-01-01", periods=n, freq="D")
    good = odds < 2.0                                                     # vantaggio solo sulle quote basse
    won = rng.random(n) < np.where(good, 1.12, 0.88) / odds
    t = bt.settle(make_sel(odds.tolist(), won.tolist(), dates=dates.strftime("%Y-%m-%d").tolist()))
    table, s = bt.walk_forward(t, n_bands=4, min_trades=30)
    assert table["anno"].tolist() == sorted(t["date"].dt.year.unique())[2:]
    assert s["roi_pct"] > s["naive_roi_pct"] and s["trades"] < len(t)
    first = table.iloc[0]
    past = t[t["date"].dt.year < first["anno"]]
    assert first["operazioni"] <= (t["date"].dt.year == first["anno"]).sum() and len(past) > 0
    assert bt.walk_forward(t.head(50))[0].empty


def test_clubelo_parsing_and_matching():
    raw = b"Rank,Club,Country,Level,Elo,From,To\n1,Man City,ENG,1,2050.5,2026-11-01,2026-11-20\n2,Inter,ITA,1,1980,2026-11-01,2026-11-20\n" \
          b"None,Forest,ENG,1,1750,2026-11-01,2026-11-20\n3,Atletico,ESP,1,1900,2026-11-01,2026-11-20\n"
    r = fx.parse_clubelo(raw)
    assert len(r) == 4 and r.loc[r["club"] == "Inter", "elo"].iloc[0] == 1980
    got = fx.clubelo_ratings(pd.Series(["Man City", "Inter", "Nott'm Forest", "Ath Madrid", "Sconosciuta"]), r)
    assert got.tolist()[:4] == [2050.5, 1980.0, 1750.0, 1900.0] and np.isnan(got.iloc[4])
    assert fx.parse_clubelo(b"errore").empty and fx.clubelo_ratings(pd.Series(["Inter"]), pd.DataFrame()).isna().all()


def test_backup_and_restore(tmp_path):
    from datetime import date
    from src import backup as bk
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    assert bk.run_backup(root=root, backup_dir=root / "data" / "backup")[0] is None       # niente da salvare
    (root / "strategies.json").write_text('{"s": 1}')
    (root / "data" / "gestionale.duckdb").write_bytes(b"registro")
    (root / "data" / "trading_db.duckdb").write_bytes(b"storici")
    extra = tmp_path / "icloud"
    for d in range(1, 18):
        path, msg = bk.run_backup(extra, root, root / "data" / "backup", date(2026, 1, d))
    assert path.name == "backup_20260117.zip" and (extra / path.name).exists()
    assert len(list((root / "data" / "backup").glob("*.zip"))) == bk.KEEP                 # tiene solo gli ultimi
    import zipfile
    assert sorted(zipfile.ZipFile(path).namelist()) == ["data/gestionale.duckdb", "strategies.json"]   # gli storici non entrano
    (root / "strategies.json").write_text("perso")
    assert bk.restore(path, root) == ["data/gestionale.duckdb", "strategies.json"]
    assert (root / "strategies.json").read_text() == '{"s": 1}'


def test_telegram_alerts_without_network(tmp_path):
    from src import notify

    class Resp:
        def __init__(self, data):
            self._d, self.status_code = data, 200
        def json(self):
            return self._d

    class Fake:
        sent = []
        def post(self, url, json=None, timeout=None):
            Fake.sent.append((url, json))
            return Resp({"ok": True})
        def get(self, url, timeout=None):
            return Resp({"ok": True, "result": [{"message": {"chat": {"id": 111}}}, {"message": {"chat": {"id": 987654}}}]})

    ok, _ = notify.send("TOKEN", "42", "ciao", Fake())
    assert ok and Fake.sent[0][0].endswith("/botTOKEN/sendMessage") and Fake.sent[0][1] == {"chat_id": "42", "text": "ciao"}
    assert notify.send("", "42", "x", Fake())[0] is False
    assert notify.find_chat_id("TOKEN", Fake())[0] == "987654"

    class Refused(Fake):
        def post(self, url, json=None, timeout=None):
            return Resp({"ok": False, "description": "chat not found"})
    assert "chat not found" in notify.send("T", "1", "x", Refused())[1]
    sig = pd.DataFrame({"strategia": ["A", "A"], "data": ["2026-11-21", "2026-11-21"], "evento": ["X - Y", "Z - W"], "selezione": ["X", "Z"],
                        "lato": "PUNTA", "quota": [2.0, 1.8], "importo_rischio": [10.0, 10.0]})
    seen = tmp_path / "n.json"
    assert len(notify.new_signals(sig, seen)) == 2 and len(notify.new_signals(sig, seen)) == 0   # ogni segnale una volta sola
    more = pd.concat([sig, sig.assign(evento="K - J")]).reset_index(drop=True)
    fresh = notify.new_signals(more, seen)
    assert fresh["evento"].tolist() == ["K - J", "K - J"] and "X - Y" not in notify.format_signals(fresh)
    assert "2 nuovi segnali" in notify.format_signals(sig) and "@ 2.00" in notify.format_signals(sig)


@needs_db
def test_model_filters_and_boards(con):
    r = bt.run_backtest({"sport": "tennis", "selection": ["favourite", "underdog"], "price": "avg",
                         "filters": {"model_edge": {"min": 5, "max": None}}}, con)
    assert r.metrics["trades"] > 500 and (r.trades["model_edge"] >= 5).all()
    assert (r.trades["model_prob"].between(0, 100)).all()
    f = bt.load_selections("football", "1x2", "avg", "open", con)
    ok = f["model_prob"].notna()
    assert ok.mean() > 0.95 and np.allclose(f[ok].groupby("match_id")["model_prob"].sum(), 100, atol=1e-6)
    cal = f[ok & (f["model_prob"] > 55)]
    assert abs(cal["won"].mean() * 100 - cal["model_prob"].mean()) < 4          # modello calibrato entro pochi punti
    assert bt.load_selections("football", "ou25", "avg", "open", con)["model_prob"].isna().all()
    t = con.execute("SELECT * FROM tennis_matches WHERE has_odds AND match_id NOT LIKE 'TD:%' ORDER BY match_date DESC LIMIT 8").df()
    odds = pd.DataFrame({"date": pd.to_datetime(t["match_date"]).dt.strftime("%Y-%m-%d"), "time": "12:00", "tournament": t["tourney_name"],
                         "player1": t["winner_name"], "player2": t["loser_name"], "odds1": t["avg_w"], "odds2": t["avg_l"]})
    board = an.tennis_board(odds, con, {"s": {"config": {"sport": "tennis", "selection": ["favourite", "underdog"]}, "metrics": {}}})
    assert len(board) == 8 and (board["segnali"] == "s").all()
    assert np.allclose(board["modello 1 %"], bt.elo_win_prob(t["winner_elo"], t["loser_elo"]) * 100)
