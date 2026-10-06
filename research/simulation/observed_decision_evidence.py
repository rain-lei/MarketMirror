"""Source explanations for saved observed decisions; never rewrite the archive."""
from __future__ import annotations

import math


def explain_path(plan, record, mode, path):
    parameters = {p['name']: p for p in plan['agents']}
    prior = {name: (p['initial_cash'], 0.0) for name, p in parameters.items()}
    output = []
    for day in path['trace']:
        rows = {}
        for name, row in day['agents'].items():
            p, decision = parameters[name], row['decision']
            market = p['market_sensitivity'] * day['market_signal']
            direction = p['text_sensitivity'] * day['text_signal'] if mode != 'no_text' else 0.0
            uncertainty = -p['uncertainty_aversion'] * day['text_uncertainty']
            score = market + direction + uncertainty
            previous_cash, previous_units = prior[name]
            open_wealth = previous_cash + previous_units * day['price_index_before']
            current_weight = previous_units * day['price_index_before'] / open_wealth
            if (not math.isclose(decision['confidence'], min(1.0, abs(score)), abs_tol=1e-12)
                    or not math.isclose(decision['current_weight'], current_weight, abs_tol=1e-12)):
                raise ValueError('解释输入与归档决策不一致')
            text_used = direction != 0 or uncertainty != 0
            evidence = []
            if text_used:
                expected = f"source:{plan['case']['case_text_sha256']}:cutoff:{day['signal_cutoff_date']}"
                if not day['source_active'] or day['text_evidence'] != expected:
                    raise ValueError('文本作用时钟或来源标识不一致')
                if mode == 'reviewed_llm':
                    for i in day['source_link']['used_fact_indices']:
                        fact = record['facts'][i]
                        mapping = plan['mapping']['fact_mapping'][fact['kind']]
                        if (direction and mapping['signal'] or uncertainty and mapping['uncertainty']):
                            evidence += [{**span, 'fact_index': i} for span in fact['evidence']]
                elif mode == 'keywords':
                    rules = {r['token']:r for r in plan['mapping']['keyword_rules']}
                    for token in day['source_link']['keyword_matches']:
                        rule = rules[token]
                        if not (direction and rule['signal'] or uncertainty and rule['uncertainty']):
                            continue
                        for segment in plan['case']['segments']:
                            if segment['source'] != 'reply':
                                continue
                            start = segment['text'].find(token)
                            if start >= 0:
                                evidence.append({'source':'reply','quote':token,'start':start,
                                                 'end':start+len(token),'keyword':token})
                if not evidence:
                    raise ValueError('实际使用的文本缺少可定位依据')
            rows[name] = {'role':p['role'],'market_contribution':market,
                'text_direction_contribution':direction,'uncertainty_contribution':uncertainty,
                'combined_score':score,'unconstrained_target_weight':p['base_weight']+0.4*score,
                'current_weight_before_order':current_weight,'text_evidence_used':evidence,
                'text_evidence_identifier':day['text_evidence'] if text_used else None,
                'explanation_basis':'archived_inputs_and_registered_parameters',
                'original_decision_evidence':list(decision['evidence'])}
            prior[name] = row['cash'], row['shares']
        output.append({'trade_date':day['trade_date'],'agents':rows})
    return output
