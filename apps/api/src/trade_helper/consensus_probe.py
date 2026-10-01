"""Read-only supplier checks. Unverified forecasts never enter Agent context."""

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx

from .credential_store import CredentialStoreError
from .db import connect, init_db
from .local_settings import desktop_mode, integration_credentials
from .storage_codec import load_json, to_datetime, utc_text

CALENDAR_URL = 'https://www.jblanked.com/news/api/forex-factory/calendar/range/'
# JBlanked FAQ documents GMT+3. Verify it against an official release time
# before using these dates to join series or accept pre-release forecasts.
PROVIDER_TIMEZONE = timezone(timedelta(hours=3))

# Exact names exclude ADP, jobless claims, productivity and the GDP price index.
# These labels identify candidates, not verified units or statistical periods.
TARGETS = {
    'CPI m/m': ('CPI', 'cpi_mom', None),
    'CPI y/y': ('CPI', 'cpi_yoy', None),
    'Core CPI m/m': ('CPI', 'core_cpi_mom', None),
    'Core CPI y/y': ('CPI', 'core_cpi_yoy', None),
    'Non-Farm Employment Change': ('employment', 'payroll_change', None),
    'Nonfarm Payrolls': ('employment', 'payroll_change', None),
    'Unemployment Rate': ('employment', 'unemployment_rate', None),
    'PCE Price Index m/m': ('PCE', 'pce_mom', None),
    'PCE Price Index y/y': ('PCE', 'pce_yoy', None),
    'Core PCE Price Index m/m': ('PCE', 'core_pce_mom', None),
    'Core PCE Price Index y/y': ('PCE', 'core_pce_yoy', None),
    'Advance GDP q/q': ('GDP', 'real_gdp_annualized', 'advance'),
    'Prelim GDP q/q': ('GDP', 'real_gdp_annualized', 'second'),
    'Final GDP q/q': ('GDP', 'real_gdp_annualized', 'third'),
}
GROUPS = ('CPI', 'employment', 'PCE', 'GDP')


def _has_numeric_forecast(value: object) -> bool:
    if value is None or isinstance(value, bool) or len(str(value)) > 40:
        return False
    try:
        return Decimal(str(value)).is_finite()
    except InvalidOperation:
        return False


def _schedule(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value, '%Y.%m.%d %H:%M:%S').replace(tzinfo=PROVIDER_TIMEZONE)
    except ValueError:
        if not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?',
            value,
        ):
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=PROVIDER_TIMEZONE)
    return parsed.astimezone(UTC)


def assess_rows(rows: list, *, observed_at: datetime | None = None) -> dict:
    observed_at = observed_at or datetime.now(UTC)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError('Observation time must include a timezone')
    if not isinstance(rows, list):
        raise TypeError('Unexpected calendar response')
    coverage = {key: {'events': 0, 'numeric_forecasts': 0, 'event_names': []} for key in GROUPS}
    metrics: dict[str, dict] = {}
    samples = []
    excluded_usd_events = 0
    for row in rows:
        if not isinstance(row, dict) or row.get('Currency') != 'USD':
            continue
        name = row.get('Name')
        if not isinstance(name, str) or name not in TARGETS:
            excluded_usd_events += 1
            continue
        group, metric, stage = TARGETS[name]
        numeric = _has_numeric_forecast(row.get('Forecast'))
        coverage[group]['events'] += 1
        coverage[group]['numeric_forecasts'] += int(numeric)
        if name not in coverage[group]['event_names']:
            coverage[group]['event_names'].append(name)
        target = metrics.setdefault(name, {
            'candidate_metric': metric, 'release_stage_from_name': stage,
            'events': 0, 'numeric_forecasts': 0,
            'future_numeric_forecasts': 0, 'post_release_numeric_forecasts': 0,
            'unknown_schedule': 0, 'scheduled_at_utc': [],
            'unit_verified': False, 'reference_period_verified': False,
            'consensus_definition_verified': False, 'usable_for_strategy': False,
        })
        target['events'] += 1
        target['numeric_forecasts'] += int(numeric)
        scheduled = _schedule(row.get('Date'))
        if numeric:
            samples.append({
                'name': name, 'candidate_metric': metric, 'release_stage_from_name': stage,
                'forecast': str(Decimal(str(row['Forecast']))),
                'scheduled_at_utc_assuming_provider_timezone': (
                    scheduled.isoformat() if scheduled else None
                ),
                'timing_status': (
                    'unknown_schedule' if scheduled is None else
                    'future_under_timezone_assumption' if scheduled > observed_at else
                    'post_release_under_timezone_assumption'
                ),
                'unit': None, 'reference_period': None, 'usable_for_strategy': False,
            })
        if scheduled is None:
            target['unknown_schedule'] += 1
        else:
            timestamp = scheduled.isoformat()
            if timestamp not in target['scheduled_at_utc']:
                target['scheduled_at_utc'].append(timestamp)
            if numeric:
                field = ('future_numeric_forecasts' if scheduled > observed_at
                         else 'post_release_numeric_forecasts')
                target[field] += 1
    return {
        'coverage': coverage, 'metrics': metrics, 'excluded_usd_events': excluded_usd_events,
        'unverified_forecast_samples': samples,
        'observed_at': observed_at.astimezone(UTC).isoformat(),
        'provider_timezone_assumption': 'UTC+03:00', 'timezone_verified': False,
        'usable_for_strategy': False,
        'remaining_checks': [
            '需核對來源 Forecast 是否為調查共識，而非模型預測',
            '需對照官方公布時間確認時區、統計月份／季度、單位及 GDP 公布批次',
            '月增率與年增率分開；ADP、失業申請與 GDP 價格指數不能代替目標實際值',
            '需事先保存公布前預期；事後取得的值不能回填為當時已知',
        ],
    }


def _failure(code: str, *, http_status: int | None = None) -> dict:
    guidance = {
        'AUTHENTICATION_REJECTED': (
            'JBlanked 拒絕請求；401 也可能與免費額度耗盡有關，不能直接判定金鑰失效。',
            '查看帳號 Free Uses；官方更新紀錄說明每天免費 1 次，額度用完先等待恢復。',
        ),
        'ACCESS_DENIED': (
            'JBlanked 拒絕存取此日曆端點。',
            '請確認帳號是否可使用 Forex Factory Calendar；不自動購買 credits。',
        ),
        'RATE_LIMITED': (
            'JBlanked 回報請求限流；無法由 HTTP 狀態判斷是否為每日額度。',
            '暫停請求並查看帳號使用量；本檢查不會自動重試。',
        ),
        'TIMEOUT': ('JBlanked 日曆請求逾時。', '稍後再執行檢查。'),
        'NETWORK_ERROR': ('無法連線至 JBlanked。', '確認網路後再執行檢查。'),
        'INVALID_RESPONSE': ('JBlanked 回應不符合日曆資料格式。', '保留未接入狀態並檢查供應商格式。'),
        'PROVIDER_ERROR': ('JBlanked 回報服務錯誤。', '稍後再執行檢查。'),
        'HTTP_ERROR': ('JBlanked 日曆請求失敗。', '依 HTTP 狀態檢查服務權限與可用性。'),
        'INVALID_KEY_FORMAT': (
            'JBLANKED_API_KEY 格式不正確。',
            '只填金鑰本身，不包含 Api-Key 前綴、引號或換行；不要在對話貼出金鑰。',
        ),
        'LOCAL_CACHE_UNAVAILABLE': (
            '無法讀寫本地日曆檢查快取；檢查未完成且不自動重試。',
            '確認應用程式資料目錄可寫入後，再執行檢查。',
        ),
        'CREDENTIALS_UNAVAILABLE': (
            '無法讀取本機已儲存的 JBlanked 金鑰，本次未呼叫日曆 API。',
            '在設定中重新儲存金鑰；本檢查不會要求系統金鑰圈互動。',
        ),
    }
    message, next_step = guidance[code]
    result = {
        'status': 'unavailable', 'provider': 'jblanked',
        'checked_at': datetime.now(UTC).isoformat(), 'error_code': code,
        'message': message, 'next_step': next_step, 'usable_for_strategy': False,
    }
    if http_status is not None:
        result['http_status'] = http_status
    return result


def _configured_key() -> str:
    credentials = integration_credentials('jblanked')
    key = credentials.get('api_key') if credentials else None
    return key.strip() if isinstance(key, str) else ''


def probe_jblanked() -> dict:
    try:
        key = _configured_key()
    except CredentialStoreError:
        return _failure('CREDENTIALS_UNAVAILABLE')
    if not key:
        return {'status': 'missing_key', 'provider': 'jblanked',
                'signup_url': 'https://www.jblanked.com/register/',
                'message': ('可先申請金鑰，在設定頁的 JBlanked 經濟日曆儲存'
                            if desktop_mode() else '可先申請免費金鑰，放入 .env 的 JBLANKED_API_KEY'),
                'usable_for_strategy': False}
    if key.lower().startswith('api-key ') or any(char in key for char in '\r\n\"\''):
        return _failure('INVALID_KEY_FORMAT')
    today = datetime.now(UTC).date()
    start, end = today - timedelta(days=30), today + timedelta(days=14)
    try:
        response = httpx.get(
            CALENDAR_URL,
            params={'from': start.isoformat(), 'to': end.isoformat(), 'currency': 'USD'},
            headers={'Authorization': f'Api-Key {key}', 'Content-Type': 'application/json'},
            timeout=30,
        )
        if response.status_code != 200:
            code = {
                401: 'AUTHENTICATION_REJECTED', 403: 'ACCESS_DENIED', 429: 'RATE_LIMITED',
            }.get(response.status_code, 'PROVIDER_ERROR' if response.status_code >= 500
                  else 'HTTP_ERROR')
            return _failure(code, http_status=response.status_code)
        observed = datetime.now(UTC)
        return {
            'status': 'checked', 'provider': 'jblanked', 'calendar_source': 'forex_factory',
            'checked_at': observed.isoformat(), 'requested_window': {
                'from': start.isoformat(), 'through': end.isoformat(),
                'full_coverage_verified': False,
            },
        } | assess_rows(response.json(), observed_at=observed)
    except httpx.TimeoutException:
        return _failure('TIMEOUT')
    except httpx.HTTPError:
        return _failure('NETWORK_ERROR')
    except (ValueError, TypeError):
        # Provider errors can include credentials: never return their body,
        # request URL, headers or exception; use only predefined messages.
        return _failure('INVALID_RESPONSE')


def probe_jblanked_cached() -> dict:
    """Reserve one attempt per rolling 24 hours, shared across local processes.

    Reserve and commit before the request. Even a timeout or process crash may
    consume a provider use, so retries and key changes cannot reset the budget.
    This is a local budget, not an assertion about the provider's reset time.
    """
    try:
        key = _configured_key()
    except CredentialStoreError:
        return _failure('CREDENTIALS_UNAVAILABLE')
    if not key or key.lower().startswith('api-key ') or any(char in key for char in '\r\n\"\''):
        return probe_jblanked()  # No network or DB request for invalid/missing keys.
    init_db()
    with connect() as db:
        now = datetime.now(UTC)
        prior = db.execute(
            'SELECT id,reserved_at,next_allowed_at,result FROM consensus_provider_checks '
            'WHERE provider=? ORDER BY reserved_at DESC,id DESC LIMIT 1', ('jblanked',),
        ).fetchone()
        if prior and to_datetime(prior['next_allowed_at']) > now:
            return load_json(prior['result']) | {
                'from_cache': True,
                'budget_reserved_at': to_datetime(prior['reserved_at']).isoformat(),
                'next_local_attempt_at': to_datetime(prior['next_allowed_at']).isoformat(),
            }
        next_at = now + timedelta(hours=24)
        pending = {
            'status': 'waiting', 'provider': 'jblanked', 'error_code': 'ATTEMPT_RESERVED',
            'message': '當次日曆檢查已保留額度；若程序中斷，仍等到下次本地允許時間。',
            'usable_for_strategy': False,
        }
        row = db.execute(
            'INSERT INTO consensus_provider_checks(provider,reserved_at,next_allowed_at,result) '
            'VALUES (?,?,?,?) RETURNING id',
            ('jblanked', utc_text(now), utc_text(next_at), json.dumps(pending, ensure_ascii=False)),
        ).fetchone()
    # The committed reservation prevents duplicate calls from concurrent CLIs.
    result = probe_jblanked()
    with connect() as db:
        db.execute('UPDATE consensus_provider_checks SET result=? WHERE id=?',
                   (json.dumps(result, ensure_ascii=False), row['id']))
    return result | {
        'from_cache': False, 'budget_reserved_at': now.isoformat(),
        'next_local_attempt_at': next_at.isoformat(),
    }


def main() -> None:
    try:
        result = probe_jblanked_cached()
    except sqlite3.Error:
        result = _failure('LOCAL_CACHE_UNAVAILABLE')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
