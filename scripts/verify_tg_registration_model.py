"""Deterministic sequential design model. No Telegram/Emby/DB/network calls.

Checks selected admission, queue and sizing invariants; not a production scheduler,
atomic concurrency test, RSS measurement, password hasher or HTTP rate middleware.
Requires local PyYAML to read the actual example design values.
"""
from collections import Counter, defaultdict, deque
from pathlib import Path
import json
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
GUARD = yaml.safe_load((ROOT / 'config/telegram-guard.example.yaml').read_text(encoding='utf-8'))['telegram_guard']
QUEUE = yaml.safe_load((ROOT / 'config/registration-queue.example.yaml').read_text(encoding='utf-8'))['registration_queue']
SIZING = yaml.safe_load((ROOT / 'config/capacity-profiles.example.yaml').read_text(encoding='utf-8'))['telegram_workload']


class ClickModel:
    def __init__(self):
        self.raw = defaultdict(deque)
        self.cached = {}
        self.semantic_writes = set()

    def click(self, uid, now, business_id='checkin-day', authorized=True, callback_id=None):
        # callback_id is intentionally not a bucket/idempotency identity.
        timestamps = self.raw[uid]
        while timestamps and timestamps[0] <= now - 60:
            timestamps.popleft()
        if len(timestamps) >= GUARD['rate_limits']['raw_uid_actions_60s'] or sum(t > now - 10 for t in timestamps) >= GUARD['rate_limits']['raw_uid_actions_10s']:
            return 'RAW_LIMIT'
        timestamps.append(now)
        if not authorized:
            return 'DENIED_BEFORE_CACHE'
        key = uid, business_id
        if key in self.cached and now < self.cached[key] + GUARD['rate_limits']['same_action_object_coalesce_seconds']:
            return 'COALESCED'
        self.cached[key] = now
        if key in self.semantic_writes:
            return 'EXISTING_BUSINESS_RESULT'
        self.semantic_writes.add(key)
        return 'ONE_NEW_BUSINESS_WRITE'


class QueueModel:
    def __init__(self, member_limit=100, daily_limit=100, capacity=None, workers=1, members=0, paid_reserved=0):
        self.member_limit, self.daily_limit = member_limit, daily_limit
        self.capacity = capacity or QUEUE['admission']['outstanding_limit']
        self.workers, self.members, self.paid_reserved = workers, members, paid_reserved
        self.rows, self.order, self.batch_rotation = {}, [], deque()
        self.daily_success = 0
        self.active_peak = 0
        self.resident_reference_peak = 0
        self.pressure_paused = False

    def outstanding(self):
        return sum(row['status'] not in ('DONE', 'CANCELLED') for row in self.rows.values())

    def enqueue(self, uid, batch='A', paid=False):
        if uid in self.rows:
            return 'ORIGINAL_TICKET' if self.rows[uid]['status'] != 'DONE' else 'ALREADY_MEMBER'
        if self.outstanding() >= self.capacity:
            return 'QUEUE_FULL'
        self.rows[uid] = {'batch': batch, 'paid': paid, 'status': 'QUEUED'}
        self.order.append(uid)
        if batch not in self.batch_rotation:
            self.batch_rotation.append(batch)
        return 'QUEUED'

    def claim(self):
        active = sum(row['status'] == 'LEASED' for row in self.rows.values())
        if self.pressure_paused or active >= self.workers:
            return None
        # This intentionally models decisions with a tiny in-memory data set.
        # Production uses indexed durable queries, not scanning all rows here.
        for _ in range(len(self.batch_rotation)):
            batch = self.batch_rotation[0]
            self.batch_rotation.rotate(-1)
            for uid in self.order:
                row = self.rows[uid]
                if row['batch'] != batch or row['status'] not in ('QUEUED', 'WAITING_POLICY', 'WAITING_CAPACITY'):
                    continue
                held_daily = sum(r['status'] in ('LEASED', 'RECONCILING') for r in self.rows.values())
                if self.daily_success + held_daily >= self.daily_limit:
                    row['status'] = 'WAITING_POLICY'
                    continue
                normal_held = sum(r['status'] in ('LEASED', 'RECONCILING') and not r['paid'] for r in self.rows.values())
                if not row['paid'] and self.members + self.paid_reserved + normal_held >= self.member_limit:
                    row['status'] = 'WAITING_CAPACITY'
                    continue
                if row['paid'] and self.paid_reserved <= 0:
                    row['status'] = 'WAITING_CAPACITY'
                    continue
                row['status'] = 'LEASED'
                active += 1
                self.active_peak = max(self.active_peak, active)
                pending = sum(r['status'] == 'QUEUED' for r in self.rows.values())
                resident = active + min(pending, QUEUE['dispatch']['initial_prefetch_ticket_refs'])
                self.resident_reference_peak = max(self.resident_reference_peak, resident)
                return uid
        return None

    def finish(self, uid, uncertain=False):
        row = self.rows[uid]
        assert row['status'] == 'LEASED'
        if uncertain:
            row['status'] = 'RECONCILING'
            return
        row['status'] = 'DONE'
        self.members += 1
        self.daily_success += 1
        if row['paid']:
            self.paid_reserved -= 1


def sizing_decision(mode, host_mib, container_mib, current_cache=16, owner_ceiling=64, evidence=True):
    effective = min(host_mib, container_mib)
    profile = 'XL' if effective >= 16384 else 'L' if effective >= 8192 else 'M' if effective >= 4096 else 'S'
    suggested = SIZING['profiles'][profile]['state_cache_mib']
    applied = current_cache
    if mode == 'AUTO_BOUNDED' and evidence:
        step = int(current_cache * (1 + SIZING['auto_bounded']['growth_step_percent_maximum'] / 100))
        applied = min(suggested, owner_ceiling, step)
    return {'effective_mib': effective, 'suggested_cache_mib': suggested, 'applied_cache_mib': applied,
            'workers': QUEUE['dispatch']['initial_provisioning_workers'],
            'raw_actions_10s': GUARD['rate_limits']['raw_uid_actions_10s']}


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    checks = []

    def check(condition, name):
        assert condition, name
        checks.append(name)

    clicks = ClickModel()
    burst = Counter(clicks.click('U1', 0, callback_id=f'forged-changing-{i}') for i in range(100))
    check(burst == {'ONE_NEW_BUSINESS_WRITE': 1, 'COALESCED': 19, 'RAW_LIMIT': 80}, '100 same-time clicks and changing callback IDs use one identity and one business write')
    check(clicks.click('U1', 11, authorized=False) == 'DENIED_BEFORE_CACHE', 'authorization checked before cache even after time advances')
    check(clicks.click('U1', 12) == 'EXISTING_BUSINESS_RESULT', 'after short coalescing window semantic retry remains idempotent')
    check(ClickModel().click('U2', 0) == 'ONE_NEW_BUSINESS_WRITE', 'another identity does not inherit user one bucket')

    q = QueueModel()
    check(all(q.enqueue(f'U{i}') == 'QUEUED' for i in range(100)), '100 eligible users fit the explicit durable queue design cap')
    check(q.enqueue('U0') == 'ORIGINAL_TICKET' and q.outstanding() == 100, 'duplicate enrollment returns the same intent without additional capacity')
    completed = 0
    while (uid := q.claim()) is not None:
        check(q.claim() is None, f'single worker denies parallel claim at completion {completed}')
        q.finish(uid)
        completed += 1
    check(completed == 100 and q.members == 100 and q.daily_success == 100, 'approved 100 member and daily policy drains 100 once')
    check(q.active_peak == 1 and q.resident_reference_peak <= 3, 'single active operation and at most two prefetched references')
    check(q.enqueue('U0') == 'ALREADY_MEMBER', 'completed registration cannot submit a second account')

    default = QueueModel(member_limit=20, daily_limit=3)
    for i in range(100):
        default.enqueue(f'D{i}')
    while (uid := default.claim()) is not None:
        default.finish(uid)
    check(default.members == 3 and default.daily_success == 3 and default.outstanding() == 97, 'default 20 members and 3 daily never silently raised by 100 invitations')

    bounded = QueueModel(member_limit=1000, daily_limit=1000)
    for i in range(256):
        bounded.enqueue(f'B{i}')
    active = bounded.claim()
    check(bounded.enqueue('overflow') == 'QUEUE_FULL' and bounded.outstanding() == 256, 'leased intent still counts in full queue cap')
    check(bounded.enqueue(active) == 'ORIGINAL_TICKET', 'existing ticket remains readable at queue capacity')
    bounded.finish(active)
    check(bounded.enqueue('overflow') == 'QUEUED' and bounded.outstanding() == 256, 'one completion releases exactly one queue slot')

    fair = QueueModel()
    for i in range(10):
        fair.enqueue(f'A{i}', batch='large')
    for i in range(3):
        fair.enqueue(f'S{i}', batch='small')
    order = []
    for _ in range(6):
        uid = fair.claim()
        order.append(uid)
        fair.finish(uid)
    check(order == ['A0', 'S0', 'A1', 'S1', 'A2', 'S2'], 'round robin across ready batches and FIFO within a batch')

    paid = QueueModel(member_limit=100, members=90, paid_reserved=10)
    paid.enqueue('free')
    paid.enqueue('paid', batch='paid', paid=True)
    check(paid.claim() == 'paid' and paid.rows['free']['status'] == 'WAITING_CAPACITY', 'free enrollment cannot steal sold reserved capacity')
    paid.finish('paid')
    check(paid.members + paid.paid_reserved == 100, 'paid claim converts one pre-existing seat without double reservation')

    unknown = QueueModel()
    unknown.enqueue('uncertain')
    unknown.finish(unknown.claim(), uncertain=True)
    check(unknown.enqueue('uncertain') == 'ORIGINAL_TICKET' and unknown.claim() is None, 'unknown external create outcome is not blindly retried')
    check(unknown.outstanding() == 1, 'reconciling outcome retains outstanding slot and reservation')
    pressure = QueueModel()
    pressure.enqueue('wait')
    pressure.pressure_paused = True
    check(pressure.claim() is None and pressure.outstanding() == 1, 'memory pressure pauses new work and preserves admitted ticket')
    pressure.pressure_paused = False
    check(pressure.claim() == 'wait', 'recovery resumes original intent')

    manual = sizing_decision('MANUAL', 8192, 8192)
    recommend = sizing_decision('RECOMMEND_MANUAL_APPLY', 8192, 8192)
    automatic = sizing_decision('AUTO_BOUNDED', 8192, 8192)
    check(manual['applied_cache_mib'] == recommend['applied_cache_mib'] == 16 and recommend['suggested_cache_mib'] == 64, 'manual and recommend modes do not apply RAM upgrade automatically')
    check(automatic['applied_cache_mib'] == 20 and automatic['raw_actions_10s'] == 20 and automatic['workers'] == 1, 'bounded auto growth uses at most 25 percent without raising security or provisioning concurrency')
    check(sizing_decision('AUTO_BOUNDED', 8192, 2048)['applied_cache_mib'] == 16, 'host upgrade cannot bypass unchanged container memory cap')
    check(sizing_decision('AUTO_BOUNDED', 8192, 8192, owner_ceiling=18)['applied_cache_mib'] == 18, 'owner maximum constrains auto growth')
    check(sizing_decision('AUTO_BOUNDED', 8192, 8192, evidence=False)['applied_cache_mib'] == 16, 'missing resource evidence blocks growth')

    report = {'date': '2026-11-19', 'version': '7.1', 'scope': 'SEQUENTIAL_DESIGN_MODEL_NOT_PRODUCTION_CONCURRENCY_OR_MEMORY_TEST',
              'checks_passed': len(checks), 'checks': checks, 'same_time_100_clicks': dict(burst),
              'approved_100_registration': {'completed': completed, 'active_peak': q.active_peak, 'prefetched_reference_ceiling': 2,
                                              'max_modeled_resident_ticket_refs': q.resident_reference_peak,
                                              'hypothetical_seconds_if_two_seconds_per_user': completed * 2},
              'unchanged_default_100_invitations': {'first_day_success': default.daily_success, 'remaining': default.outstanding()},
              'fair_first_six': order, '8gb_auto_growth_one_step': automatic,
              'not_tested': ['real authentication and nonce verification', 'atomic threads/processes/DB uniqueness or locks',
                             'Telegram delivery and API methods', 'actual Emby account creation/timeout recovery', 'password hashing',
                             'RSS/cgroup memory', 'full batch fairness/priority under concurrency', 'cache eviction and distributed limiter recovery',
                             'hysteresis timing or real pressure metrics']}
    (ROOT / 'analysis/tg-registration-model.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k not in ('checks', 'not_tested')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
