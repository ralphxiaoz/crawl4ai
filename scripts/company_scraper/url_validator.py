#!/usr/bin/env python3
"""
Company URL Validator & Researcher

Validates existing URLs and researches missing ones using a hybrid approach:
1. Phase 1: Quick HTTP validation + content matching (free/fast)
2. Phase 2: LLM-powered research for missing/failed URLs (Deepseek)

Input/Output: companies.csv (tab-separated: Company Name, URL, Confidence)
"""

import asyncio
import csv
import os
import re
import sys
import yaml
import logging
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse
from difflib import SequenceMatcher
import httpx



def load_env_file(env_path: Path = None):
    """Load environment variables from .env file if it exists"""
    if env_path is None:
        env_path = Path(__file__).parent / ".env"

    if env_path.exists():
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        if key not in os.environ:
                            os.environ[key] = value


def setup_logging(script_dir: Path, verbose: bool = True) -> logging.Logger:
    """Setup logging to both file and console"""
    log_dir = script_dir / "logs"
    log_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"url_validator_{timestamp}.log"

    logger = logging.getLogger("url_validator")
    logger.setLevel(logging.DEBUG)
    logger.handlers = []

    # File handler
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO if verbose else logging.WARNING)
    console_handler.setFormatter(logging.Formatter('%(levelname)s - %(message)s'))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


def load_config(config_path: Path) -> dict:
    """Load configuration from YAML file"""
    default_config = {
        'url_validator': {
            'batch_size': 10,
            'timeout_seconds': 15,
            'max_concurrent': 5,
            'confidence_threshold': 70,
            'research_missing': True,
            'research_low_confidence': True,
            'low_confidence_threshold': 50,
        },
        'deepseek': {
            'model': 'deepseek-chat',
            'temperature': 0.3,
            'max_tokens': 500,
        }
    }

    if config_path.exists():
        with open(config_path, 'r') as f:
            file_config = yaml.safe_load(f) or {}
            # Merge with defaults
            if 'url_validator' in file_config:
                default_config['url_validator'].update(file_config['url_validator'])
            if 'deepseek' in file_config:
                default_config['deepseek'].update(file_config['deepseek'])

    return default_config


def normalize_url(url: str) -> str:
    """Normalize URL: add https:// if missing, handle www."""
    if not url:
        return ""

    url = url.strip()

    # Remove leading/trailing quotes if present
    url = url.strip('"\'')

    # Add https:// if no scheme
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    return url


def extract_company_core_name(company_name: str) -> Tuple[str, str]:
    """
    Extract the core company name for searching.

    DBA (Doing Business As) handling:
    - If company has multiple DBAs, use the LAST one (final trading name)
    - The name BEFORE first DBA is the legal entity name (use as fallback)

    Hyphenated names like "Company A, Inc.-Company B, Inc." are split.

    Returns: (primary_name, fallback_name) - both lowercase for matching
    """
    # Simple approach: split by DBA and take the last segment
    # This handles "Company DBA Name1 DBA Name2" → Name2
    dba_pattern = r'\s+(?:dba|d/b/a)[:\s]+'
    parts = re.split(dba_pattern, company_name, flags=re.IGNORECASE)

    if len(parts) > 1:
        # Has DBA - last part is the trading name
        last_dba = parts[-1].strip().lower()

        # Clean up - handle comma-separated list like "a4c, Seller1on1, Tech2date"
        # Take just the first item if comma-separated
        if ',' in last_dba:
            last_dba = last_dba.split(',')[0].strip()

        # Remove trailing LLC, Inc, etc.
        last_dba = re.sub(r',?\s*(llc|inc|corp|ltd)\.?$', '', last_dba, flags=re.IGNORECASE).strip()

        # Legal name (first part, before any DBA)
        legal_name = parts[0].strip()
        legal_name = re.sub(r',?\s*(llc|inc|corp|ltd|lp|llp|pllc)\.?$', '', legal_name, flags=re.IGNORECASE)
        legal_name = legal_name.strip().lower()

        return last_dba, legal_name

    # Handle hyphenated company names: "Company A, Inc.-Company B, Inc."
    # These often indicate related companies, use the first one
    if '-' in company_name and ', Inc.' in company_name:
        parts = company_name.split('-')
        if len(parts) >= 2:
            first_company = parts[0].strip()
            first_company = re.sub(r',?\s*(llc|inc|corp|ltd|lp|llp|pllc)\.?$', '', first_company, flags=re.IGNORECASE)
            return first_company.strip().lower(), None

    # No DBA - just clean up the company name
    name = company_name.lower()

    # Remove common business suffixes
    suffixes = [
        r',?\s*(llc|inc|corp|ltd|lp|llp|pllc)\.?$',
        r',?\s*(incorporated|limited|corporation)$',
        r'\s*[-;,&]\s*$',          # Trailing punctuation
    ]

    for pattern in suffixes:
        name = re.sub(pattern, '', name, flags=re.IGNORECASE)

    return name.strip(), None


def fuzzy_match_score(company_name: str, text: str) -> float:
    """
    Calculate how well the company name matches the text (0-100).
    Checks both the main name and any DBA names.
    """
    if not text:
        return 0.0

    text_lower = text.lower()
    primary_name, fallback_name = extract_company_core_name(company_name)

    # Check for exact substring match first
    if primary_name in text_lower:
        return 95.0
    if fallback_name and fallback_name in text_lower:
        return 90.0

    # Check individual significant words (>2 chars to include short company names)
    # Remove hyphens and dots for better matching
    primary_clean = primary_name.replace('-', ' ').replace('.', ' ')
    text_clean = text_lower.replace('-', ' ').replace('.', ' ')

    words = [w for w in primary_clean.split() if len(w) > 2]
    if words:
        # Check for word matches, handling singular/plural
        def word_matches(word, text):
            if word in text:
                return True
            # Handle singular/plural: "computers" matches "computer", "technologies" matches "technology"
            if word.endswith('s') and word[:-1] in text:
                return True
            if word.endswith('ies') and word[:-3] + 'y' in text:
                return True
            if word + 's' in text:
                return True
            return False

        matches = sum(1 for w in words if word_matches(w, text_clean))
        word_score = (matches / len(words)) * 85

        # If most words match (>=75%), that's a strong signal
        if matches / len(words) >= 0.75:
            return max(word_score, 80)

        # If at least half the words match, decent signal
        if matches / len(words) >= 0.5:
            return max(word_score, 60)

        if word_score > 40:
            return word_score

    # Check if the first two significant words match (often the brand name)
    if len(words) >= 2:
        first_two = words[:2]
        if all(w in text_clean for w in first_two):
            return 75.0

    # Fuzzy string matching as fallback
    ratio = SequenceMatcher(None, primary_name, text_lower[:200]).ratio()
    return ratio * 60


async def validate_url(
    client: httpx.AsyncClient,
    company_name: str,
    url: str,
    timeout: int,
    logger: logging.Logger
) -> Tuple[str, float, str]:
    """
    Validate a URL by fetching it and checking if content matches company name.

    Returns: (status, confidence, notes)
    - status: 'verified', 'mismatch', 'unreachable', 'redirect'
    - confidence: 0-100
    - notes: additional info
    """
    if not url:
        return ('missing', 0, 'No URL provided')

    normalized_url = normalize_url(url)

    try:
        response = await client.get(
            normalized_url,
            follow_redirects=True,
            timeout=timeout
        )

        # Check for significant redirects (different domain)
        final_url = str(response.url)
        original_domain = urlparse(normalized_url).netloc.replace('www.', '')
        final_domain = urlparse(final_url).netloc.replace('www.', '')

        if original_domain != final_domain:
            logger.debug(f"Redirect detected: {normalized_url} -> {final_url}")

        if response.status_code != 200:
            return ('unreachable', 0, f'HTTP {response.status_code}')

        # Extract text content for matching
        content = response.text[:50000]  # Limit content size

        # Extract title
        title_match = re.search(r'<title[^>]*>([^<]+)</title>', content, re.IGNORECASE)
        title = title_match.group(1).strip() if title_match else ''

        # Extract meta description
        meta_match = re.search(
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
            content, re.IGNORECASE
        )
        if not meta_match:
            meta_match = re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']',
                content, re.IGNORECASE
            )
        meta_desc = meta_match.group(1).strip() if meta_match else ''

        # Calculate match scores
        title_score = fuzzy_match_score(company_name, title)
        meta_score = fuzzy_match_score(company_name, meta_desc)

        # Also check body content (first 5000 chars)
        body_match = re.search(r'<body[^>]*>(.*?)</body>', content, re.IGNORECASE | re.DOTALL)
        body_text = re.sub(r'<[^>]+>', ' ', body_match.group(1)[:5000]) if body_match else ''
        body_score = fuzzy_match_score(company_name, body_text)

        # Also check domain name - company name often appears in URL
        domain_score = fuzzy_match_score(company_name, original_domain)

        # Take the best score
        confidence = max(title_score, meta_score, body_score, domain_score)

        # Determine status based on confidence
        if confidence >= 70:
            status = 'verified'
        elif confidence >= 40:
            status = 'likely'
        else:
            status = 'mismatch'

        notes = f"title_match={title_score:.0f}, meta_match={meta_score:.0f}, body_match={body_score:.0f}, domain_match={domain_score:.0f}"

        # Add redirect info if applicable
        if original_domain != final_domain:
            notes += f", redirected_to={final_domain}"
            # Lower confidence for redirects to completely different domains
            if confidence < 80:
                confidence *= 0.8

        logger.debug(f"{company_name}: {status} (confidence={confidence:.0f}, {notes})")

        return (status, confidence, notes)

    except httpx.TimeoutException:
        logger.debug(f"{company_name}: Timeout fetching {normalized_url}")
        return ('unreachable', 0, 'Timeout')
    except httpx.ConnectError as e:
        logger.debug(f"{company_name}: Connection error for {normalized_url}: {e}")
        return ('unreachable', 0, 'Connection failed')
    except Exception as e:
        logger.debug(f"{company_name}: Error validating {normalized_url}: {e}")
        return ('error', 0, str(e)[:50])


async def brave_search(
    query: str,
    api_key: str,
    logger: logging.Logger,
    count: int = 5
) -> list[dict]:
    """
    Search using Brave Search API.

    Returns list of results: [{"title": ..., "link": ..., "snippet": ...}, ...]
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={
                    "q": query,
                    "count": count,
                },
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": api_key,
                },
                timeout=15
            )

            if response.status_code == 429:
                logger.warning("Brave Search API rate limit reached")
                return []

            if response.status_code == 401:
                logger.error("Brave Search API: Invalid API key")
                return []

            response.raise_for_status()
            data = response.json()

            results = data.get("web", {}).get("results", [])
            return [
                {
                    "title": item.get("title", ""),
                    "link": item.get("url", ""),
                    "snippet": item.get("description", ""),
                    "displayLink": item.get("url", "").split("/")[2] if item.get("url") else "",
                }
                for item in results
            ]
    except Exception as e:
        logger.error(f"Brave search failed: {e}")
        return []


async def serper_search(
    query: str,
    api_key: str,
    logger: logging.Logger,
    count: int = 5
) -> list[dict]:
    """
    Search using Serper API (Google search results).

    Returns list of results: [{"title": ..., "link": ..., "snippet": ...}, ...]
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://google.serper.dev/search",
                json={
                    "q": query,
                    "num": count,
                },
                headers={
                    "X-API-KEY": api_key,
                    "Content-Type": "application/json",
                },
                timeout=15
            )

            if response.status_code == 429:
                logger.warning("Serper API rate limit reached")
                return []

            if response.status_code == 401:
                logger.error("Serper API: Invalid API key")
                return []

            response.raise_for_status()
            data = response.json()

            # Serper returns results in "organic" array
            results = data.get("organic", [])
            return [
                {
                    "title": item.get("title", ""),
                    "link": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                    "displayLink": item.get("link", "").split("/")[2] if item.get("link") else "",
                }
                for item in results[:count]
            ]
    except Exception as e:
        logger.error(f"Serper search failed: {e}")
        return []


async def web_search(
    query: str,
    config: dict,
    logger: logging.Logger,
    count: int = 5
) -> list[dict]:
    """
    Unified search function that uses the configured provider.

    Providers: "brave" or "serper"
    """
    search_config = config.get('url_validator', {}).get('search', {})
    provider = search_config.get('provider', 'brave').lower()

    if provider == 'serper':
        api_key = os.getenv('SERPER_API_KEY') or search_config.get('serper_api_key')
        if not api_key:
            logger.warning("SERPER_API_KEY not set, falling back to Brave")
            provider = 'brave'
        else:
            return await serper_search(query, api_key, logger, count)

    # Default: Brave
    api_key = os.getenv('BRAVE_API_KEY')
    if not api_key:
        # Try OpenClaw config
        openclaw_config_path = Path.home() / ".openclaw" / "openclaw.json"
        if openclaw_config_path.exists():
            try:
                with open(openclaw_config_path, 'r') as f:
                    openclaw_config = json.load(f)
                    api_key = openclaw_config.get('tools', {}).get('web', {}).get('search', {}).get('apiKey')
            except Exception:
                pass

    if not api_key:
        logger.warning("No search API key configured")
        return []

    return await brave_search(query, api_key, logger, count)


def extract_best_url_from_results(
    company_name: str,
    search_results: list[dict],
    logger: logging.Logger
) -> Tuple[str, float, str, str]:
    """
    Extract the most likely company URL and LinkedIn URL from search results.
    Uses heuristics to score and rank results.

    Returns: (url, confidence, notes, linkedin_url)
    """
    if not search_results:
        return ('', 0, 'No search results', '')

    primary_name, fallback_name = extract_company_core_name(company_name)
    candidates = []
    linkedin_url = ''

    for result in search_results:
        link = result.get('link', '')
        title = result.get('title', '').lower()
        snippet = result.get('snippet', '').lower()
        display_link = result.get('displayLink', '').lower()

        if not link:
            continue

        # Parse the domain
        parsed = urlparse(link)
        domain = parsed.netloc.replace('www.', '')

        # Capture LinkedIn URL separately
        if 'linkedin.com' in domain:
            if '/company/' in link and not linkedin_url:
                linkedin_url = link
                logger.debug(f"Found LinkedIn for {company_name}: {link}")
            continue  # Don't add to candidates

        # Skip other aggregator/directory sites (but NOT LinkedIn - we captured it above)
        skip_domains = [
            'facebook.com', 'twitter.com', 'instagram.com',
            'yelp.com', 'bbb.org', 'yellowpages.com', 'manta.com',
            'dnb.com', 'zoominfo.com', 'crunchbase.com', 'bloomberg.com',
            'mapquest.com', 'google.com', 'youtube.com', 'glassdoor.com',
        ]
        if any(skip in domain for skip in skip_domains):
            continue

        score = 0
        notes = []

        # Check if company name appears in domain
        domain_clean = domain.replace('.com', '').replace('.net', '').replace('.org', '').replace('-', '').replace('.', '')
        primary_clean = primary_name.replace(' ', '').replace('-', '').replace('.', '')

        if primary_clean in domain_clean or domain_clean in primary_clean:
            score += 40
            notes.append("domain_match")

        # Check if company name appears in title (primary name = DBA if present)
        if primary_name in title:
            score += 30
            notes.append("title_match")
        elif fallback_name and fallback_name in title:
            score += 25
            notes.append("legal_name_match")

        # Check snippet for company indicators
        company_indicators = ['official', 'home', 'welcome', 'about us', 'contact']
        if any(ind in snippet for ind in company_indicators):
            score += 10
            notes.append("official_indicator")

        # Bonus for being first result
        if result == search_results[0]:
            score += 15
            notes.append("top_result")

        # Check for ITAD-related keywords (domain-specific boost)
        itad_keywords = ['recycl', 'itad', 'asset', 'ewaste', 'electronics', 'refurbish', 'disposal']
        if any(kw in title or kw in snippet for kw in itad_keywords):
            score += 10
            notes.append("itad_related")

        candidates.append({
            'url': domain,
            'full_link': link,
            'score': score,
            'notes': notes,
            'title': result.get('title', '')
        })

    if not candidates:
        # No official site found, but we might have LinkedIn
        if linkedin_url:
            return ('', 0, 'No official site, LinkedIn only', linkedin_url)
        return ('', 0, 'No valid candidates after filtering', '')

    # Sort by score
    candidates.sort(key=lambda x: x['score'], reverse=True)
    best = candidates[0]

    # Convert score to confidence (0-100)
    # Max possible score is ~105, scale to confidence
    confidence = min(95, best['score'] + 20)  # Base 20 for being found

    logger.debug(f"Best candidate for {company_name}: {best['url']} (score={best['score']}, notes={best['notes']})")

    return (best['url'], confidence, f"brave: {', '.join(best['notes'])}", linkedin_url)


async def search_linkedin(
    company_name: str,
    config: dict,
    logger: logging.Logger
) -> str:
    """
    Search for company LinkedIn URL.

    Returns: linkedin_url or empty string
    """
    primary_name, _ = extract_company_core_name(company_name)

    linkedin_query = f"{primary_name} linkedin"
    logger.debug(f"Searching LinkedIn for: {linkedin_query}")

    linkedin_results = await web_search(linkedin_query, config, logger, count=5)

    for result in linkedin_results:
        link = result.get('link', '')
        if 'linkedin.com/company/' in link:
            logger.info(f"Found LinkedIn for {company_name}: {link}")
            return link

    return ''


async def research_company_url(
    company_name: str,
    config: dict,
    logger: logging.Logger
) -> Tuple[str, float, str, str]:
    """
    Research a company's URL using configured search provider (Brave or Serper).

    Returns: (url, confidence, notes, linkedin_url)
    """
    # Extract names - primary_name is DBA (trading name) if present, otherwise legal name
    primary_name, fallback_name = extract_company_core_name(company_name)

    # Search 1: Company official website (just company name, no extra keywords)
    query = f'"{primary_name}"'

    logger.debug(f"Searching for: {query}")
    results = await web_search(query, config, logger)

    # If no results with quoted search, try without quotes
    if not results:
        query = f"{primary_name}"
        logger.debug(f"Retrying without quotes: {query}")
        results = await web_search(query, config, logger)

    # If still no results and we have a fallback (legal name), try that
    if not results and fallback_name:
        query = f"{fallback_name}"
        logger.debug(f"Retrying with legal name: {query}")
        results = await web_search(query, config, logger)

    # Extract best URL from results (ignore LinkedIn from this search)
    url, confidence, notes, _ = extract_best_url_from_results(company_name, results, logger)

    if url:
        logger.info(f"Researched {company_name}: {url} (confidence={confidence})")

    # Search 2: Dedicated LinkedIn search
    linkedin_query = f"{primary_name} linkedin"
    logger.debug(f"Searching LinkedIn for: {linkedin_query}")
    linkedin_results = await web_search(linkedin_query, config, logger, count=3)

    linkedin_url = ''
    for result in linkedin_results:
        link = result.get('link', '')
        if 'linkedin.com/company/' in link:
            linkedin_url = link
            logger.info(f"Found LinkedIn for {company_name}: {linkedin_url}")
            break

    return (url, confidence, notes, linkedin_url)


async def process_companies(
    companies: list[dict],
    config: dict,
    logger: logging.Logger
) -> list[dict]:
    """
    Process all companies: validate existing URLs, research missing ones.
    """
    validator_config = config.get('url_validator', {})
    timeout = validator_config.get('timeout_seconds', 15)
    max_concurrent = validator_config.get('max_concurrent', 5)
    research_missing = validator_config.get('research_missing', True)
    research_low_confidence = validator_config.get('research_low_confidence', True)
    low_confidence_threshold = validator_config.get('low_confidence_threshold', 50)

    results = []
    semaphore = asyncio.Semaphore(max_concurrent)

    async with httpx.AsyncClient(
        headers={'User-Agent': 'Mozilla/5.0 (compatible; CompanyValidator/1.0)'},
        follow_redirects=True,
    ) as client:

        # Phase 1: Quick validation
        print(f"\n{'='*60}")
        print("Phase 1: Quick URL Validation")
        print(f"{'='*60}\n")

        async def validate_with_semaphore(company: dict) -> dict:
            async with semaphore:
                name = company['name']
                url = company.get('url', '')

                if url:
                    status, confidence, notes = await validate_url(
                        client, name, url, timeout, logger
                    )
                    return {
                        'name': name,
                        'original_url': normalize_url(url) if url else '',
                        'researched_url': '',
                        'status': status,
                        'confidence': confidence,
                        'notes': notes,
                        'linkedin_url': '',
                        'needs_research': status in ('mismatch', 'unreachable', 'error') or confidence < low_confidence_threshold
                    }
                else:
                    return {
                        'name': name,
                        'original_url': '',
                        'researched_url': '',
                        'status': 'missing',
                        'confidence': 0,
                        'notes': 'No URL provided',
                        'linkedin_url': '',
                        'needs_research': research_missing
                    }

        # Process in batches
        batch_size = validator_config.get('batch_size', 10)
        for i in range(0, len(companies), batch_size):
            batch = companies[i:i+batch_size]
            print(f"Processing batch {i//batch_size + 1}/{(len(companies) + batch_size - 1)//batch_size}...")

            tasks = [validate_with_semaphore(c) for c in batch]
            batch_results = await asyncio.gather(*tasks)
            results.extend(batch_results)

        # Summary of Phase 1
        verified = sum(1 for r in results if r['status'] == 'verified')
        likely = sum(1 for r in results if r['status'] == 'likely')
        missing = sum(1 for r in results if r['status'] == 'missing')
        failed = sum(1 for r in results if r['status'] in ('mismatch', 'unreachable', 'error'))

        print(f"\nPhase 1 Results:")
        print(f"  Verified: {verified}")
        print(f"  Likely: {likely}")
        print(f"  Missing URL: {missing}")
        print(f"  Failed/Mismatch: {failed}")

        # Phase 2: Research missing/failed URLs
        needs_research = [r for r in results if r.get('needs_research', False)]

        if needs_research and (research_missing or research_low_confidence):
            print(f"\n{'='*60}")
            print(f"Phase 2: Researching {len(needs_research)} companies")
            print(f"{'='*60}\n")

            for i, result in enumerate(needs_research):
                print(f"Researching ({i+1}/{len(needs_research)}): {result['name'][:50]}...")

                url, confidence, notes, linkedin_url = await research_company_url(
                    result['name'], config, logger
                )

                if url:
                    # Validate the researched URL
                    status, val_confidence, val_notes = await validate_url(
                        client, result['name'], url, timeout, logger
                    )

                    # Use the higher confidence (research or validation)
                    final_confidence = max(confidence * 0.9, val_confidence)  # Slight penalty for researched

                    result['researched_url'] = normalize_url(url)
                    result['status'] = 'researched_' + status
                    result['confidence'] = final_confidence
                    result['notes'] = f"{notes}; validation: {val_notes}"
                    if linkedin_url:
                        result['linkedin_url'] = linkedin_url
                else:
                    result['status'] = 'not_found'
                    result['notes'] = notes
                    if linkedin_url:
                        result['linkedin_url'] = linkedin_url

                # Rate limit for API calls
                await asyncio.sleep(0.5)

        # Phase 3: LinkedIn search for ALL companies without LinkedIn URL
        needs_linkedin = [r for r in results if not r.get('linkedin_url')]

        if needs_linkedin:
            print(f"\n{'='*60}")
            print(f"Phase 3: Searching LinkedIn for {len(needs_linkedin)} companies")
            print(f"{'='*60}\n")

            for i, result in enumerate(needs_linkedin):
                print(f"LinkedIn search ({i+1}/{len(needs_linkedin)}): {result['name'][:50]}...")

                linkedin_url = await search_linkedin(result['name'], config, logger)

                if linkedin_url:
                    result['linkedin_url'] = linkedin_url

                # Rate limit for API calls
                await asyncio.sleep(0.3)

    return results


def read_companies_file(filepath: Path) -> list[dict]:
    """
    Read companies from TSV/CSV file.
    Handles tab-separated format with optional header.
    """
    companies = []

    with open(filepath, 'r', encoding='utf-8') as f:
        # Detect delimiter (tab vs comma)
        first_line = f.readline()
        f.seek(0)

        if '\t' in first_line:
            delimiter = '\t'
        else:
            delimiter = ','

        reader = csv.reader(f, delimiter=delimiter)

        for row in reader:
            if not row:
                continue

            # Skip header row
            if row[0].lower().strip() in ('company', 'company name', 'name'):
                continue

            name = row[0].strip() if len(row) > 0 else ''
            url = row[1].strip() if len(row) > 1 else ''

            if name:
                companies.append({'name': name, 'url': url})

    return companies


def write_results(results: list[dict], filepath: Path):
    """
    Write results back to TSV file.
    Columns: Company, Original_URL, Researched_URL, Confidence, Status, LinkedIn, Notes
    """
    with open(filepath, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t')

        # Header
        writer.writerow(['Company', 'Original_URL', 'Researched_URL', 'Confidence', 'Status', 'LinkedIn', 'Notes'])

        # Sort by confidence descending
        sorted_results = sorted(results, key=lambda x: x.get('confidence', 0), reverse=True)

        for r in sorted_results:
            writer.writerow([
                r['name'],
                r.get('original_url', ''),
                r.get('researched_url', ''),
                f"{r.get('confidence', 0):.0f}",
                r.get('status', ''),
                r.get('linkedin_url', ''),
                r.get('notes', '')
            ])


def generate_websites_txt(results: list[dict], filepath: Path, min_confidence: int = 50):
    """
    Generate websites.txt from validated results.
    Only includes URLs above the confidence threshold.
    Uses researched_url if available and verified, otherwise original_url.
    """
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(f"# Generated by url_validator.py on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# Minimum confidence threshold: {min_confidence}\n\n")

        for r in results:
            if r.get('confidence', 0) >= min_confidence:
                # Prefer researched_url if available, otherwise use original_url
                url = r.get('researched_url') or r.get('original_url', '')
                if url:
                    # Remove https:// for cleaner format (scraper adds it back)
                    url = url.replace('https://', '').replace('http://', '')
                    f.write(f"{url}\n")


async def main():
    script_dir = Path(__file__).parent

    # Load .env file
    load_env_file()

    # Load config
    config_path = script_dir / "config.yaml"
    config = load_config(config_path)

    # Setup logging
    logger = setup_logging(script_dir)
    logger.info("="*60)
    logger.info("URL Validator & Researcher - Starting")
    logger.info("="*60)

    # Input/output files
    input_dir = script_dir / "input"
    input_file = input_dir / "companies.csv"
    output_file = input_dir / "companies_validated.csv"
    websites_file = script_dir / "websites.txt"

    if not input_file.exists():
        print(f"Error: {input_file} not found")
        print("Please create input/companies.csv with columns: Company, URL (tab-separated)")
        sys.exit(1)

    # Read companies
    companies = read_companies_file(input_file)
    print(f"\nLoaded {len(companies)} companies from {input_file.name}")
    logger.info(f"Loaded {len(companies)} companies")

    # Check for search API keys (needed for research phase)
    search_config = config.get('url_validator', {}).get('search', {})
    search_provider = search_config.get('provider', 'brave').lower()

    has_search_key = False
    if search_provider == 'serper':
        serper_key = os.getenv('SERPER_API_KEY') or search_config.get('serper_api_key')
        if serper_key:
            has_search_key = True
            print(f"\nUsing Serper (Google) for research")
        else:
            print("\nWarning: SERPER_API_KEY not set. Falling back to Brave.")
            search_provider = 'brave'

    if search_provider == 'brave':
        brave_key = os.getenv('BRAVE_API_KEY')
        openclaw_config_path = Path.home() / ".openclaw" / "openclaw.json"
        if not brave_key and openclaw_config_path.exists():
            try:
                with open(openclaw_config_path, 'r') as f:
                    openclaw_config = json.load(f)
                    brave_key = openclaw_config.get('tools', {}).get('web', {}).get('search', {}).get('apiKey')
            except Exception:
                pass
        if brave_key:
            has_search_key = True
            print(f"\nUsing Brave Search for research")

    if not has_search_key:
        print("\nWarning: No search API configured. Research phase will be skipped.")
        print("Options:")
        print("  - Brave: Set BRAVE_API_KEY or configure in OpenClaw")
        print("  - Serper: Set SERPER_API_KEY and set provider: 'serper' in config.yaml")
        logger.warning("No search API configured")

    # Process companies
    results = await process_companies(companies, config, logger)

    # Write results
    write_results(results, output_file)
    print(f"\nResults saved to: {output_file.name}")
    logger.info(f"Results saved to {output_file.name}")

    # Generate websites.txt for scraper
    confidence_threshold = config.get('url_validator', {}).get('confidence_threshold', 70)
    generate_websites_txt(results, websites_file, confidence_threshold)

    valid_count = sum(1 for r in results if r.get('confidence', 0) >= confidence_threshold)
    print(f"Generated websites.txt with {valid_count} validated URLs (confidence >= {confidence_threshold})")
    logger.info(f"Generated websites.txt with {valid_count} URLs")

    # Final summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")

    status_counts = {}
    for r in results:
        status = r.get('status', 'unknown')
        status_counts[status] = status_counts.get(status, 0) + 1

    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")

    # Show companies that need review
    needs_review = [r for r in results if r.get('confidence', 0) < confidence_threshold and r.get('url')]
    if needs_review:
        print(f"\nCompanies needing review ({len(needs_review)}):")
        for r in needs_review[:10]:
            print(f"  - {r['name'][:40]}: {r['url']} (confidence={r.get('confidence', 0):.0f})")
        if len(needs_review) > 10:
            print(f"  ... and {len(needs_review) - 10} more")

    print(f"\n{'='*60}")
    print(f"Done! Check {output_file.name} for full results.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
