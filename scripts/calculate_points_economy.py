"""Reproducible raw checkin/retention scenarios; no external service calls."""
from pathlib import Path
import json
import sys
import yaml
from calculate_checkin import calculate

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    points = yaml.safe_load((ROOT/'config/policy.example.yaml').read_text(encoding='utf-8'))['points']
    rows = []
    for name, profile in points['economy']['presets'].items():
        low, high = profile['daily_raw_range']
        price = profile['activity_A_price']
        result = calculate(low, high, [price], 31)
        selected = [r for r in result['daily_distribution_summary'] if r['checkin_days'] in [20, 22, 29, 30, 31]]
        probabilities = []
        for r in selected:
            probability = r['at_least_price'][str(price)]
            guaranteed = r['checkin_days'] * low >= price
            rounded = f"{100 * probability['probability']:.4f}%"
            if not guaranteed and rounded == '100.0000%':
                rounded = '接近100%，仍非保证'
            probabilities.append({'count': r['checkin_days'], 'probability_float': probability['probability'],
                                  'exact_numerator': probability['exact_numerator'], 'exact_denominator': probability['exact_denominator'],
                                  'guaranteed_from_minimum_award': guaranteed, 'display': rounded})
        rows.append({'preset': name, 'effective_by_default': profile['enabled_as_effective_default'],
                     'raw_mean_daily': (low+high)/2, 'A_and_month_prices': [price,profile['exempt_month_price']],
                     'quarter_year_prices': [profile['exempt_quarter_price'],profile['exempt_year_price']],
                     'reward_soft_cap': profile['reward_soft_cap'], 'expected_raw_30_awards': 30*(low+high)/2,
                     'expected_net_after_one_A_no_other_flows': 30*(low+high)/2-price,
                     'first_passage_raw_award_model': {'expected_successful_award_count': result['renewals'][0]['expected_first_passage_days'],
                                                        'earliest_count': result['renewals'][0]['earliest_days'],
                                                        'guaranteed_count_under_raw_baseline': result['renewals'][0]['guaranteed_days']},
                     'probabilities_by_real_award_count': probabilities})
    catalog = yaml.safe_load((ROOT/'config/catalog.example.yaml').read_text(encoding='utf-8'))
    enabled_prices = [p['price']['amount'] for p in catalog['products'] if p.get('enabled') and p.get('price',{}).get('unit') == 'POINTS']
    assert points['reward_soft_cap']['points'] >= max(enabled_prices)
    assert all(p['reward_soft_cap'] >= p['exempt_year_price'] for p in points['economy']['presets'].values())
    report = {'date': '2026-11-19', 'version': '8.1',
              'scope': 'RAW_REWARD_COUNT_BASELINE_NOT_CALENDAR_DAY_OR_REVENUE_PROMISE',
              'assumptions': ['zero start', 'independent uniform raw awards', 'no spend before threshold calculation',
                              'no freeze cap or bonus affecting these raw distributions', '30 counts need not fit an arbitrary 30-day window'],
              'actual_cap_examples': [{'available':3595,'frozen':0,'raw':16,'actual':5}, {'available':3600,'frozen':0,'raw':16,'actual':0}],
              'presets': rows, 'B_remains_free_and_does_not_spend_points': True,
              'single_user_cap_does_not_bound_total_supply_with_new_users_or_nonreward_credits': True}
    assert not rows[0]['probabilities_by_real_award_count'][2]['guaranteed_from_minimum_award']
    assert rows[0]['probabilities_by_real_award_count'][3]['guaranteed_from_minimum_award']
    (ROOT/'analysis/points-economy.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps(rows,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
