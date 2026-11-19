"""Build/check the v8 design deliverable; requires PyYAML, never contacts a service.

This checks documentation/configuration ownership and conservative example defaults.
It does not test a running backend, real authentication, updates or disaster recovery.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

try:
    import yaml
except ImportError as exc:
    raise SystemExit("This design checker requires PyYAML in the local environment.") from exc

ROOT = Path(__file__).resolve().parents[1]
VERSION = 8
SCHEMA_VERSION = 8
DATE = "2026-11-19"


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicated YAML keys rather than silently dropping an earlier value."""


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"Duplicated YAML key {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def leaves(value, path=""):
    if isinstance(value, dict) and value:
        for key, child in value.items():
            yield from leaves(child, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list) and value:
        for index, child in enumerate(value):
            yield from leaves(child, f"{path}[{index}]")
    else:
        yield path, value


def read_ref(configs, reference):
    filename, path = reference.split(":", 1)
    value = configs[filename]
    for key in path.split("."):
        value = value[key]
    return value


def render_compiled(chapters):
    intro = (
        "# 个人云盘 Emby 与 Telegram 服务完整优化方案\n\n"
        f"版本：{VERSION}\n\n更新日期：{DATE}\n\n"
        "最终设计：美西2C2G＋网盘、Emby自动刮削/追更、TG自助、注册/保号/积分、"
        "独立购买/收款发码、帮助/客户端、资源/防护/域名、备份/迁移均保留。"
        "独立网页总控通过独立管理员账号/MFA管理24模块和全部项目设置，TG面板是另一个入口。\n\n"
        "更新助手支持未来可信程序仓库自动检查/验证拉取、预检/备份/维护应用及兼容回退；"
        "程序仓库尚未创建、当前未配置且不执行更新，现有方案仓库不充当程序源。"
        "TG购买外链沿用2026-11-18核验后的有效关闭状态，独立网址由运营者手动告知。\n\n"
        "新增TG防连点/防爆破、真实身份跨入口预算、内存档位与手动/推荐/边界自动模式；"
        "100+邀请码注册采用有界数据库队列、单开户worker、轮巡/配额/资源保护，见第28篇。\n\n"
        "v8纠正保号：30天窗A授权够300自动扣分/B有效30分钟免费续，原到期＋30天；"
        "20天仅实际更换A/B后的冷却，解锁权保留。免保号不足30天才自愿选择，旧活动分废止。\n\n"
        "本文件是29篇完整设计的合订本，包含静态交互原型规格，未部署真实后端或通过生产验收。"
        "当前默认/遗漏闭环/实施顺序见第27篇，所有总控字段见第25篇，更新助手见第26篇；"
        "当前保号以第29/03/14/15篇及retention配置为准；本地核验见../analysis/v8-verification.md，保留旧版本历史。\n\n## 分篇目录\n\n"
    )
    toc = "".join(f"- [{p.stem}]({p.name})\n" for p in chapters)
    return intro + toc + "\n" + "\n\n---\n\n".join(p.read_text(encoding="utf-8").rstrip() for p in chapters) + "\n"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild", action="store_true", help="Regenerate the compiled design and field ownership inventory")
    parser.add_argument("--export", type=Path, help="Also save the compiled Markdown to this explicit file")
    args = parser.parse_args()
    checks = []

    def require(condition, label):
        if not condition:
            raise AssertionError(label)
        checks.append(label)

    configs = {p.name: yaml.load(p.read_text(encoding="utf-8"), Loader=UniqueLoader) for p in sorted((ROOT / "config").glob("*.yaml"))}
    admin = configs["admin-console.example.yaml"]
    modules = {section["id"] for section in admin["sections"]}
    require(len(admin["sections"]) == len(modules) == 24, "24 unique independent-web-console modules")
    require(all(c["schema_version"] == SCHEMA_VERSION for c in configs.values()), "all current design examples use v8 schema")
    roots = admin["settings_coverage"]["root_owners"]
    require(set(configs) == set(roots), "all config files explicitly registered, no implicit owner fallback")
    inventory = []
    for filename, config in configs.items():
        require(set(config) == set(roots[filename]), f"all top-level keys explicitly owned: {filename}")
        for root_key, owner in roots[filename].items():
            require(owner in modules, f"valid module: {filename}:{root_key}")
            for path, value in leaves(config[root_key], root_key):
                inventory.append({"file": filename, "path": path, "module": owner,
                                  "example_type": type(value).__name__,
                                  "status": "DESIGN_OWNERSHIP_ONLY_RUNTIME_FIELD_METADATA_PENDING"})
    require(set(row["module"] for row in inventory) == modules, "every console module owns design fields")
    for pair in admin["shared_setting_aliases"]["pairs"]:
        require(read_ref(configs, pair["source"]) == read_ref(configs, pair["target"]), f"derived alias matches canonical: {pair['source']}")
    for section in admin["sections"]:
        if "config" in section:
            require(section["config"] in configs, f"module config exists: {section['id']}")

    update = configs["update-assistant.example.yaml"]["update_assistant"]
    policy = configs["policy.example.yaml"]
    commerce = configs["commerce.example.yaml"]
    backup = configs["backup.example.yaml"]["backup_policy"]
    require(not update["runtime_implemented"] and not update["effective_enabled"], "updater remains a design, not enabled")
    require(update["initial_status"] == "UNCONFIGURED" and update["program_repository"]["full_name"] is None
            and not update["program_repository"]["created"], "future program repo explicitly absent")
    require(not update["documentation_repository_is_program_source"], "documentation repo never used as software source")
    require(not update["automation"]["auto_apply_enabled"] and update["scope"]["auto_apply_components"] == [], "no default auto-apply authorization")
    require(not update["acquisition"]["production_worktree_git_pull"] and not update["acquisition"]["build_on_2c2g_production_host"], "isolated fetch and prebuilt production artifacts")
    require(update["trust"]["manifest_signature_and_artifact_digest_required"], "trusted provenance and digest required by design")
    require(not update["rollback"]["automatic_old_business_database_restore"]
            and update["rollback"]["after_new_writes_require_reconciliation"], "app rollback preserves new business writes")
    require(update["execution"]["executor_separate_from_web_lifecycle"]
            and update["execution"]["durable_checkpoints_and_fenced_single_writer"], "independent durable single-executor update workflow")
    require(not update["execution"]["web_can_access_docker_socket"], "no Docker socket in the web backend")
    require(not backup["remote_transport"]["disabling_remote_transport_disables_local_backup"], "remote switch does not disable base backup")
    require(backup["restore"]["compare_latest_claim_revoke_payment_watermarks"], "restoration checks latest claim/revoke/payment watermarks")
    require(not commerce["real_payments_enabled"] and not commerce["entrypoints"]["telegram_bot_and_mini_app"]["external_purchase_link"]["effective_enabled"], "cash and Telegram purchase link remain effectively off")
    require(policy["retention"]["mode_lock_days"] == 20 and policy["free_membership"]["cycle_days"] == 30, "30-day window and 20-day actual mode-change cooldown are separate")
    require(policy["points"]["checkin_reward"]["minimum"] == 10 and policy["points"]["checkin_reward"]["maximum"] == 20, "10-20 integer wallet reward retained")
    require(admin["console"]["standalone_web"] and not admin["console"]["telegram_client_or_bot_required"]
            and not admin["console"]["accepts_telegram_panel_session_as_admin"], "standalone admin auth remains separate from Telegram")
    tg = configs["telegram-guard.example.yaml"]["telegram_guard"]
    queue = configs["registration-queue.example.yaml"]["registration_queue"]
    sizing = configs["capacity-profiles.example.yaml"]["telegram_workload"]
    require(not tg["runtime_implemented"] and not queue["runtime_implemented"], "TG guard and queue are design only")
    require(tg["identity"]["bucket_scope"] == "VERIFIED_BOT_ID_AND_TELEGRAM_USER_ID"
            and tg["identity"]["verified_mini_app_identity_uses_same_user_budget"], "TG identity budget shared across verified entrypoints")
    require(tg["callbacks"]["check_permission_and_revocation_before_reuse"]
            and tg["callbacks"]["semantic_mutation_idempotency_persisted"], "callback cache cannot bypass current auth or durable write idempotency")
    require(not tg["resources"]["replay_or_cooldown_eviction_grants_fresh_write_budget"], "eviction never grants new sensitive write budget")
    require(queue["admission"]["one_active_intent_per_identity_and_service"]
            and queue["admission"]["admission_and_outstanding_counter_atomic"], "queue cap and identity uniqueness must be atomic")
    require(set(queue["admission"]["outstanding_includes"]) >= {"LEASED", "RETRY_WAIT", "RECONCILING", "WAITING_CAPACITY"}, "queue cap counts leased and uncertain work, not only waiting")
    require(queue["dispatch"]["initial_provisioning_workers"] == 1
            and queue["dispatch"]["initial_prefetch_ticket_refs"] == 2, "S profile starts with one provisioning worker and two refs")
    require(not queue["dispatch"]["one_db_connection_per_waiting_user"]
            and not queue["observability"]["huge_inmemory_waiter_array_or_sleep_per_user"], "durable waiting queue does not imply per-user resident tasks or DB connections")
    require(not queue["provisioning"]["unknown_result_blindly_retries_account_creation"], "unknown external create must reconcile before retry")
    require(not queue["invitations"]["code_count_equals_member_or_concurrent_stream_capacity"], "100 invitation codes do not automatically raise capacity")
    require(sizing["mode"] == "RECOMMEND_MANUAL_APPLY" and not sizing["auto_bounded"]["enabled"], "resource sizing default is recommendation with manual apply")
    require(not sizing["auto_bounded"]["member_daily_sales_playback_security_or_telegram_egress_auto_increase"], "RAM upgrade cannot auto-raise business security or Telegram egress limits")
    require(sizing["profiles"]["S"]["inbox_outstanding"] == tg["ingress"]["durable_inbox_outstanding_limit"]
            and sizing["profiles"]["S"]["registration_outstanding"] == queue["admission"]["outstanding_limit"], "S profile defaults agree with inbox and registration caps")
    require(queue["pressure"]["resume_available_memory_at_least_mib"] > queue["pressure"]["pause_new_provisioning_available_memory_below_mib"]
            and queue["pressure"]["resume_cgroup_usage_at_most_percent"] < queue["pressure"]["pause_new_provisioning_cgroup_usage_percent"], "pressure protection has recovery hysteresis")
    require(queue["admission"]["status_poll_recommended_seconds"] * (1 - queue["admission"]["status_poll_jitter_percent"] / 100)
            >= queue["admission"]["status_poll_minimum_seconds"], "suggested jitter never goes below status minimum interval")
    keep = configs["retention.example.yaml"]["retention"]
    require(keep["window"]["duration_days"] == keep["window"]["renewal_duration_days"] == 30
            and keep["window"]["success_expiry_formula"] == "OLD_PLAYBACK_END_PLUS_30_DAYS", "v8 renewal appends 30 days without losing current coverage")
    require(keep["checkin_A"]["automatic_debit_points"] == 300 and keep["checkin_A"]["minimum_current_window_checkin_days"] == 0
            and not policy["retention"]["checkin"]["activity_score_enabled"], "A only requires authorized available 300, no fixed signed-day or activity-score threshold")
    require(keep["watch_B"]["required_valid_seconds"] == 1800 and keep["watch_B"]["renewal_points_cost"] == 0, "B uses valid 30 minutes without points")
    require(keep["window"]["at_most_one_automatic_settlement_per_window"] and not keep["window"]["future_window_evaluated_before_start"], "early success cannot cascade into future automatic renewals")
    require(not keep["modes"]["unused_switch_permission_expires"] and not keep["modes"]["unchanged_selection_resets_cooldown"]
            and not keep["modes"]["renewal_purchase_exemption_or_migration_resets_cooldown"], "unused switch right survives and only actual selection/change starts cooldown")
    require(keep["exempt"]["default_participation"] == "OFF" and keep["exempt"]["selection_visible_only_when_playback_remaining_strictly_less_than_days"] == 30
            and not keep["exempt"]["exact_30_days_or_permanent_exemption_shows_selection"], "exempt selection remains optional and only below 30 days")
    require(keep["settlement"]["atomic_debit_grant_window_close_end_update_outbox"] and not keep["settlement"]["background_retry_can_consume_a_second_300_points"], "automatic debit and expiry must commit once together")
    require(keep["manual_cards"]["balance_preview_conflict_requires_new_confirmation"] and not keep["manual_cards"]["same_wallet_points_spent_twice"], "manual exempt card and automatic A cannot double-spend a stale wallet preview")

    chapters = sorted((ROOT / "docs").glob("[0-9][0-9]-*.md"))
    require([int(p.name[:2]) for p in chapters] == list(range(1, 30)), "29 consecutively numbered plan chapters")
    compiled = render_compiled(chapters)
    inventory_document = {"version": VERSION, "date": DATE, "scope": "DESIGN_ONLY_NOT_IMPLEMENTED_FORMS",
                          "config_files": len(configs), "console_modules": len(modules),
                          "field_paths": len(inventory), "by_module": dict(sorted(Counter(row["module"] for row in inventory).items())),
                          "fields": inventory}
    inventory_text = json.dumps(inventory_document, ensure_ascii=False, indent=2) + "\n"
    if args.rebuild:
        (ROOT / "docs" / "完整方案.md").write_text(compiled, encoding="utf-8", newline="\n")
        (ROOT / "analysis" / "control-surface-inventory.json").write_text(inventory_text, encoding="utf-8", newline="\n")
    require((ROOT / "docs" / "完整方案.md").read_text(encoding="utf-8") == compiled, "compiled plan matches all final chapters")
    require((ROOT / "analysis" / "control-surface-inventory.json").read_text(encoding="utf-8") == inventory_text, "field inventory reproducible and current")

    for p in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")), *sorted((ROOT / "analysis").glob("*.md"))]:
        contents = p.read_text(encoding="utf-8")
        require(sum(1 for line in contents.splitlines() if line.lstrip().startswith("```")) % 2 == 0, f"balanced Markdown fences: {p.name}")
        for raw in re.findall(r"(?<!!)\[[^\]\n]+\]\(([^)]+)\)", contents):
            target = raw.strip("<>")
            if target.startswith(("http:", "https:", "mailto:", "#")):
                continue
            target = unquote(target.split("#", 1)[0])
            require((p.parent / target).exists(), f"local link exists: {p.name} -> {target}")

    fragment = (ROOT / "ui" / "admin-console.fragment.html").read_text(encoding="utf-8")
    module_block = re.search(r"const modules=\[([\s\S]*?)\n    \];", fragment)
    require(module_block is not None, "admin prototype module registry exists")
    ui_modules = re.findall(r"\['([a-z_]+)'\s*,", module_block.group(1))
    require(len(ui_modules) == 24 and set(ui_modules) == modules, "24 prototype module IDs match the configuration")
    require("程序仓库未创建" in fragment and "未配置" in fragment, "updater prototype honestly marks absent program source")
    require(all(x in fragment for x in ["TG防连点", "资源调参", "注册排队", "RECOMMEND_MANUAL_APPLY", "AUTO_BOUNDED"]), "TG guard sizing and enrollment pages are present in admin prototype")
    wrapper = (ROOT / "ui" / "admin-console-preview.html").read_text(encoding="utf-8")
    require(fragment in wrapper, "standalone admin preview embeds current fragment")
    tg_fragment = (ROOT / "ui" / "tg-panel.fragment.html").read_text(encoding="utf-8")
    require(tg_fragment in (ROOT / "ui" / "panel-preview.html").read_text(encoding="utf-8"), "TG standalone preview embeds current v8 fragment")
    require(all(x in tg_fragment for x in ["本窗已签到", "nextSwitch", "免保号剩余≥30天", "maybeRenew"])
            and "state.activity" not in tg_fragment and "预约下期观影" not in tg_fragment, "TG prototype uses real signed-day progress and new cooldown rather than old activity scores")
    original = ROOT / "reference" / "emby-v1-original.markdown"
    source = Path("E:/1111陈朝阳/emby/emby.markdown")
    if source.exists():
        require(original.read_bytes() == source.read_bytes(), "original user source remains byte-exact")
    if args.export:
        args.export.parent.mkdir(parents=True, exist_ok=True)
        args.export.write_text(compiled, encoding="utf-8", newline="\n")
        require(args.export.read_bytes() == (ROOT / "docs" / "完整方案.md").read_bytes(), "export byte-matches final compiled plan")
    report = {"date": DATE, "design_checks_passed": len(checks), "config_files": len(configs),
              "console_modules": len(modules), "field_paths": len(inventory), "chapters": len(chapters),
              "compiled_sha256": hashlib.sha256(compiled.encode()).hexdigest(),
              "scope": "Documentation and design examples only; no runtime security/update/restore test",
              "checks": checks}
    validation = ROOT / ".validation"
    validation.mkdir(exist_ok=True)
    (validation / "v8-plan-checks.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "checks"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
