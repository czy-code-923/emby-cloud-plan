"""v8 deterministic retention state model; no real DB, Telegram or Emby calls.

Checks rule interactions and sequential orderings, not authentication, collection
accuracy, distributed locks, real concurrency or a production entitlement engine.
"""
from dataclasses import dataclass, field
from pathlib import Path
import json
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
RULE = yaml.safe_load((ROOT / 'config/retention.example.yaml').read_text(encoding='utf-8'))['retention']
DAY = 86400
SPAN = RULE['window']['duration_days'] * DAY
COST = RULE['checkin_A']['automatic_debit_points']
COOLDOWN = RULE['modes']['actual_change_cooldown_days'] * DAY


@dataclass
class Member:
    end: int = 30 * DAY
    balance: int = 0
    frozen: int = 0
    mode: str | None = 'CHECKIN_POINTS'
    participating: bool = True
    consent: bool = True
    consent_ceiling: int = COST
    next_switch: int = 20 * DAY
    immune_end: int = 0
    permanent: bool = False
    banned: bool = False
    version: int = 0
    mode_version: int = 0
    signed_dates: set = field(default_factory=set)
    signed_times: list = field(default_factory=list)
    watch: list = field(default_factory=list)
    closed_windows: set = field(default_factory=set)
    ledger: list = field(default_factory=list)
    grants: list = field(default_factory=list)
    renewals: list = field(default_factory=list)
    applied_end: int = 30 * DAY

    def selection_visible(self, now):
        if self.permanent:
            return False
        return now >= self.immune_end or 0 < self.end - now < SPAN

    def choose(self, mode, now, consent=False):
        if not self.selection_visible(now):
            return 'SELECTION_HIDDEN'
        actual_change = mode != self.mode
        if actual_change and self.mode is not None and now < self.next_switch:
            return 'COOLDOWN'
        if mode == 'CHECKIN_POINTS' and not consent:
            return 'CONSENT_REQUIRED'
        if actual_change or self.mode is None:
            self.next_switch = now + COOLDOWN
            self.mode = mode
            self.mode_version += 1
        if not self.participating or self.consent != (mode == 'CHECKIN_POINTS'):
            self.mode_version += 1
        self.participating = True
        self.consent = mode == 'CHECKIN_POINTS'
        return 'MODE_SELECTED'

    def signed_in_window(self):
        return len({t // DAY for t in self.signed_times if self.end - SPAN <= t < self.end})

    def checkin(self, now, reward=16):
        date = now // DAY  # Test epoch is midnight Asia/Shanghai.
        if date in self.signed_dates:
            return 'ORIGINAL_CHECKIN'
        self.signed_dates.add(date)
        self.signed_times.append(now)
        self.balance += reward
        self.version += 1
        self.ledger.append(('CHECKIN', reward, date))
        return 'CHECKIN_COMMITTED'

    def valid_watch_seconds(self, now):
        clipped = sorted((max(a, self.end - SPAN), min(b, self.end, now)) for a, b in self.watch
                         if b > self.end - SPAN and a < min(self.end, now))
        total, right = 0, self.end - SPAN
        for a, b in clipped:
            if b > max(right, a):
                total += b - max(right, a)
                right = b
        return total

    def settle(self, now, expected_end=None, expected_mode_version=None, simulate_abort=False):
        if self.banned or not self.participating or self.permanent:
            return 'NOT_AUTHORIZED'
        if expected_end is not None and expected_end != self.end:
            return 'STALE_WINDOW'
        if expected_mode_version is not None and expected_mode_version != self.mode_version:
            return 'STALE_MODE'
        old = self.end
        if now < old - SPAN:
            return 'FUTURE_WINDOW'
        if now >= old:
            return 'EXPIRED_NO_SILENT_DEBIT'
        if old in self.closed_windows:
            return 'ALREADY_SETTLED'
        if self.mode == 'CHECKIN_POINTS':
            if not self.consent or COST > self.consent_ceiling:
                return 'CONSENT_REQUIRED'
            if self.balance < COST:
                return 'INSUFFICIENT'
        elif self.mode == 'WATCH':
            if self.valid_watch_seconds(now) < RULE['watch_B']['required_valid_seconds']:
                return 'WATCH_INCOMPLETE'
        else:
            return 'NO_MODE'
        if simulate_abort:
            return 'ABORT_WITHOUT_COMMIT'
        if self.mode == 'CHECKIN_POINTS':
            self.balance -= COST
            self.ledger.append(('ACTIVITY_A_DEBIT', -COST, old))
        self.closed_windows.add(old)
        self.end = old + SPAN
        self.version += 1
        self.grants.append(('ACTIVITY_RENEWAL', old, self.end))
        self.renewals.append({'mode': self.mode, 'old_end': old, 'new_end': self.end, 'signed_days': sum(old - SPAN <= t < old for t in self.signed_times)})
        return 'RENEWED'

    def manual_card(self, now, preview_version, days=30, price=300):
        if preview_version != self.version:
            return 'PREVIEW_CONFLICT'
        if self.balance < price:
            return 'INSUFFICIENT'
        old = self.end
        self.closed_windows.add(old)
        self.balance -= price
        self.end = max(now, old) + days * DAY
        self.immune_end = self.end
        self.participating = False
        self.consent = False
        self.mode_version += 1
        self.version += 1
        self.ledger.append(('MANUAL_EXEMPT_CARD', -price, old))
        self.grants.append(('EXEMPT_PLAYBACK', max(now, old), self.end))
        if old > now:
            self.grants.append(('EXPLICIT_REMAINDER_EXEMPTION_OVERLAY', now, old))
        return 'CARD_COMMITTED'


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    checks = []

    def check(condition, name):
        assert condition, name
        checks.append(name)

    a = Member(balance=284)
    for d in range(19):
        a.signed_dates.add(d)
        a.signed_times.append(d * DAY)
    check(a.signed_in_window() == 19, 'current window displays 19 actual signed dates')
    check(a.settle(19 * DAY) == 'INSUFFICIENT', 'A cannot debit unavailable balance')
    check(a.checkin(19 * DAY) == 'CHECKIN_COMMITTED' and a.balance == 300, '284 plus actual checkin 16 equals 300')
    check(a.signed_in_window() == 20, 'signed-day counter changes independently of wallet')
    check(a.settle(19 * DAY) == 'RENEWED' and a.balance == 0 and a.end == 60 * DAY, 'A debits 300 once and appends 30 to original end')
    check(a.grants[-1][0] == 'ACTIVITY_RENEWAL' and a.immune_end == 0, 'automatic A is activity renewal, not an exempt card')
    check(a.checkin(19 * DAY + 1) == 'ORIGINAL_CHECKIN' and a.balance == 0, 'same-day callback retry never awards or charges again')
    check(a.settle(19 * DAY + 1) == 'FUTURE_WINDOW' and len(a.renewals) == 1, 'future next window does not run immediately')
    check(a.signed_in_window() == 0 and a.renewals[0]['signed_days'] == 20, 'new future progress does not erase historical settled counter')
    rich = Member(balance=900)
    check(rich.settle(0) == 'RENEWED' and rich.balance == 600, 'existing 300 balance needs zero new checkin days')
    check(rich.settle(1) == 'FUTURE_WINDOW' and rich.balance == 600, 'large balance cannot create an immediate renewal loop')
    check(rich.settle(30 * DAY) == 'RENEWED' and rich.end == 90 * DAY and rich.balance == 300, 'next window opens at old end and can settle once')
    frozen = Member(balance=299, frozen=1000)
    check(frozen.settle(0) == 'INSUFFICIENT' and frozen.frozen == 1000, 'frozen points cannot pay renewal')
    revoked = Member(balance=300, consent=False)
    check(revoked.settle(0) == 'CONSENT_REQUIRED', 'A authorization required at final evaluation')
    increased = Member(balance=1000, consent_ceiling=299)
    check(increased.settle(0) == 'CONSENT_REQUIRED', 'higher price than agreed ceiling requires consent')
    aborted = Member(balance=300)
    check(aborted.settle(0, simulate_abort=True) == 'ABORT_WITHOUT_COMMIT' and aborted.end == 30 * DAY and aborted.balance == 300 and not aborted.ledger, 'failed modeled transaction leaves money and expiry unchanged')
    expired = Member(balance=300)
    check(expired.settle(30 * DAY) == 'EXPIRED_NO_SILENT_DEBIT' and expired.balance == 300, 'deadline is end-exclusive and no later silent charge')

    b = Member(mode='WATCH', consent=False)
    b.watch = [(DAY, DAY + 1799)]
    check(b.settle(DAY + 1799) == 'WATCH_INCOMPLETE', 'B 29m59s is incomplete')
    b.watch.append((DAY + 1799, DAY + 1800))
    check(b.settle(DAY + 1800) == 'RENEWED' and b.end == 60 * DAY and b.balance == 0, 'B 30m renews 30 days without spending points')
    check(b.settle(DAY + 1801) == 'FUTURE_WINDOW', 'B cannot reuse old viewing immediately')
    check(b.settle(30 * DAY) == 'WATCH_INCOMPLETE', 'old viewing cannot qualify next window')
    b.watch.extend([(30 * DAY, 30 * DAY + 1800), (30 * DAY + 500, 30 * DAY + 1600)])
    check(b.valid_watch_seconds(30 * DAY + 1800) == 1800, 'multi-device overlapping intervals count only once')
    check(b.settle(30 * DAY + 1800) == 'RENEWED' and b.end == 90 * DAY, 'new window uses only its own viewing')
    cross = Member(end=60 * DAY, mode='WATCH', consent=False)
    cross.watch = [(30 * DAY - 600, 30 * DAY + 1200)]
    check(cross.valid_watch_seconds(30 * DAY + 1200) == 1200, 'cross-window viewing clipped at new start')

    mode = Member()
    check(mode.choose('WATCH', 10 * DAY) == 'COOLDOWN' and mode.next_switch == 20 * DAY, 'D10 switch rejected without resetting rights')
    check(mode.choose('WATCH', 35 * DAY) == 'MODE_SELECTED' and mode.next_switch == 55 * DAY, 'unspent D20 unlock survives until actual D35 change')
    check(mode.choose('WATCH', 36 * DAY) == 'MODE_SELECTED' and mode.next_switch == 55 * DAY, 'same-mode repeat does not reset cooldown')
    check(mode.choose('CHECKIN_POINTS', 80 * DAY, consent=True) == 'MODE_SELECTED' and mode.next_switch == 100 * DAY, 'later actual switch starts its own new 20 days')
    switch = Member(balance=300)
    switch.watch = [(20 * DAY, 20 * DAY + 1800)]
    old_mode_version = switch.mode_version
    check(switch.choose('WATCH', 20 * DAY + 1800) == 'MODE_SELECTED' and switch.end == 30 * DAY, 'unlocked immediate mode change does not extend deadline itself')
    check(switch.settle(20 * DAY + 1800, expected_mode_version=old_mode_version) == 'STALE_MODE' and switch.balance == 300, 'stale A candidate cannot debit after B change')
    check(switch.settle(20 * DAY + 1800) == 'RENEWED' and switch.balance == 300, 'B can use verified preselection progress in same unsettled window')

    immune = Member(balance=1000, mode=None, participating=False, consent=False, immune_end=30 * DAY)
    check(not immune.selection_visible(0), 'exactly 30 exempt days hides selection')
    check(immune.selection_visible(1), 'strictly less than 30 days shows optional selection')
    check(immune.settle(20 * DAY) == 'NOT_AUTHORIZED' and immune.balance == 1000, 'exempt default off never silently debits even with enough points')
    check(immune.checkin(20 * DAY) == 'CHECKIN_COMMITTED' and immune.balance == 1016, 'exempt member earns ordinary checkin wallet points')
    check(immune.choose('CHECKIN_POINTS', 20 * DAY, consent=True) == 'MODE_SELECTED', 'tail A requires explicit opt in')
    check(immune.settle(20 * DAY) == 'RENEWED' and immune.end == 60 * DAY and immune.immune_end == 30 * DAY, 'tail renewal preserves paid exemption and adds future activity segment')
    check(not immune.selection_visible(20 * DAY), 'renewed total coverage hides selection during remaining current exemption')
    tail_b = Member(mode=None, participating=False, consent=False, immune_end=30 * DAY)
    tail_b.watch = [(20 * DAY, 20 * DAY + 1800)]
    check(tail_b.choose('WATCH', 21 * DAY) == 'MODE_SELECTED' and tail_b.settle(21 * DAY) == 'RENEWED', 'optional exempt tail B uses its current-window verified progress')
    permanent = Member(permanent=True, balance=300, participating=False)
    check(not permanent.selection_visible(100 * DAY), 'permanent exemption never shows activity selection')

    manual_first = Member(balance=600)
    old = manual_first.end
    next_switch = manual_first.next_switch
    check(manual_first.manual_card(20 * DAY, manual_first.version) == 'CARD_COMMITTED' and manual_first.end == 60 * DAY and manual_first.immune_end == 60 * DAY, 'manual exempt card preserves 10 remaining days and appends a full 30')
    check(manual_first.next_switch == next_switch and not manual_first.participating, 'buying card pauses participation without resetting switch rights')
    check(manual_first.settle(20 * DAY, expected_end=old) == 'NOT_AUTHORIZED' and manual_first.balance == 300, 'manual card first invalidates old automatic candidate')
    check(any(g[0] == 'EXPLICIT_REMAINDER_EXEMPTION_OVERLAY' for g in manual_first.grants), 'preserved free remainder exemption is explicit overlay, not a fake payment')
    auto_first = Member(balance=600)
    preview = auto_first.version
    check(auto_first.settle(20 * DAY) == 'RENEWED', 'automatic-first race ordering commits once')
    check(auto_first.manual_card(20 * DAY, preview) == 'PREVIEW_CONFLICT' and auto_first.balance == 300, 'stale manual preview cannot take a second 300')
    check(auto_first.manual_card(20 * DAY, auto_first.version) == 'CARD_COMMITTED' and auto_first.end == 90 * DAY, 'explicit fresh second purchase may append legitimately')
    check(a.applied_end == 30 * DAY and a.end == 60 * DAY, 'Emby pending synchronization is separate from committed expiry')
    a.applied_end = a.end
    check(a.balance == 0 and len(a.renewals) == 1, 'sync retry does not charge again')
    banned = Member(balance=300, banned=True)
    check(banned.settle(0) == 'NOT_AUTHORIZED', 'security ban blocks activity settlement')

    report = {'version': 8, 'date': '2026-11-19', 'scope': 'SEQUENTIAL_RULE_MODEL_NOT_RUNTIME_OR_DB_CONCURRENCY_TEST',
              'checks_passed': len(checks), 'checks': checks,
              'example_A': {'initial_available': 284, 'daily_reward': 16, 'automatic_debit': 300, 'final_available': a.balance,
                            'old_end_day': 30, 'new_end_day': a.end // DAY, 'settled_signed_days': a.renewals[0]['signed_days']},
              'example_B': {'cost_points': 0, 'first_new_end_day': 60},
              'not_tested': ['real signature/auth/nonce and DB transactions', 'thread/process/distributed races',
                             'real Emby session sampling and paused/seek validation', 'late event adjudication and grace rescue',
                             'refund recomputation and production migration', '32-bit/clock/timezone/DST platform issues']}
    (ROOT / 'analysis/retention-model.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(json.dumps({k: v for k, v in report.items() if k not in ('checks', 'not_tested')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
