"""Platform adapter to the existing audited portfolio engine (synthetic inputs)."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta

from research.simulation.agents import AgentParameters
from research.simulation.portfolio_market import initial_portfolio, simulate_portfolio
from research.simulation.portfolio_audit import audit_portfolio_day
from .strategy_config import defaults, load_model, parameters_digest, validate_parameters
from .experiment_scenario import DEFAULT_ASSUMPTIONS, assumptions_digest, validate_assumptions


def run_market(config: dict) -> dict:
    """Run matched controls; audit every day before returning either result."""
    model, model_hash = load_model()
    if config.get('mechanism_config_sha256', model_hash) != model_hash:
        raise ValueError('基础撮合配置已变化，请创建新实验以使用当前配置')
    parameters = validate_parameters(config.get('strategy_parameters', defaults(model)), model)
    parameter_hash = parameters_digest(parameters)
    if config.get('strategy_parameters_sha256', parameter_hash) != parameter_hash:
        raise ValueError('实验的策略参数快照已改变')
    assumptions = validate_assumptions(config.get('market_assumptions', DEFAULT_ASSUMPTIONS))
    if config.get('market_assumptions_sha256', assumptions_digest(assumptions)) != assumptions_digest(assumptions):
        raise ValueError('实验的市场假设快照已改变')
    # Existing engine has four accounts per role and three initial asset wallets.
    # Round down to cents explicitly and report the effective role cash.
    per_wallet_minor = int(round(config['cash'] * 100)) // 12
    agents = [AgentParameters(**{**p, **parameters[p['role']], 'initial_cash': per_wallet_minor / 100})
              for p in model['agent_parameters']]
    core = {**model['core'], 'seed': config['seed']}
    background = {**model['background'], 'seed': config['seed']}
    feedback = {**model['feedback'], 'volatility_floor': assumptions['volatility']}
    venue, case = model['venue'], model['portfolio_case']
    joined = {a: [] for a in ('A', 'B', 'C')}
    for i in range(config['sessions']):
        day = date(2000, 1, 1) + timedelta(days=i)
        for asset in joined:
            active = (assumptions['scope'] == 'public' or asset == 'A') and 4 <= i < 4 + config['duration']
            joined[asset].append({
                'signal_cutoff_date': day.isoformat(),
                'execution_reference_date': (day + timedelta(days=1)).isoformat(),
                'trade_date': (day + timedelta(days=2)).isoformat(),
                'execution_available': True, 'observed_return': 0.,
                'market_signal': 0., 'estimated_volatility': .01,
                'text_signal': config['signal'] if active else 0.,
                'text_uncertainty': config['uncertainty'] if active else 0.,
                'text_evidence': 'platform:user-assumed-scenario',
            })
    paths = {}
    shock_options = {}
    if assumptions['market'] != 0:
        shock_options['scenario_shocks'] = {
            'scenario_id': 'platform-assumed-common-market',
            'common': [assumptions['market']] * config['sessions'],
            'asset_specific': {a: [0.] * config['sessions'] for a in joined}}
    for label, enabled in (('baseline', False), ('with_message', True)):
        result = simulate_portfolio(joined, agents, core, background, venue, feedback, case, enabled, **shock_options)
        _, accounts, _, _ = initial_portfolio(agents, core, background, list(joined), case, venue)
        previous = {'accounts': {n: asdict(a) for n, a in accounts.items()},
                    'prices': dict.fromkeys(joined, venue['price_start_minor']), 'fee_pool_minor': 0,
                    'initial_cash_minor': sum(sum(a.wallets.values()) for a in accounts.values()),
                    'initial_shares': {s: sum(a.shares[s] for a in accounts.values()) for s in joined}}
        for step, row in enumerate(result['trace']):
            previous = audit_portfolio_day(row, previous, venue, step, dense=step == 0)
        paths[label] = result
    result = {'mode': 'synthetic_market', 'status': 'completed', 'paths': paths,
            'audit': {'passed': True, 'days_checked': config['sessions'] * 2},
            'assumptions': {'message_first_step': 5, 'exposed_asset': 'A',
                            'dates_are_ordering_only': True, 'llm_used': False,
                            'effective_initial_cash_per_role': per_wallet_minor * 12 / 100,
                            'additional_initial_shares_per_account_per_asset': 500,
                            'accounts_per_role': 4},
            'mechanism_config_sha256': model_hash,
            'strategy_parameters': parameters, 'strategy_parameters_sha256': parameter_hash,
            'effective_agent_parameters': [asdict(p) for p in agents]}
    if 'market_assumptions' in config:
        result['market_assumptions'] = assumptions
        result['market_assumptions_sha256'] = assumptions_digest(assumptions)
        result['assumptions'].update(
            exposed_asset='A' if assumptions['scope'] == 'A' else 'A/B/C',
            common_market_offset=assumptions['market'], volatility_floor=assumptions['volatility'],
            market_offset_first_step=1, memory_initial_state={'sign': 0, 'streak': 0},
            volatility_is_floor=True, price_feedback_enabled=True)
    return result
