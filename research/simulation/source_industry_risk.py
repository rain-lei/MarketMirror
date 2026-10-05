"""Past-window market/industry covariance diagnostics; never an agent signal."""
from __future__ import annotations
from collections import defaultdict
from fractions import Fraction
import hashlib
import math
import statistics

NAMES = ("source", "market_independent_residual", "independent_industries", "joint_industries")

def qcov(a, b):
    if len(a) != len(b) or len(a) < 2:
        raise ValueError("aligned covariance history required")
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    return sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True)) / (n - 1)

def fcov(a, b):
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    return math.fsum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True)) / (len(a) - 1)

def pack(value):
    return None if value is None else [value.numerator, value.denominator]

def permuted_membership(membership, seed):
    if type(seed) is not str or not seed or any(type(k) is not str or type(v) is not str or not v for k, v in membership.items()):
        raise ValueError("explicit seed and complete string membership required")
    order = sorted(membership, key=lambda s: hashlib.sha256(f"source-sector-permutation-v1:{seed}:{s}".encode()).hexdigest())
    labels = sorted(membership.values())
    return dict(zip(order, labels, strict=True))

def decompose(index_returns, stock_returns, membership):
    stocks, n = sorted(stock_returns), len(index_returns)
    if (n < 3 or len(stocks) < 2 or set(membership) != set(stocks)
        or any(type(s) is not str or not s for s in stocks)
        or any(type(g) is not str or not g for g in membership.values())
        or any(len(v) != n for v in stock_returns.values())
        or any(type(v) not in (int, float) or not math.isfinite(v) for a in [index_returns, *stock_returns.values()] for v in a)):
        raise ValueError("complete aligned finite market/company histories and industry labels required")
    x = [Fraction(str(v)) for v in index_returns]
    x = [v - sum(x) / n for v in x]
    vm = qcov(x, x)
    if vm <= 0:
        raise ValueError("undefined market exposure; zero benchmark variance")
    groups, e, beta, vy, ve = defaultdict(list), {}, {}, {}, {}
    for s in stocks:
        y = [Fraction(str(v)) for v in stock_returns[s]]
        y = [v - sum(y) / n for v in y]
        beta[s] = qcov(x, y) / vm
        e[s] = [b - beta[s] * a for a, b in zip(x, y, strict=True)]
        vy[s], ve[s] = qcov(y, y), qcov(e[s], e[s])
        if sum(e[s]) or qcov(x, e[s]) or vy[s] != beta[s] ** 2 * vm + ve[s]:
            raise ValueError("exact market variance/orthogonality failed")
        groups[membership[s]].append(s)
    factors, vg, gamma, u, vu, loo, status = {}, {}, {}, {}, {}, {}, {}
    for g, members in sorted(groups.items()):
        if len(members) >= 2:
            factors[g] = [sum(e[s][t] for s in members) / len(members) for t in range(n)]
            vg[g] = qcov(factors[g], factors[g])
        usable = len(members) >= 2 and vg[g] > 0
        status[g] = {"members": members, "factor_active": usable,
            "reason": "identified_past_projection" if usable else "singleton_no_peer" if len(members) == 1 else "zero_sector_factor_variance",
            "factor_variance_fraction": pack(vg.get(g)), "mean_factor_includes_own_company": len(members) >= 2}
        for s in members:
            gamma[s] = qcov(factors[g], e[s]) / vg[g] if usable else None
            u[s] = [a - gamma[s] * b for a, b in zip(e[s], factors[g], strict=True)] if usable else list(e[s])
            vu[s] = qcov(u[s], u[s])
            if vu[s] < 0 or sum(u[s]) or qcov(x, u[s]):
                raise ValueError("exact company remainder failed")
            if usable and (qcov(factors[g], u[s]) or ve[s] != gamma[s] ** 2 * vg[g] + vu[s]):
                raise ValueError("exact industry variance/orthogonality failed")
            if len(members) >= 2:
                peer = [(sum(e[k][t] for k in members) - e[s][t]) / (len(members) - 1) for t in range(n)]
                vp = qcov(peer, peer)
                gp = qcov(peer, e[s]) / vp if vp else None
                rp = gp ** 2 * vp / ve[s] if gp is not None and ve[s] > 0 else None
                loo[s] = {"peer_variance_fraction": pack(vp), "gamma_fraction": pack(gp), "r_squared": None if rp is None else float(rp)}
            else:
                loo[s] = {"peer_variance_fraction": None, "gamma_fraction": None, "r_squared": None}
        if usable and (sum(gamma[s] for s in members) != len(members) or any(sum(u[s][t] for s in members) for t in range(n))):
            raise ValueError("sector self-inclusion cancellation identity failed")
    active_groups = sorted(g for g, row in status.items() if row["factor_active"])
    fc = {g: [float(v) for v in factors[g]] for g in active_groups}
    sector_cov = {(a, b): float(vg[a]) if a == b else fcov(fc[a], fc[b]) for a in active_groups for b in active_groups}
    size = len(stocks)
    matrices = {k: [[0.0] * size for _ in stocks] for k in NAMES}
    for i, a in enumerate(stocks):
        for j in range(i, size):
            b, ga, gb = stocks[j], membership[a], membership[stocks[j]]
            market = float(beta[a]) * float(beta[b]) * float(vm)
            industry = float(gamma[a]) * float(gamma[b]) * sector_cov[ga, gb] if gamma[a] is not None and gamma[b] is not None else 0.0
            values = {"source": fcov(stock_returns[a], stock_returns[b]),
                "market_independent_residual": market + (float(ve[a]) if i == j else 0.0),
                "independent_industries": market + (industry if ga == gb else 0.0) + (float(vu[a]) if i == j else 0.0),
                "joint_industries": market + industry + (float(vu[a]) if i == j else 0.0)}
            for name, value in values.items():
                if not math.isfinite(value):
                    raise ValueError("nonfinite covariance structure")
                matrices[name][i][j] = matrices[name][j][i] = value
    eligible = [s for s in stocks if status[membership[s]]["factor_active"]]
    identity = None
    if len(eligible) >= 2:
        pos = [stocks.index(s) for s in eligible]
        actual = math.fsum(matrices["source"][i][j] for i in pos for j in pos) / len(pos) ** 2
        fitted = math.fsum(matrices["joint_industries"][i][j] for i in pos for j in pos) / len(pos) ** 2
        extra = float(sum(vu[s] for s in eligible) / len(eligible) ** 2)
        if not math.isclose(fitted - actual, extra, abs_tol=1e-14, rel_tol=1e-10):
            raise ValueError("joint sector portfolio self-inclusion identity failed")
        identity = {"companies": len(pos), "source_variance": actual, "joint_industries_variance": fitted,
            "extra_independent_company_variance": extra, "joint_minus_source": fitted - actual}
    parameters = {}
    for s in stocks:
        g = membership[s]
        rs = gamma[s] ** 2 * vg[g] / ve[s] if gamma[s] is not None and ve[s] else None
        parameters[s] = {"sector": g, "market_beta_fraction": pack(beta[s]), "stock_variance_fraction": pack(vy[s]),
            "market_residual_variance_fraction": pack(ve[s]), "industry_gamma_fraction": pack(gamma[s]),
            "company_remainder_variance_fraction": pack(vu[s]), "inclusive_industry_r_squared": None if rs is None else float(rs),
            "leave_one_out": loo[s], "inactive_factor_keeps_original_market_residual": gamma[s] is None}
    return {"stock_parameters": parameters, "sector_status": status, "sector_portfolio_self_inclusion_identity": identity,
        "active_industry_companies": len(eligible), "unknown_industry_exposures": sum(gamma[s] is None for s in stocks),
        "exact_market_variance_identities": size, "exact_industry_variance_identities": len(eligible)}, matrices

def matrix_error(source, candidate):
    errors, magnitudes = [], []
    for i in range(len(source)):
        for j in range(i + 1, len(source)):
            errors.append((candidate[i][j] - source[i][j]) ** 2)
            magnitudes.append(source[i][j] ** 2)
    rmse, scale = math.sqrt(statistics.fmean(errors)), math.sqrt(statistics.fmean(magnitudes))
    return {"off_diagonal_covariance_rmse": rmse, "relative_off_diagonal_covariance_rmse": rmse / scale if scale else None}

def verify_numpy(index_returns, stock_returns, membership, result, matrices):
    import numpy as np
    stocks = sorted(stock_returns)
    x = np.asarray(index_returns)
    y = np.column_stack([stock_returns[s] for s in stocks])
    market_design = np.column_stack([np.ones(len(x)), x])
    mb = np.linalg.lstsq(market_design, y, rcond=None)[0]
    residuals = y - market_design @ mb
    groups = {g: [i for i, s in enumerate(stocks) if membership[s] == g] for g in sorted(set(membership.values()))}
    loadings, factors, remainder, fitted = [], [], residuals.copy(), {}
    loo_checks = 0
    for g, idx in groups.items():
        f = residuals[:, idx].mean(axis=1)
        # Numerically zero projections have undefined exposure, never coefficient 0.
        usable = len(idx) >= 2 and np.var(f, ddof=1) > 1e-26
        if usable != result["sector_status"][g]["factor_active"]:
            raise ValueError("independent sector availability differs")
        if usable:
            design = np.column_stack([np.ones(len(x)), f])
            fitted[g] = np.linalg.lstsq(design, residuals[:, idx], rcond=None)[0]
            remainder[:, idx] = residuals[:, idx] - design @ fitted[g]
            loading = np.zeros(len(stocks))
            loading[idx] = fitted[g][1]
            factors.append(f)
            loadings.append(loading)
            for pos, i in enumerate(idx):
                expected = float(Fraction(*result["stock_parameters"][stocks[i]]["industry_gamma_fraction"]))
                if not math.isclose(expected, float(fitted[g][1, pos]), rel_tol=1e-10, abs_tol=1e-14):
                    raise ValueError("independent industry exposure differs")
        for i in idx:
            saved = result["stock_parameters"][stocks[i]]["leave_one_out"]
            peer = (residuals[:, idx].sum(axis=1) - residuals[:, i]) / (len(idx) - 1) if len(idx) >= 2 else None
            available = peer is not None and np.var(peer, ddof=1) > 1e-26
            if available != (saved["gamma_fraction"] is not None):
                raise ValueError("independent leave-one-out availability differs")
            if available:
                gp = np.linalg.lstsq(np.column_stack([np.ones(len(x)), peer]), residuals[:, i], rcond=None)[0][1]
                if not math.isclose(float(Fraction(*saved["gamma_fraction"])), float(gp), rel_tol=1e-10, abs_tol=1e-14):
                    raise ValueError("independent leave-one-out exposure differs")
            loo_checks += 1
    market = np.outer(mb[1], mb[1]) * np.var(x, ddof=1)
    source = np.cov(y, rowvar=False, ddof=1)
    rcov = np.cov(residuals, rowvar=False, ddof=1)
    ucov = np.cov(remainder, rowvar=False, ddof=1)
    if factors:
        f = np.column_stack(factors)
        cf = np.atleast_2d(np.cov(f, rowvar=False, ddof=1))
        b = np.column_stack(loadings)
        sector_independent = b @ np.diag(np.diag(cf)) @ b.T
        sector_joint = b @ cf @ b.T
    else:
        sector_independent = sector_joint = np.zeros_like(source)
    expected = {"source": source, "market_independent_residual": market + np.diag(np.diag(rcov)),
        "independent_industries": market + sector_independent + np.diag(np.diag(ucov)),
        "joint_industries": market + sector_joint + np.diag(np.diag(ucov))}
    eigenvalues = {}
    for name in NAMES:
        if not np.allclose(expected[name], matrices[name], atol=1e-14, rtol=1e-10):
            raise ValueError("independent full industry covariance matrix differs: " + name)
        if not np.allclose(np.diag(expected[name]), np.diag(source), atol=1e-14, rtol=1e-10):
            raise ValueError("industry comparison changed marginal source variance")
        value = float(np.linalg.eigvalsh(expected[name])[0])
        if value < -1e-14:
            raise ValueError("covariance structure is not positive semidefinite")
        eigenvalues[name] = value
    return {"independent_matrix_positions": 4 * len(stocks) ** 2, "leave_one_out_positions": loo_checks,
        "minimum_eigenvalues": eigenvalues, "numpy_zero_variance_threshold": 1e-26}
