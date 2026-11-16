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
