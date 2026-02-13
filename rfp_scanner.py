"""
ClimateView RFP Scanner Agent v1.1
Scans procurement portals for relevant climate action RFPs.

Fixes from v1.0:
- Cross-portal deduplication
- Record update on re-scan
- Retry on timeout
- Atomic writes
- Auto-expire stale records
- Pagination support
"""

import json
import hashlib
import os
import sys
import time
import shutil
import tempfile
import logging
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('rfp_scanner')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(SCRIPT_DIR, 'rfp_data.json')
SCAN_LOG_FILE = os.path.join(SCRIPT_DIR, 'scan_log.json')

sys.path.insert(0, SCRIPT_DIR)
from rfp_scorer import RFPScorer, RFPInput

KEYWORDS = {
    'en': ['climate action plan', 'greenhouse gas inventory', 'GHG emissions',
           'net-zero strategy', 'climate data platform', 'carbon accounting',
           'emissions reduction plan', 'climate software', 'sustainability reporting',
           'climate action', 'decarbonization', 'net zero'],
    'de': ['Klimaschutzkonzept', 'Treibhausgasbilanz', 'THG-Bilanz',
           'Klimaschutzmanagement', 'CO2-Bilanzierung', 'kommunaler Klimaschutz',
           'Nachhaltigkeitsbericht', 'Klimadaten'],
    'fr': ['plan climat', 'bilan carbone', 'inventaire GES', 'stratégie bas-carbone'],
    'nl': ['klimaatactieplan', 'broeikasgasinventaris', 'CO2-boekhouding'],
    'sv': ['klimathandlingsplan', 'växthusgasinventering'],
    'no': ['klimahandlingsplan', 'klimaregnskap', 'utslippsregnskap', 'bærekraftsrapportering'],
    'fi': ['ilmastosuunnitelma', 'kasvihuonekaasupäästöt', 'päästöinventaario'],
    'da': ['klimahandlingsplan', 'drivhusgasopgørelse', 'bæredygtighedsrapportering'],
}

CPV_CODES = ['71313000', '72000000', '90700000', '90730000', '72212000', '72260000', '90710000']

HEADERS = {
    'User-Agent': 'ClimateView-RFP-Scanner/1.1 (Climate Action Procurement Intelligence)',
    'Accept': 'application/json',
}

COUNTRY_TO_MARKET = {
    'US': 'North America', 'CA': 'North America',
    'GB': 'UK + Ireland', 'IE': 'UK + Ireland',
    'DE': 'DACH', 'AT': 'DACH', 'CH': 'DACH',
    'BE': 'Benelux', 'NL': 'Benelux', 'LU': 'Benelux',
    'SE': 'Nordics', 'DK': 'Nordics', 'NO': 'Nordics', 'FI': 'Nordics',
    'FR': 'Adjacent', 'ES': 'Adjacent', 'IT': 'Adjacent', 'PT': 'Adjacent',
    'AU': 'Adjacent', 'NZ': 'Adjacent',
}


def generate_id(title: str, entity: str) -> str:
    """Deterministic ID from normalized title+entity (portal-independent for cross-dedup)."""
    raw = f"{title.strip().lower()}|{entity.strip().lower()}"
    return f"rfp-{hashlib.md5(raw.encode()).hexdigest()[:12]}"


def atomic_save(data: list, path: str):
    """Write to temp file then rename for crash safety."""
    dir_name = os.path.dirname(path) or '.'
    fd, tmp_path = tempfile.mkstemp(suffix='.json', dir=dir_name)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        shutil.move(tmp_path, path)
        log.info(f"Saved {len(data)} RFPs to {path}")
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def load_existing_data() -> list:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return []


def auto_expire(data: list) -> int:
    """Set status='Passed' for expired RFPs that are still 'New'. Returns count changed."""
    today = datetime.now().strftime('%Y-%m-%d')
    count = 0
    for r in data:
        if r.get('deadline') and r['deadline'] < today and r.get('status') == 'New':
            r['status'] = 'Passed'
            r['pass_reason'] = 'Deadline elapsed without action'
            count += 1
    return count


def log_scan(portal: str, rfps_found: int, new_rfps: int, updated: int = 0, error: str = None):
    logs = []
    if os.path.exists(SCAN_LOG_FILE):
        with open(SCAN_LOG_FILE) as f:
            logs = json.load(f)
    logs.append({
        'timestamp': datetime.now().isoformat(),
        'portal': portal,
        'rfps_found': rfps_found,
        'new_rfps': new_rfps,
        'updated_rfps': updated,
        'error': error
    })
    logs = logs[-500:]
    with open(SCAN_LOG_FILE, 'w') as f:
        json.dump(logs, f, indent=2)


def fetch_with_retry(session, url, params=None, timeout=30, retries=1):
    """Fetch URL with retry on failure."""
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, params=params, timeout=timeout)
            return resp
        except (requests.Timeout, requests.ConnectionError) as e:
            if attempt < retries:
                wait = 5 * (attempt + 1)
                log.warning(f"Retry {attempt+1}/{retries} after {wait}s for {url}: {e}")
                time.sleep(wait)
            else:
                raise


def result_to_record(result, title, entity, country, description, budget_eur, deadline, portal, url, date_found=None):
    """Convert ScoringResult to a dashboard record dict."""
    market = COUNTRY_TO_MARKET.get(country.upper(), 'Adjacent') if country else 'Unknown'
    return {
        'id': generate_id(title, entity),
        'rfp_title': title,
        'issuing_entity': entity,
        'country': country,
        'market': market,
        'description': description[:2000] if description else '',
        'budget_eur': budget_eur,
        'deadline': deadline,
        'relevance_score': result.relevance_score,
        'win_probability': result.win_probability,
        'feature_alignment_score': result.feature_alignment_score,
        'geographic_fit_score': result.geographic_fit_score,
        'budget_fit_score': result.budget_fit_score,
        'timeline_score': result.timeline_score,
        'competitive_score': result.competitive_score,
        'strategic_value_score': result.strategic_value_score,
        'advisory_bonus': result.advisory_bonus,
        'feature_breakdown': result.feature_breakdown,
        'competitor_signals': result.competitor_signals,
        'competitor_recommendation': result.competitor_recommendation,
        'positive_signals': result.positive_signals,
        'edge_case_flags': result.edge_case_flags,
        'score_confidence': result.score_confidence,
        'rfp_type': result.rfp_type,
        'deadline_status': result.deadline_status,
        'scoring_config_version': result.scoring_config_version,
        'source_portal': portal,
        'source_url': url,
        'date_found': date_found or datetime.now().strftime('%Y-%m-%d'),
        'status': 'New',
        'notes': '',
        'status_history': [],
        'pass_reason': ''
    }


class PortalScanner:
    def __init__(self, scorer: RFPScorer):
        self.scorer = scorer
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def scan(self, lookback_days: int = 90) -> list:
        raise NotImplementedError


class SAMGovScanner(PortalScanner):
    PORTAL_NAME = 'SAM.gov'
    API_BASE = 'https://api.sam.gov/opportunities/v2/search'

    def scan(self, lookback_days: int = 90) -> list:
        results = []
        api_key = os.environ.get('SAM_API_KEY', '')
        if not api_key:
            log.warning("SAM_API_KEY not set. Get free key at https://api.sam.gov")
            return []

        posted_from = (datetime.now() - timedelta(days=lookback_days)).strftime('%m/%d/%Y')
        posted_to = datetime.now().strftime('%m/%d/%Y')

        for keyword in KEYWORDS['en'][:6]:
            try:
                params = {
                    'api_key': api_key,
                    'postedFrom': posted_from,
                    'postedTo': posted_to,
                    'keyword': keyword,
                    'ptype': 'p,k',
                    'limit': 25,
                    'offset': 0
                }
                resp = fetch_with_retry(self.session, self.API_BASE, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    for opp in data.get('opportunitiesData', []):
                        rec = self._parse(opp)
                        if rec:
                            results.append(rec)
                elif resp.status_code == 429:
                    log.warning(f"SAM.gov rate limited on '{keyword}', waiting 60s")
                    time.sleep(60)
                time.sleep(2)
            except Exception as e:
                log.error(f"SAM.gov error for '{keyword}': {e}")
        return results

    def _parse(self, opp: dict) -> dict:
        title = opp.get('title', '')
        entity = opp.get('organizationName', '') or opp.get('departmentName', '')
        description = opp.get('description', '') or opp.get('synopsis', '') or ''
        deadline = opp.get('responseDeadLine', '')
        notice_id = opp.get('noticeId', '')

        if deadline:
            try:
                dl = datetime.strptime(deadline[:10], '%Y-%m-%d')
                if dl < datetime.now():
                    return None
                deadline = dl.strftime('%Y-%m-%d')
            except ValueError:
                deadline = None

        rfp_input = RFPInput(title=title, issuing_entity=entity, description=description[:2000],
                             country='US', deadline=deadline,
                             source_portal=self.PORTAL_NAME,
                             source_url=f"https://sam.gov/opp/{notice_id}" if notice_id else None)
        result = self.scorer.score(rfp_input)
        if not result.qualified:
            return None
        return result_to_record(result, title, entity, 'US', description, None, deadline,
                                self.PORTAL_NAME, f"https://sam.gov/opp/{notice_id}" if notice_id else None)


class TEDScanner(PortalScanner):
    PORTAL_NAME = 'TED (EU)'
    # New consolidated API domain (old ted.europa.eu/api/v3.0 is deprecated)
    API_URLS = [
        'https://api.ted.europa.eu/v3/notices/search',
        'https://ted.europa.eu/api/v3.0/notices/search',  # legacy fallback
    ]

    def _find_api_url(self):
        """Probe API URLs and return the first working one."""
        for url in self.API_URLS:
            try:
                resp = self.session.get(url, params={'query': 'cpv=72000000', 'pageSize': 1, 'pageNum': 1}, timeout=15)
                if resp.status_code in (200, 400):  # 400 = recognized but bad query, still means URL works
                    log.info(f"TED API: using {url}")
                    return url
            except Exception:
                continue
        return self.API_URLS[0]  # default to new URL

    def scan(self, lookback_days: int = 90) -> list:
        results = []
        date_from = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y%m%d')
        api_url = self._find_api_url()

        # CPV code search
        for cpv in CPV_CODES[:4]:
            try:
                for page in range(1, 4):
                    params = {
                        'query': f'cpv={cpv} AND PD>=[{date_from}]',
                        'fields': 'ND,TI,CY,CA,DT,TVL',
                        'pageSize': 50,
                        'pageNum': page,
                    }
                    resp = fetch_with_retry(self.session, api_url, params=params)
                    if resp.status_code == 200:
                        data = resp.json()
                        notices = data.get('results', data.get('notices', []))
                        if isinstance(data, list):
                            notices = data
                        for notice in notices:
                            rec = self._parse_notice(notice)
                            if rec:
                                results.append(rec)
                        if len(notices) < 50:
                            break
                    else:
                        log.warning(f"TED CPV {cpv} page {page}: HTTP {resp.status_code}")
                        break
                    time.sleep(2)
            except Exception as e:
                log.error(f"TED error for CPV {cpv}: {e}")

        # Keyword search in multiple languages
        lang_groups = [KEYWORDS['en'][:3], KEYWORDS['de'][:3], KEYWORDS['fr'][:2],
                       KEYWORDS['nl'][:2], KEYWORDS['sv'][:1], KEYWORDS['no'][:1],
                       KEYWORDS['fi'][:1], KEYWORDS['da'][:1]]
        for lang_kws in lang_groups:
            for kw in lang_kws:
                try:
                    params = {'query': f'FT="{kw}" AND PD>=[{date_from}]',
                              'fields': 'ND,TI,CY,CA,DT,TVL',
                              'pageSize': 25, 'pageNum': 1}
                    resp = fetch_with_retry(self.session, api_url, params=params)
                    if resp.status_code == 200:
                        data = resp.json()
                        notices = data.get('results', data.get('notices', []))
                        if isinstance(data, list):
                            notices = data
                        for notice in notices:
                            rec = self._parse_notice(notice)
                            if rec:
                                results.append(rec)
                    time.sleep(2)
                except Exception as e:
                    log.error(f"TED keyword error '{kw}': {e}")
        log.info(f"TED: {len(results)} qualified notices found")
        return results

    def _parse_notice(self, notice: dict) -> dict:
        # Handle both legacy TED schema and eForms response formats
        title = notice.get('TI', notice.get('title', {}))
        if isinstance(title, dict):
            title = title.get('EN', '') or title.get('en', '') or next(iter(title.values()), '')
        entity = notice.get('CA', notice.get('MA', notice.get('buyerName', ''))) or 'Unknown'
        if isinstance(entity, dict):
            entity = entity.get('EN', '') or entity.get('officialName', '') or next(iter(entity.values()), '')
        country = (notice.get('CY', notice.get('country', '')) or '')[:2].upper() or 'EU'
        notice_id = notice.get('ND', notice.get('noticeId', notice.get('id', '')))
        deadline = notice.get('DT', notice.get('deadline', ''))
        budget = None
        tvl = notice.get('TVL', notice.get('estimatedValue', notice.get('totalValue', '')))
        if tvl:
            try:
                budget = float(str(tvl).replace(',', ''))
            except (ValueError, TypeError):
                pass

        # Get description from CONTENT field or title as fallback
        description = notice.get('CONTENT', notice.get('description', str(title)))
        if isinstance(description, dict):
            description = description.get('EN', '') or next(iter(description.values()), '')

        if deadline:
            try:
                dl_str = str(deadline)[:8]
                dl = datetime.strptime(dl_str, '%Y%m%d')
                if dl < datetime.now():
                    return None
                deadline = dl.strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                try:
                    dl = datetime.strptime(str(deadline)[:10], '%Y-%m-%d')
                    if dl < datetime.now():
                        return None
                    deadline = dl.strftime('%Y-%m-%d')
                except (ValueError, TypeError):
                    deadline = None

        url = f"https://ted.europa.eu/en/notice/-/{notice_id}" if notice_id else None
        rfp_input = RFPInput(title=str(title), issuing_entity=str(entity),
                             description=str(description)[:2000],
                             country=country, budget_eur=budget, deadline=deadline,
                             source_portal=self.PORTAL_NAME, source_url=url)
        result = self.scorer.score(rfp_input)
        if not result.qualified:
            return None
        return result_to_record(result, str(title), str(entity), country,
                                str(description), budget, deadline, self.PORTAL_NAME, url)


class UKContractsScanner(PortalScanner):
    PORTAL_NAME = 'Contracts Finder (UK)'
    API_BASE = 'https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search'

    def scan(self, lookback_days: int = 90) -> list:
        results = []
        published_from = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%dT00:00:00Z')
        for keyword in KEYWORDS['en'][:6]:
            try:
                params = {'keyword': keyword, 'publishedFrom': published_from, 'size': 50, 'stage': 'tender'}
                resp = fetch_with_retry(self.session, self.API_BASE, params=params)
                if resp.status_code == 200:
                    for release in resp.json().get('releases', []):
                        rec = self._parse_release(release)
                        if rec:
                            results.append(rec)
                time.sleep(2)
            except Exception as e:
                log.error(f"Contracts Finder error '{keyword}': {e}")
        return results

    def _parse_release(self, release: dict) -> dict:
        tender = release.get('tender', {})
        title = tender.get('title', '')
        description = tender.get('description', '')
        deadline = tender.get('tenderPeriod', {}).get('endDate', '')
        buyer = release.get('buyer', {})
        entity = buyer.get('name', 'Unknown')
        budget = None
        value = tender.get('value', {})
        if value.get('amount'):
            budget = value['amount']
            if value.get('currency', 'GBP') == 'GBP':
                budget = budget * 1.17

        if deadline:
            try:
                dl = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
                if dl.replace(tzinfo=None) < datetime.now():
                    return None
                deadline = dl.strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                deadline = None

        rfp_input = RFPInput(title=title, issuing_entity=entity, description=description[:2000],
                             country='GB', budget_eur=round(budget) if budget else None,
                             deadline=deadline, source_portal=self.PORTAL_NAME,
                             source_url=release.get('id', ''))
        result = self.scorer.score(rfp_input)
        if not result.qualified:
            return None
        return result_to_record(result, title, entity, 'GB', description,
                                round(budget) if budget else None, deadline,
                                self.PORTAL_NAME, release.get('id', ''))


class ScotlandScanner(PortalScanner):
    """Public Contracts Scotland – OCDS API, no auth required."""
    PORTAL_NAME = 'Public Contracts Scotland'
    API_BASE = 'https://api.publiccontractsscotland.gov.uk/v1/Notices'

    def scan(self, lookback_days: int = 90) -> list:
        results = []
        now = datetime.now()
        # API uses MM-YYYY format – collect all months in the lookback window
        months = set()
        for d in range(0, lookback_days + 1, 14):
            dt = now - timedelta(days=d)
            months.add(dt.strftime('%m-%Y'))
        months.add(now.strftime('%m-%Y'))  # always include current month

        for month in sorted(months):
            try:
                params = {'dateFrom': month, 'noticeType': 2, 'outputType': 0}
                resp = fetch_with_retry(self.session, self.API_BASE, params=params, timeout=60)
                if resp.status_code == 200:
                    data = resp.json()
                    releases = data.get('releases', []) if isinstance(data, dict) else data
                    log.info(f"  Scotland {month}: {len(releases)} notices")
                    for release in releases:
                        rec = self._parse_ocds(release)
                        if rec:
                            results.append(rec)
                else:
                    log.warning(f"Scotland {month}: HTTP {resp.status_code}")
                time.sleep(2)
            except Exception as e:
                log.error(f"Scotland error for {month}: {e}")
        return results

    def _parse_ocds(self, release: dict) -> dict:
        tender = release.get('tender', {})
        title = tender.get('title', '')
        description = tender.get('description', '')
        buyer = release.get('buyer', {})
        entity = buyer.get('name', 'Unknown')
        notice_id = release.get('id', '')

        deadline_raw = tender.get('tenderPeriod', {}).get('endDate', '')
        budget = None
        value = tender.get('value', {})
        if value.get('amount'):
            budget = value['amount']
            if value.get('currency', 'GBP') == 'GBP':
                budget = budget * 1.17  # GBP to EUR approx

        deadline = None
        if deadline_raw:
            try:
                dl = datetime.fromisoformat(deadline_raw.replace('Z', '+00:00'))
                if dl.replace(tzinfo=None) < datetime.now():
                    return None
                deadline = dl.strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                pass

        url = f"https://www.publiccontractsscotland.gov.uk/Search/Search_Switch.aspx?ID={notice_id}"
        rfp_input = RFPInput(title=title, issuing_entity=entity, description=description[:2000],
                             country='GB', budget_eur=round(budget) if budget else None,
                             deadline=deadline, source_portal=self.PORTAL_NAME, source_url=url)
        result = self.scorer.score(rfp_input)
        if not result.qualified:
            return None
        return result_to_record(result, title, entity, 'GB', description,
                                round(budget) if budget else None, deadline, self.PORTAL_NAME, url)


class WalesScanner(PortalScanner):
    """Sell2Wales – OCDS API, same format as Scotland."""
    PORTAL_NAME = 'Sell2Wales'
    API_BASE = 'https://api.sell2wales.gov.wales/v1/Notices'

    def scan(self, lookback_days: int = 90) -> list:
        results = []
        now = datetime.now()
        months = set()
        for d in range(0, lookback_days + 1, 14):
            dt = now - timedelta(days=d)
            months.add(dt.strftime('%m-%Y'))
        months.add(now.strftime('%m-%Y'))

        for month in sorted(months):
            try:
                params = {'dateFrom': month, 'noticeType': 2, 'outputType': 0}
                resp = fetch_with_retry(self.session, self.API_BASE, params=params, timeout=60)
                if resp.status_code == 200:
                    data = resp.json()
                    releases = data.get('releases', []) if isinstance(data, dict) else data
                    log.info(f"  Wales {month}: {len(releases)} notices")
                    for release in releases:
                        rec = self._parse_ocds(release)
                        if rec:
                            results.append(rec)
                else:
                    log.warning(f"Wales {month}: HTTP {resp.status_code}")
                time.sleep(2)
            except Exception as e:
                log.error(f"Wales error for {month}: {e}")
        return results

    def _parse_ocds(self, release: dict) -> dict:
        tender = release.get('tender', {})
        title = tender.get('title', '')
        description = tender.get('description', '')
        buyer = release.get('buyer', {})
        entity = buyer.get('name', 'Unknown')
        notice_id = release.get('id', '')

        deadline_raw = tender.get('tenderPeriod', {}).get('endDate', '')
        budget = None
        value = tender.get('value', {})
        if value.get('amount'):
            budget = value['amount']
            if value.get('currency', 'GBP') == 'GBP':
                budget = budget * 1.17

        deadline = None
        if deadline_raw:
            try:
                dl = datetime.fromisoformat(deadline_raw.replace('Z', '+00:00'))
                if dl.replace(tzinfo=None) < datetime.now():
                    return None
                deadline = dl.strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                pass

        url = f"https://www.sell2wales.gov.wales/Search/Search_Switch.aspx?ID={notice_id}"
        rfp_input = RFPInput(title=title, issuing_entity=entity, description=description[:2000],
                             country='GB', budget_eur=round(budget) if budget else None,
                             deadline=deadline, source_portal=self.PORTAL_NAME, source_url=url)
        result = self.scorer.score(rfp_input)
        if not result.qualified:
            return None
        return result_to_record(result, title, entity, 'GB', description,
                                round(budget) if budget else None, deadline, self.PORTAL_NAME, url)


class DoffinScanner(PortalScanner):
    """Doffin (Norway) – Azure API Management. Requires DOFFIN_API_KEY env var."""
    PORTAL_NAME = 'Doffin (Norway)'
    API_BASE = 'https://dof-notices-prod-api.developer.azure-api.net'

    def scan(self, lookback_days: int = 90) -> list:
        api_key = os.environ.get('DOFFIN_API_KEY', '')
        if not api_key:
            log.info("DOFFIN_API_KEY not set – skipping. Get key at https://dof-notices-prod-api.developer.azure-api.net/")
            return []

        results = []
        date_from = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
        headers = {'Ocp-Apim-Subscription-Key': api_key}

        for kw in KEYWORDS['no'][:3] + KEYWORDS['en'][:3]:
            try:
                params = {'keyword': kw, 'publishedFrom': date_from, 'size': 50}
                resp = self.session.get(f"{self.API_BASE}/api/v1/notices", params=params,
                                        headers=headers, timeout=30)
                if resp.status_code == 200:
                    for notice in resp.json().get('notices', resp.json() if isinstance(resp.json(), list) else []):
                        rec = self._parse(notice)
                        if rec:
                            results.append(rec)
                elif resp.status_code == 401:
                    log.warning("Doffin: Invalid API key")
                    return results
                time.sleep(2)
            except Exception as e:
                log.error(f"Doffin error '{kw}': {e}")
        return results

    def _parse(self, notice: dict) -> dict:
        title = notice.get('title', '')
        entity = notice.get('buyerName', notice.get('organization', 'Unknown'))
        description = notice.get('description', str(title))
        deadline = notice.get('deadline', notice.get('tenderDeadline', ''))
        notice_id = notice.get('id', notice.get('noticeId', ''))
        budget = notice.get('estimatedValue', None)

        if deadline:
            try:
                dl = datetime.fromisoformat(str(deadline).replace('Z', '+00:00'))
                if dl.replace(tzinfo=None) < datetime.now():
                    return None
                deadline = dl.strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                deadline = None

        url = f"https://doffin.no/notices/{notice_id}" if notice_id else None
        rfp_input = RFPInput(title=title, issuing_entity=str(entity), description=str(description)[:2000],
                             country='NO', budget_eur=round(float(budget) * 0.089) if budget else None,
                             deadline=deadline, source_portal=self.PORTAL_NAME, source_url=url)
        result = self.scorer.score(rfp_input)
        if not result.qualified:
            return None
        return result_to_record(result, title, str(entity), 'NO', str(description),
                                round(float(budget) * 0.089) if budget else None,
                                deadline, self.PORTAL_NAME, url)


class HilmaScanner(PortalScanner):
    """Hilma (Finland) – Azure API Management. Requires HILMA_API_KEY env var."""
    PORTAL_NAME = 'Hilma (Finland)'
    API_BASE = 'https://hns-hilma-prod-apim.developer.azure-api.net'

    def scan(self, lookback_days: int = 90) -> list:
        api_key = os.environ.get('HILMA_API_KEY', '')
        if not api_key:
            log.info("HILMA_API_KEY not set – skipping. Get key at https://hns-hilma-prod-apim.developer.azure-api.net/")
            return []

        results = []
        headers = {'Ocp-Apim-Subscription-Key': api_key}

        for kw in KEYWORDS['fi'][:2] + KEYWORDS['en'][:3]:
            try:
                params = {'keyword': kw, 'size': 50}
                resp = self.session.get(f"{self.API_BASE}/hilmatenders", params=params,
                                        headers=headers, timeout=30)
                if resp.status_code == 200:
                    tenders = resp.json() if isinstance(resp.json(), list) else resp.json().get('tenders', [])
                    for tender in tenders:
                        rec = self._parse(tender)
                        if rec:
                            results.append(rec)
                elif resp.status_code == 401:
                    log.warning("Hilma: Invalid API key")
                    return results
                time.sleep(2)
            except Exception as e:
                log.error(f"Hilma error '{kw}': {e}")
        return results

    def _parse(self, tender: dict) -> dict:
        title = tender.get('name', tender.get('title', ''))
        entity = tender.get('organization', tender.get('buyerName', 'Unknown'))
        description = tender.get('description', str(title))
        deadline = tender.get('tenderDate', tender.get('deadline', ''))
        tender_id = tender.get('id', '')
        budget = tender.get('estimatedValue', None)

        if deadline:
            try:
                dl = datetime.fromisoformat(str(deadline).replace('Z', '+00:00'))
                if dl.replace(tzinfo=None) < datetime.now():
                    return None
                deadline = dl.strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                deadline = None

        url = f"https://www.hankintailmoitukset.fi/en/notice/{tender_id}" if tender_id else None
        rfp_input = RFPInput(title=str(title), issuing_entity=str(entity), description=str(description)[:2000],
                             country='FI', budget_eur=round(float(budget)) if budget else None,
                             deadline=deadline, source_portal=self.PORTAL_NAME, source_url=url)
        result = self.scorer.score(rfp_input)
        if not result.qualified:
            return None
        return result_to_record(result, str(title), str(entity), 'FI', str(description),
                                round(float(budget)) if budget else None,
                                deadline, self.PORTAL_NAME, url)


SCANNERS = {
    'sam': SAMGovScanner,
    'ted': TEDScanner,
    'uk': UKContractsScanner,
    'scotland': ScotlandScanner,
    'wales': WalesScanner,
    'doffin': DoffinScanner,
    'hilma': HilmaScanner,
}


def run_scan(portals=None, lookback_days=30, dry_run=False):
    scorer = RFPScorer(os.path.join(SCRIPT_DIR, 'rfp_scoring_config.json'))
    existing = load_existing_data()

    # Auto-expire stale records
    expired_count = auto_expire(existing)
    if expired_count:
        log.info(f"Auto-expired {expired_count} stale RFPs")

    existing_by_id = {r['id']: r for r in existing}

    if portals is None:
        portals = list(SCANNERS.keys())

    all_new = []
    all_updated = 0
    for portal_key in portals:
        if portal_key not in SCANNERS:
            log.warning(f"Unknown portal: {portal_key}")
            continue
        log.info(f"Scanning {portal_key}...")
        scanner = SCANNERS[portal_key](scorer)
        try:
            results = scanner.scan(lookback_days=lookback_days)
            # Cross-portal dedup + update logic
            new_count = 0
            updated_count = 0
            for r in results:
                rid = r['id']
                if rid in existing_by_id:
                    # Update: merge new info into existing record
                    old = existing_by_id[rid]
                    changed = False
                    for field in ['deadline', 'budget_eur', 'relevance_score', 'win_probability',
                                  'deadline_status', 'competitor_recommendation', 'description']:
                        if r.get(field) and r[field] != old.get(field):
                            old[field] = r[field]
                            changed = True
                    if changed:
                        old['last_updated'] = datetime.now().strftime('%Y-%m-%d')
                        updated_count += 1
                else:
                    existing.append(r)
                    existing_by_id[rid] = r
                    new_count += 1
                    all_new.append(r)
            all_updated += updated_count
            log.info(f"  {portal_key}: {len(results)} found, {new_count} new, {updated_count} updated")
            log_scan(portal_key, len(results), new_count, updated_count)
        except Exception as e:
            log.error(f"  {portal_key} FAILED: {e}")
            log_scan(portal_key, 0, 0, 0, str(e))

    if dry_run:
        log.info(f"\n[DRY RUN] Would add {len(all_new)} new, update {all_updated}")
        for r in all_new:
            log.info(f"  + {r['rfp_title'][:60]} | {r['relevance_score']} | {r['win_probability']}")
        return all_new

    # Remove records expired >30 days ago (keep Won/Submitted indefinitely)
    cutoff = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    existing = [r for r in existing if
                not r.get('deadline') or
                r['deadline'] >= cutoff or
                r.get('status') in ('Submitted', 'Won', 'Reviewing')]

    atomic_save(existing, DATA_FILE)
    log.info(f"Total active: {len(existing)}, new: {len(all_new)}, updated: {all_updated}")
    return all_new


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='ClimateView RFP Scanner v1.1')
    parser.add_argument('--portal', type=str, help='Specific portal (sam, ted, uk)')
    parser.add_argument('--days', type=int, default=30, help='Lookback days')
    parser.add_argument('--dry-run', action='store_true', help='Preview without saving')
    args = parser.parse_args()
    portals = [args.portal] if args.portal else None
    run_scan(portals=portals, lookback_days=args.days, dry_run=args.dry_run)
