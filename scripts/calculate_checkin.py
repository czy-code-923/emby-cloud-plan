"""Deterministic renewal estimates for positive, uniform integer check-in rewards."""
from __future__ import annotations

import argparse
from collections import defaultdict
from fractions import Fraction
import json
from pathlib import Path


def calculate(low: int, high: int, prices: list[int], days: int) -> dict:
    if not 1 <= low <= high <= 1000 or not prices or min(prices) <= 0:
        raise ValueError("Rewards and prices must be positive; max reward is 1000.")
    if max(prices) > 100000 or not 1 <= days <= 366:
        raise ValueError("Price or observation period exceeds the calculation budget.")
    count = high - low + 1
    estimated_steps = count * (max(prices) + days + (high - low) * days * (days - 1) // 2)
    if estimated_steps > 20_000_000:
        raise ValueError("Requested exact calculation exceeds the operation budget.")
    expectation = [0.0] * (max(prices) + 1)
    for remaining in range(1, len(expectation)):
        expectation[remaining] = 1 + sum(
            expectation[max(0, remaining - reward)]
            for reward in range(low, high + 1)
        ) / count
    distribution = {0: 1}
    rows = []
    for day in range(1, days + 1):
        updated: dict[int, int] = defaultdict(int)
        for total, ways in distribution.items():
            for reward in range(low, high + 1):
                updated[total + reward] += ways
        distribution = updated
        denominator = count ** day
        probabilities = {}
        for price in prices:
            numerator = sum(ways for total, ways in distribution.items() if total >= price)
            probability = Fraction(numerator, denominator)
            probabilities[str(price)] = {
                "probability": float(probability),
                "exact_numerator": str(probability.numerator),
                "exact_denominator": str(probability.denominator),
            }
        rows.append({"checkin_days": day, "expected_points": day * (low + high) / 2,
                     "minimum_points": day * low, "maximum_points": day * high,
                     "at_least_price": probabilities})
    return {
        "model": "independent_discrete_uniform_no_bonus_no_spending_zero_start",
        "reward_min": low, "reward_max": high, "mean_daily_reward": (low + high) / 2,
        "renewals": [{"price": price, "expected_first_passage_days": expectation[price],
                      "earliest_days": (price + high - 1) // high,
                      "guaranteed_days": (price + low - 1) // low} for price in prices],
        "daily_distribution_summary": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min", dest="low", type=int, default=10)
    parser.add_argument("--max", dest="high", type=int, default=20)
    parser.add_argument("--prices", type=int, nargs="+", default=[300, 850, 3000])
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = calculate(args.low, args.high, args.prices, args.days)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8", newline="\n")
        print("Report written; expected first-passage days:")
        print(json.dumps(result["renewals"], ensure_ascii=True))
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
