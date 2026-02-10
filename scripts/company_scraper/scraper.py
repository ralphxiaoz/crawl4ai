#!/usr/bin/env python3
"""
Crawl4AI + Deepseek Company Website Analyzer

This script crawls company websites using BFS deep crawling and analyzes them
with Deepseek AI to extract structured information.

Configuration is loaded from config.yaml.
Website list is read from websites.txt.
Results are saved with timestamps in the outputs/ directory.
"""

import asyncio
import json
import os
import sys
import yaml
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List
from urllib.parse import urlparse
import re

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    LLMConfig,
    CacheMode
)
from crawl4ai.extraction_strategy import LLMExtractionStrategy
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
from pydantic import BaseModel, Field, create_model

# Import content consolidator for universal deduplication
from content_consolidator import UniversalContentConsolidator


def sanitize_company_name(company_name: str) -> str:
    """Sanitize company name for use as a filename base."""
    safe_name = ''.join(c if c.isalnum() or c in [' ', '-', '_'] else '_' for c in company_name)
    return safe_name.replace(' ', '_')[:50]


def save_progress(progress: dict, progress_file: Path):
    """Save progress.json atomically (write to temp, then rename)."""
    progress["last_updated"] = datetime.now().isoformat()
    tmp_file = progress_file.with_suffix('.json.tmp')
    with open(tmp_file, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2, ensure_ascii=False)
    tmp_file.rename(progress_file)


import atexit
import signal

_active_lock_file: Path | None = None


def acquire_lock(output_dir: Path) -> Path:
    """Acquire a PID-based lock file. Exits if another instance is running."""
    global _active_lock_file
    lock_file = output_dir / ".lock"
    if lock_file.exists():
        try:
            pid = int(lock_file.read_text().strip())
            # Check if PID is still alive
            os.kill(pid, 0)
            print(f"ERROR: Another scraper (PID {pid}) is already running on this directory.")
            print(f"If the process is dead, remove {lock_file} and retry.")
            sys.exit(1)
        except (ValueError, ProcessLookupError):
            # PID is dead or invalid — stale lock
            print(f"Removing stale lock file (PID no longer running).")
            lock_file.unlink()
        except PermissionError:
            # Process exists but we can't signal it — it's alive
            print(f"ERROR: Another scraper (PID {pid}) is already running on this directory.")
            sys.exit(1)
    lock_file.write_text(str(os.getpid()))
    _active_lock_file = lock_file
    atexit.register(release_lock)
    return lock_file


def release_lock():
    """Release the lock file. Registered with atexit for automatic cleanup."""
    global _active_lock_file
    if _active_lock_file:
        try:
            _active_lock_file.unlink(missing_ok=True)
        except Exception:
            pass
        _active_lock_file = None


def setup_logging(script_dir: Path) -> logging.Logger:
    """Setup logging to both file and console"""
    # Create logs directory
    log_dir = script_dir / "logs"
    log_dir.mkdir(exist_ok=True)

    # Create timestamped log file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"crawl_{timestamp}.log"

    # Create logger
    logger = logging.getLogger("crawl4ai_deepseek")
    logger.setLevel(logging.DEBUG)

    # Remove existing handlers
    logger.handlers = []

    # File handler (detailed logging)
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_format)

    # Console handler (less verbose)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter('%(levelname)s - %(message)s')
    console_handler.setFormatter(console_format)

    # Add handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    logger.info(f"Logging initialized. Log file: {log_file.name}")

    return logger


def load_env_file(env_path: Path = None):
    """Load environment variables from .env file if it exists"""
    if env_path is None:
        env_path = Path(__file__).parent / ".env"

    if env_path.exists():
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                # Skip comments and empty lines
                if line and not line.startswith('#'):
                    # Parse KEY=value format
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        # Only set if not already in environment
                        if key not in os.environ:
                            os.environ[key] = value


def load_config(config_path: str = "config.yaml") -> Dict[str, Any]:
    """Load configuration from YAML file"""
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    return config


def load_websites(filepath: str = "websites.txt") -> List[str]:
    """
    Load company websites from a text file
    Lines starting with # are ignored (comments)
    """
    websites = []
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                # Normalize URL
                if not line.startswith(('http://', 'https://')):
                    line = 'https://' + line
                websites.append(line)
    return websites


def load_companies(filepath: str = "input/companies_validated.csv") -> List[Dict[str, str]]:
    """
    Load companies from validated CSV file (output of url_validator.py).

    CSV columns: Company, Original_URL, Researched_URL, Confidence, Status, LinkedIn, Notes

    Returns list of dicts with: name, url (best available), linkedin_url, confidence
    """
    import csv

    companies = []
    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.reader(f, delimiter='\t')

        # Skip header
        header = next(reader, None)
        if not header:
            return companies

        for row in reader:
            if len(row) < 5:
                continue

            name = row[0].strip()
            original_url = row[1].strip() if len(row) > 1 else ''
            researched_url = row[2].strip() if len(row) > 2 else ''
            confidence = row[3].strip() if len(row) > 3 else '0'
            status = row[4].strip() if len(row) > 4 else ''
            linkedin_url = row[5].strip() if len(row) > 5 else ''

            # Use researched_url if available, otherwise original_url
            url = researched_url or original_url

            # Skip if no valid URL
            if not url:
                continue

            # Normalize URL
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url

            companies.append({
                'name': name,
                'url': url,
                'linkedin_url': linkedin_url,
                'confidence': int(confidence) if confidence.isdigit() else 0,
                'status': status
            })

    return companies


def create_pydantic_model_from_schema(schema: Dict[str, Any]) -> type[BaseModel]:
    """
    Create a Pydantic model from the YAML schema definition
    """
    fields = {}

    for field_name, field_config in schema.items():
        field_type = field_config.get('type', 'string')
        description = field_config.get('description', '')

        # Map YAML types to Python types
        type_mapping = {
            'string': str,
            'integer': int,
            'number': float,
            'boolean': bool,
            'array': List[str],
            'object': Dict[str, Any]
        }

        python_type = type_mapping.get(field_type, str)

        # Create field with default None (optional)
        fields[field_name] = (
            python_type | None,
            Field(None, description=description)
        )

    # Create dynamic Pydantic model
    return create_model('CompanyAnalysis', **fields)


def clean_markdown_content(markdown_text: str) -> str:
    """
    Clean markdown content to reduce token usage:
    1. Remove images entirely
    2. Strip URLs from links (keep anchor text), preserving mailto:/tel:
    """
    if not markdown_text:
        return ""

    # 1. Remove images: ![alt](url) -> ""
    # Matches ![...](...)
    no_images = re.sub(r'!\[([^\]]*)\]\([^)]+\)', '', markdown_text)

    # 2. Process links: [text](url)
    # We use a callback to check the URL
    def link_replacer(match):
        text = match.group(1)
        url = match.group(2)
        # Preserve contact links
        if url.startswith(('mailto:', 'tel:')):
            return match.group(0) # Keep entire link
        return text # Keep only text

    cleaned = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', link_replacer, no_images)
    
    # Collapse multiple newlines
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    
    return cleaned.strip()


def get_domain_name(url: str) -> str:
    """Extract clean domain name from URL for filename"""
    parsed = urlparse(url)
    domain = parsed.netloc or parsed.path
    # Remove www. and clean up
    domain = domain.replace('www.', '')
    # Remove special characters
    domain = ''.join(c if c.isalnum() or c in ['.', '-'] else '_' for c in domain)
    return domain


async def crawl_and_analyze_website(
    crawler: AsyncWebCrawler,
    company: Dict[str, Any],
    config: Dict[str, Any],
    output_dir: Path,
    logger: logging.Logger
) -> Dict[str, Any]:
    """
    Crawl a website and analyze it with Deepseek

    Args:
        company: Dict with 'name', 'url', 'linkedin_url', 'confidence', 'status'
    """
    url = company['url']
    company_name = company['name']
    linkedin_url = company.get('linkedin_url', '')

    print(f"\n{'='*80}")
    print(f"Processing: {company_name}")
    print(f"URL: {url}")
    if linkedin_url:
        print(f"LinkedIn: {linkedin_url}")
    print(f"{'='*80}\n")

    logger.info(f"Starting processing for: {company_name} ({url})")
    logger.debug(f"Output directory: {output_dir}")

    # Create filename from company name (sanitized)
    safe_name = sanitize_company_name(company_name)
    base_filename = safe_name

    # Configure Deepseek
    logger.debug("Configuring Deepseek LLM")
    deepseek_config = config['deepseek']
    llm_config = LLMConfig(
        provider=deepseek_config['model'],
        api_token=os.getenv('DEEPSEEK_API_KEY'),
        temperature=deepseek_config.get('temperature', 0.7),
        max_tokens=deepseek_config.get('max_tokens', 4000)
    )
    logger.debug(f"Deepseek model: {deepseek_config['model']}, temp: {deepseek_config.get('temperature', 0.7)}")

    # Create Pydantic model from schema
    logger.debug("Creating Pydantic model from schema")
    CompanyModel = create_pydantic_model_from_schema(config['output_schema'])
    
    # 1. Setup Content Filtering (Markdown Generator) FIRST
    content_filter_config = config['content_filter']
    md_generator = None
    if content_filter_config.get('enabled', False):
        logger.debug(f"Configuring content filter (threshold: {content_filter_config['threshold']})")
        print(f"DEBUG: Content Filter Threshold: {content_filter_config['threshold']}, Type: {content_filter_config['threshold_type']}")
        filter = PruningContentFilter(
            threshold=content_filter_config['threshold'],
            threshold_type=content_filter_config['threshold_type'],
            min_word_threshold=content_filter_config['min_word_threshold']
        )
        md_generator = DefaultMarkdownGenerator(content_filter=filter)
        logger.debug("Markdown generator configured with pruning filter")
    else:
        md_generator = DefaultMarkdownGenerator()
        logger.debug("Markdown generator configured (default)")

    # 2. Create BFS Deep Crawl Strategy
    logger.debug("Creating BFSDeepCrawlStrategy")
    crawl_settings = config['crawl_settings']
    # Use max_pages in the wrapper logic or deep crawl strategy if supported.
    deep_crawl_strategy = BFSDeepCrawlStrategy(
        max_depth=crawl_settings.get('max_depth', 3),
        max_pages=crawl_settings.get('max_pages', 30),
        include_external=False
    )

    # 3. Configure crawler run for deep crawl (USING md_generator)
    run_config = CrawlerRunConfig(
        deep_crawl_strategy=deep_crawl_strategy,
        markdown_generator=md_generator,
        cache_mode=CacheMode.ENABLED, # Use cache for speed if retrying
    )
    
    # Get system prompt and replace language placeholder
    output_language = deepseek_config.get('output_language', 'English')
    system_prompt = config['system_prompt'].replace('{output_language}', output_language)
    logger.debug(f"Output language: {output_language}")

    # Initialize extraction strategy (used later for analysis, not during crawl)
    extraction_strategy = LLMExtractionStrategy(
        llm_config=llm_config,
        schema=config['output_schema'],
        instruction=system_prompt,
        chunk_token_threshold=deepseek_config.get('chunk_token_threshold', 50000)
    )

    print(f"🔧 Configuration:")
    print(f"   Strategy: BFS Deep Crawl (replaces {crawl_settings.get('strategy', 'adaptive')})")
    print(f"   Max pages: {crawl_settings.get('max_pages', 30)}, "
                f"max_depth: {crawl_settings.get('max_depth', 3)}")

    try:
        print(f"🔍 Starting BFS deep crawl...")
        logger.info(f"Starting BFS deep crawl for {url}")

        # Run the deep crawl
        results = await crawler.arun(
            url=url,
            config=run_config
        )

        print(f"\n✓ Crawl complete!")
        print(f"  Pages crawled: {len(results)}")
        logger.info(f"Crawl complete. Pages: {len(results)}")

        # Combine all crawled content
        logger.debug("Combining content from all crawled pages")
        all_content = []
        all_content_filtered = []
        successful_pages = 0
        original_size = 0
        filtered_size = 0

        # Combine all crawled content
        logger.debug("Combining content from all crawled pages")
        
        # Deduplicate pages based on normalized URL AND Content Similarity
        # 1. Normalize URL (stripping www.)
        # 2. Check content similarity (Jaccard index) to avoid adding redundant pages
        unique_pages = {} # normalized_url -> (page, content_length)
        accepted_word_sets = []

        # Helper to compute word set
        def get_word_set(text):
            return set(w.lower() for w in text.split() if len(w) > 3)

        # 1. First, dedup by URL (keep longest)
        url_deduped = {}
        for page in results:
             if page.success and page.markdown:
                normalized_url = page.url.replace("www.", "").rstrip("/")
                raw_content = page.markdown.raw_markdown if hasattr(page.markdown, "raw_markdown") else str(page.markdown)
                # Ensure we have content to compare
                if normalized_url not in url_deduped:
                    url_deduped[normalized_url] = page
                else:
                    existing_content = url_deduped[normalized_url].markdown.raw_markdown if hasattr(url_deduped[normalized_url].markdown, "raw_markdown") else str(url_deduped[normalized_url].markdown)
                    if len(raw_content) > len(existing_content):
                        url_deduped[normalized_url] = page

        # 2. Then, filter by content similarity
        final_pages = []
        accepted_word_sets = []

        # Sort by URL for deterministic order
        for url_key in sorted(url_deduped.keys()):
            page = url_deduped[url_key]
            raw_content = page.markdown.raw_markdown if hasattr(page.markdown, "raw_markdown") else str(page.markdown)
            word_set = get_word_set(raw_content)
            
            is_redundant = False
            for accepted_set in accepted_word_sets:
                if not accepted_set or not word_set: continue
                intersection = len(accepted_set.intersection(word_set))
                union = len(accepted_set.union(word_set))
                jaccard = intersection / union if union > 0 else 0
                if jaccard > 0.8: 
                    is_redundant = True
                    print(f"  Start Skipping redundant page {page.url} ({jaccard:.0%} match)")
                    logger.info(f"Skipping content-redundant page {page.url} (Similarity: {jaccard:.2f})")
                    break
            
            if not is_redundant:
                final_pages.append(page)
                accepted_word_sets.append(word_set)

        # Now process unique pages
        all_content = []
        all_content_filtered = []
        successful_pages = 0
        original_size = 0
        filtered_size = 0

        for page in final_pages:
            # Add URL header
            all_content.append(f"# URL: {page.url}\n\n")
            all_content_filtered.append(f"# URL: {page.url}\n\n")

            # Add content (raw or filtered based on config)
            # Verify if raw_markdown attribute exists, else use formatted
            raw_content = ""
            if hasattr(page.markdown, "raw_markdown"):
                raw_content = page.markdown.raw_markdown
            elif hasattr(page, "markdown"):
                raw_content = str(page.markdown)
            
            if not raw_content:
                raw_content = ""
                
            all_content.append(raw_content)
            original_size += len(raw_content)

            # Use filtered content if available and enabled
            if content_filter_config.get("enabled", False) and hasattr(page.markdown, "fit_markdown") and page.markdown.fit_markdown:
                filtered_content = page.markdown.fit_markdown
                all_content_filtered.append(filtered_content)
                filtered_size += len(filtered_content)
            else:
                # No filtering or not available, use raw
                all_content_filtered.append(raw_content)
                filtered_size += len(raw_content)

            # Add separator
            all_content.append("\n\n" + "="*80 + "\n\n")
            all_content_filtered.append("\n\n" + "="*80 + "\n\n")
            combined_markdown = "\n".join(all_content)
        combined_markdown_filtered = "\n".join(all_content_filtered)

        # Log content reduction stats
        if content_filter_config.get("enabled", False) and filtered_size < original_size:
            reduction_pct = 100 * (1 - filtered_size / original_size) if original_size > 0 else 0
            logger.info(f"Content filtering reduced size: {original_size:,} → {filtered_size:,} chars ({reduction_pct:.1f}% reduction)")
            print(f"  📉 Content filtering: {original_size:,} → {filtered_size:,} chars ({reduction_pct:.1f}% reduction)")

            # Estimate token savings
            # Estimate token savings
            original_tokens = original_size // 4  # Rough estimate: 4 chars per token
            filtered_tokens = filtered_size // 4
            token_savings = original_tokens - filtered_tokens
            cost_savings = token_savings * 0.00000028  # Deepseek input cost per token
            logger.info(f"Estimated token savings: ~{token_savings:,} tokens (~)")
            print(f"  💰 Estimated savings: ~{token_savings:,} tokens (~)")

        # Save raw content
        if config['output'].get('save_raw_content', True):
            raw_file = output_dir / f"{base_filename}_raw.md"
            logger.debug(f"Saving raw content to {raw_file.name}")
            with open(raw_file, 'w', encoding='utf-8') as f:
                f.write(combined_markdown)
            print(f"💾 Saved raw content: {raw_file.name}")
            logger.info(f"Raw content saved: {raw_file.name} ({len(combined_markdown):,} chars)")

        # Save filtered content (if filtering is enabled and different from raw)
        if content_filter_config.get('enabled', False) and filtered_size < original_size:
            filtered_file = output_dir / f"{base_filename}_filtered.md"
            logger.debug(f"Saving filtered content to {filtered_file.name}")
            with open(filtered_file, 'w', encoding='utf-8') as f:
                f.write(combined_markdown_filtered)
            print(f"💾 Saved filtered content: {filtered_file.name}")
            logger.info(f"Filtered content saved: {filtered_file.name} ({len(combined_markdown_filtered):,} chars)")

        # Now analyze with Deepseek
        print(f"\n🤖 Analyzing with Deepseek...")
        logger.info("Starting Deepseek analysis")

        # Create a single-page crawl config for analysis
        analysis_config = CrawlerRunConfig(
            extraction_strategy=extraction_strategy,
            cache_mode=CacheMode.BYPASS
        )

        # We need to create a temporary HTML with the combined content
        # Use filtered content if available to reduce API costs
        content_for_analysis = combined_markdown_filtered if content_filter_config.get('enabled', False) else combined_markdown

        # Apply universal content consolidation (replaces custom_cleaning)
        consolidation_config = config.get('content_consolidation', {})
        if consolidation_config.get('enabled', True):
            logger.debug("Applying universal content consolidation")
            print(f"\n🔄 Applying content consolidation...")

            original_len = len(content_for_analysis)

            # Split content back into pages for cross-page analysis
            # Pages are separated by "\n\n" + "="*80 + "\n\n"
            page_separator = "\n\n" + "="*80 + "\n\n"
            pages = content_for_analysis.split(page_separator)

            # Initialize consolidator
            consolidator = UniversalContentConsolidator(consolidation_config)

            # Consolidate content
            content_for_analysis = consolidator.consolidate(pages)

            # Get and log statistics
            stats = consolidator.get_stats()
            logger.info(f"Content consolidation complete:")
            logger.info(f"  Original: {stats['original_chars']:,} chars")
            logger.info(f"  Final: {stats['final_chars']:,} chars")
            logger.info(f"  Total reduction: {stats['total_reduction_pct']}%")
            logger.info(f"  Boilerplate lines found: {stats['boilerplate_lines_found']}")
            logger.info(f"  Duplicates removed: {stats['duplicates_removed']}")
            logger.info(f"  Low-value lines removed: {stats['lowvalue_lines_removed']}")

            print(f"  📉 Content consolidation: {original_len:,} → {len(content_for_analysis):,} chars ({stats['total_reduction_pct']}% reduction)")
            print(f"     - Structural cleanup: {stats['structural_reduction_pct']}%")
            print(f"     - Boilerplate removal: {stats['boilerplate_reduction_pct']}% ({stats['boilerplate_lines_found']} lines)")
            print(f"     - Duplicate removal: {stats['duplicate_reduction_pct']}% ({stats['duplicates_removed']} occurrences)")
            print(f"     - Low-value filtering: {stats['lowvalue_reduction_pct']}% ({stats['lowvalue_lines_removed']} lines)")

            # Estimate token savings
            original_tokens = original_len // 4
            final_tokens = len(content_for_analysis) // 4
            token_savings = original_tokens - final_tokens
            print(f"  💰 Estimated token savings: ~{token_savings:,} tokens")
        elif config.get('custom_cleaning', {}).get('enabled', False):
            # Fallback to old custom cleaning if consolidation is disabled
            logger.debug("Applying custom content cleaning (legacy)")
            original_len = len(content_for_analysis)
            content_for_analysis = clean_markdown_content(content_for_analysis)
            cleaned_len = len(content_for_analysis)
            logger.info(f"Custom cleaning reduced content: {original_len:,} -> {cleaned_len:,} chars")
            print(f"  🧹 Custom cleaning reduced content: {original_len:,} -> {cleaned_len:,} chars")
        temp_html = f"<html><body><pre>{content_for_analysis}</pre></body></html>"
        logger.debug(f"Created temporary HTML for analysis ({len(temp_html)} chars)")
        logger.debug(f"Sending {'filtered' if content_filter_config.get('enabled', False) else 'full'} content to Deepseek")

        # Save Deepseek input payload for transparency/debugging
        deepseek_input_payload = {
            "metadata": {
                "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "website_url": url,
                "model": deepseek_config['model'],
                "temperature": deepseek_config.get('temperature', 0.7),
                "max_tokens": deepseek_config.get('max_tokens', 4000),
                "content_filtered": content_filter_config.get('enabled', False),
                "original_size_chars": len(combined_markdown),
                "sent_size_chars": len(content_for_analysis),
                "reduction_percent": round(100 * (1 - len(content_for_analysis) / len(combined_markdown)), 1) if len(combined_markdown) > 0 else 0
            },
            "system_prompt": system_prompt,
            "output_language": output_language,
            "json_schema": CompanyModel.model_json_schema(),
            "content_sent": content_for_analysis,
            "content_length_stats": {
                "characters": len(content_for_analysis),
                "estimated_tokens": len(content_for_analysis) // 4,  # Rough estimate
                "estimated_cost_input_usd": round((len(content_for_analysis) // 4) * 0.00000028, 6)
            }
        }

        # Save the input payload
        input_file = output_dir / f"{base_filename}_deepseek_input.json"
        logger.debug(f"Saving Deepseek input payload to {input_file.name}")
        with open(input_file, 'w', encoding='utf-8') as f:
            json.dump(deepseek_input_payload, f, indent=2, ensure_ascii=False)
        print(f"�� Saved Deepseek input: {input_file.name}")
        logger.info(f"Deepseek input payload saved: {input_file.name}")

        # Use raw: protocol to pass content directly
        logger.debug("Sending content to Deepseek via raw: protocol")
        analysis_result = await crawler.arun(
            url=f"raw:{temp_html}",
            config=analysis_config
        )

        if analysis_result.success:
            logger.info("Deepseek analysis successful")
            # Parse extracted JSON
            analysis_data = json.loads(analysis_result.extracted_content)
            
            # Save structured data
            if config['output'].get('save_json', True):
                json_file = output_dir / f"{base_filename}_analysis.json"
                logger.debug(f"Saving JSON analysis to {json_file.name}")
                with open(json_file, 'w', encoding='utf-8') as f:
                    json.dump(analysis_data, f, indent=2, ensure_ascii=False)
                print(f"💾 Saved analysis: {json_file.name}")
                logger.info(f"JSON analysis saved: {json_file.name}")

            # Save summary if available
            if config['output'].get('save_summary', True) and 'summary' in analysis_data and analysis_data['summary']:
                summary_file = output_dir / f"{base_filename}_summary.txt"
                logger.debug(f"Saving summary to {summary_file.name}")
                with open(summary_file, 'w', encoding='utf-8') as f:
                    f.write(f"SUMMARY FOR {analysis_data.get('company_name', url)}\n")
                    f.write("="*50 + "\n\n")
                    f.write(f"{analysis_data['summary']}\n\n")

                    # Add contact info if available
                    if analysis_data.get('contact_emails'):
                        f.write(f"\n📧 Contact Emails:\n")
                        for email in analysis_data['contact_emails']:
                            f.write(f"  - {email}\n")

                    if analysis_data.get('contact_phones'):
                        f.write(f"\n📞 Phone Numbers:\n")
                        for phone in analysis_data['contact_phones']:
                            f.write(f"  - {phone}\n")

                print(f"💾 Saved summary: {summary_file.name}")
                logger.info(f"Summary saved: {summary_file.name}")

            # Show token usage
            if config['output'].get('verbose', True):
                print(f"\n📊 Token Usage:")
                extraction_strategy.show_usage()
                logger.debug(f"Token usage: {extraction_strategy.total_usage}")

            logger.info(f"Successfully completed processing for {url}")

            # Build files dict
            files_dict = {
                'raw': f"{base_filename}_raw.md",
                'deepseek_input': f"{base_filename}_deepseek_input.json",
                'json': f"{base_filename}_analysis.json",
                'summary': f"{base_filename}_summary.txt"
            }

            # Add filtered file if it was created
            if content_filter_config.get('enabled', False) and filtered_size < original_size:
                files_dict['filtered'] = f"{base_filename}_filtered.md"

            # Inject company name and LinkedIn URL from validated data
            if isinstance(analysis_data, list) and len(analysis_data) > 0:
                analysis_data[0]['company_name'] = company_name
                analysis_data[0]['linkedin_url'] = linkedin_url
            elif isinstance(analysis_data, dict):
                analysis_data['company_name'] = company_name
                analysis_data['linkedin_url'] = linkedin_url

            return {
                'company_name': company_name,
                'url': url,
                'linkedin_url': linkedin_url,
                'status': 'success',
                'pages_crawled': len(results),
                'confidence': 1.0,
                'analysis': analysis_data,
                'files': files_dict
            }
        else:
            error_msg = analysis_result.error_message or "Unknown error"
            print(f"✗ Analysis failed: {error_msg}")
            logger.error(f"Analysis failed for {company_name}: {error_msg}")
            return {
                'company_name': company_name,
                'url': url,
                'linkedin_url': linkedin_url,
                'status': 'analysis_failed',
                'error': error_msg
            }

    except Exception as e:
        print(f"✗ Error processing {url}: {str(e)}")
        logger.error(f"Error processing {company_name}: {str(e)}", exc_info=True)
        import traceback
        traceback.print_exc()
        return {
            'company_name': company_name,
            'url': url,
            'linkedin_url': linkedin_url,
            'status': 'error',
            'error': str(e)
        }


async def main():
    """Main execution function"""
    # Get script directory
    script_dir = Path(__file__).parent

    # Setup logging first
    logger = setup_logging(script_dir)
    logger.info("="*80)
    logger.info("Crawl4AI + Deepseek Company Analyzer - Starting")
    logger.info("="*80)

    # Load .env file if it exists (for project-specific API keys)
    logger.debug("Loading environment variables from .env file")
    load_env_file()

    # Load configuration
    config_file = script_dir / "config.yaml"
    logger.debug(f"Loading configuration from {config_file}")
    if not config_file.exists():
        print(f"❌ Error: config.yaml not found at {config_file}")
        logger.error(f"config.yaml not found at {config_file}")
        print("Please create config.yaml with your settings.")
        return

    config = load_config(config_file)
    logger.info("Configuration loaded successfully")

    # Load companies from validated CSV
    companies_file = script_dir / "input" / "companies_validated.csv"
    logger.debug(f"Loading companies from {companies_file}")
    if not companies_file.exists():
        print(f"❌ Error: companies_validated.csv not found at {companies_file}")
        logger.error(f"companies_validated.csv not found at {companies_file}")
        print("Please run url_validator.py first to generate the validated companies file.")
        return

    companies = load_companies(companies_file)
    logger.info(f"Loaded {len(companies)} companies from {companies_file}")

    if not companies:
        print("❌ No companies with valid URLs found in companies_validated.csv")
        logger.error("No companies with valid URLs found")
        return

    # Check for Deepseek API key
    api_key = os.getenv('DEEPSEEK_API_KEY')
    if not api_key:
        print("⚠️  Warning: DEEPSEEK_API_KEY environment variable not set!")
        logger.error("DEEPSEEK_API_KEY not set")
        print("Please set it: export DEEPSEEK_API_KEY='your-api-key'")
        return

    logger.debug(f"DEEPSEEK_API_KEY found (length: {len(api_key)})")

    # Setup output directory with run-specific subfolder
    base_output_dir = script_dir / config['output']['output_dir']
    base_output_dir.mkdir(exist_ok=True)

    # Resume mode: reuse existing output directory
    resume_dir = None
    if len(sys.argv) >= 3 and sys.argv[1] == '--resume':
        resume_path = Path(sys.argv[2])
        if not resume_path.is_absolute():
            resume_dir = base_output_dir / sys.argv[2]
        else:
            resume_dir = resume_path
        if not resume_dir.exists():
            print(f"Error: Resume directory not found: {resume_dir}")
            logger.error(f"Resume directory not found: {resume_dir}")
            return
        logger.info(f"RESUME MODE: Resuming from {resume_dir}")

    if resume_dir:
        output_dir = resume_dir
        logger.info(f"Resuming into existing output directory: {output_dir}")
    else:
        # Create run-specific subfolder: timestamp_N_companies_scraped
        run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_folder_name = f"{run_timestamp}_{len(companies)}_companies_scraped"
        output_dir = base_output_dir / run_folder_name
        output_dir.mkdir(exist_ok=True)

    logger.info(f"Output directory: {output_dir}")
    logger.info(f"Processing {len(companies)} companies in this run")

    # Acquire lock — prevents multiple instances from running on the same directory
    lock_file = acquire_lock(output_dir)
    logger.info(f"Lock acquired (PID {os.getpid()})")

    # Load or initialize progress tracking
    progress_file = output_dir / "progress.json"
    if progress_file.exists():
        with open(progress_file, 'r') as f:
            progress = json.load(f)
        logger.info(f"Loaded existing progress: {progress['completed']} completed, "
                     f"{progress['failed']} failed of {progress['total_companies']}")
        print(f"\nResuming: {progress['completed']} already completed, "
              f"{progress['failed']} failed")
    else:
        progress = {
            "run_id": output_dir.name,
            "started_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
            "total_companies": len(companies),
            "completed": 0,
            "failed": 0,
            "skipped_on_resume": 0,
            "status": "running",
            "companies": {}
        }
        save_progress(progress, progress_file)

    print(f"\n{'='*80}")
    print(f"Crawl4AI + Deepseek Company Analyzer")
    print(f"{'='*80}")
    print(f"\nConfiguration:")
    print(f"  Model: {config['deepseek']['model']}")
    print(f"  Strategy: {config['crawl_settings']['strategy']}")
    print(f"  Max pages: {config['crawl_settings']['max_pages']}")
    print(f"  Language: {config['deepseek'].get('output_language', 'English')}")
    print(f"  Output directory: {output_dir}")
    print(f"\nCompanies to process: {len(companies)}")
    for i, company in enumerate(companies, 1):
        print(f"  {i}. {company['name']} ({company['url'][:40]}...)")

    # Configure browser
    browser_config = BrowserConfig(
        headless=True,
        verbose=config['output'].get('verbose', True)
    )

    results = []

    # Process each website
    logger.info("Initializing AsyncWebCrawler")
    async with AsyncWebCrawler(config=browser_config) as crawler:
        for i, company in enumerate(companies, 1):
            safe_name = sanitize_company_name(company['name'])

            # Check if already completed successfully
            company_progress = progress["companies"].get(safe_name, {})
            if company_progress.get("status") == "success":
                analysis_file = output_dir / f"{safe_name}_analysis.json"
                if analysis_file.exists():
                    logger.info(f"SKIP (already completed): {company['name']}")
                    print(f"\n[{i}/{len(companies)}] SKIP: {company['name']} (already completed)")
                    progress["skipped_on_resume"] += 1
                    results.append({
                        'company_name': company['name'],
                        'url': company['url'],
                        'linkedin_url': company.get('linkedin_url', ''),
                        'status': 'success',
                        'pages_crawled': company_progress.get('pages_crawled', 0),
                        'confidence': 1.0,
                        'analysis': None,
                        'files': {},
                        'resumed': True
                    })
                    continue
                else:
                    logger.warning(f"Progress says success but {safe_name}_analysis.json missing. Re-processing.")

            # Mark as in_progress
            progress["companies"][safe_name] = {
                "name": company['name'],
                "url": company['url'],
                "safe_name": safe_name,
                "status": "in_progress",
                "started_at": datetime.now().isoformat(),
                "finished_at": None,
                "duration_seconds": None,
                "pages_crawled": None,
                "error": None
            }
            save_progress(progress, progress_file)

            logger.info(f"Processing company {i}/{len(companies)}: {company['name']} ({company['url']})")
            start_time = datetime.now()
            result = await crawl_and_analyze_website(
                crawler,
                company,
                config,
                output_dir,
                logger
            )
            elapsed = (datetime.now() - start_time).total_seconds()

            results.append(result)

            # Update progress
            progress["companies"][safe_name]["status"] = result['status']
            progress["companies"][safe_name]["finished_at"] = datetime.now().isoformat()
            progress["companies"][safe_name]["duration_seconds"] = round(elapsed, 1)
            if result.get('error'):
                progress["companies"][safe_name]["error"] = result['error']
            if result.get('pages_crawled'):
                progress["companies"][safe_name]["pages_crawled"] = result['pages_crawled']

            if result['status'] == 'success':
                progress["completed"] += 1
            else:
                progress["failed"] += 1

            save_progress(progress, progress_file)
            logger.info(f"Completed {i}/{len(companies)} companies "
                        f"({progress['completed']} success, {progress['failed']} failed)")

    # Finalize progress
    if progress["failed"] > 0:
        progress["status"] = "completed_with_errors"
    else:
        progress["status"] = "completed"
    save_progress(progress, progress_file)

    # Print final summary
    print(f"\n\n{'='*80}")
    print(f"PROCESSING COMPLETE")
    print(f"{'='*80}\n")

    successful = sum(1 for r in results if r['status'] == 'success')
    failed = len(results) - successful

    logger.info("="*80)
    logger.info(f"FINAL RESULTS: {successful} successful, {failed} failed out of {len(results)} total")
    logger.info("="*80)

    print(f"✓ Successful: {successful}/{len(results)}")
    print(f"✗ Failed: {failed}/{len(results)}\n")

    for result in results:
        print(f"\n{result['company_name']} ({result['url']}):")
        if result['status'] == 'success':
            print(f"  ✓ Status: Success")
            print(f"  📄 Pages crawled: {result['pages_crawled']}")
            print(f"  🎯 Confidence: {result['confidence']:.2%}")
            print(f"  📁 Files:")
            for file_type, filename in result['files'].items():
                print(f"     - {filename}")

            # Show LinkedIn if available
            if result.get('linkedin_url'):
                print(f"  🔗 LinkedIn: {result['linkedin_url']}")

            # Show extracted contact info
            if result.get('analysis'):
                analysis = result['analysis']
                if isinstance(analysis, list): analysis = analysis[0] if analysis else {}
                if analysis.get('contact_emails'):
                    print(f"  📧 Emails found: {len(analysis['contact_emails'])}")
                    for email in analysis['contact_emails'][:3]:
                        print(f"     - {email}")
                if analysis.get('contact_phones'):
                    print(f"  📞 Phones found: {len(analysis['contact_phones'])}")
        else:
            print(f"  ✗ Status: {result['status']}")
            if result.get('error'):
                print(f"  Error: {result['error']}")

    # Aggregate all analysis JSON files into one combined file
    print(f"\n{'='*80}")
    print(f"AGGREGATING RESULTS")
    print(f"{'='*80}\n")

    logger.info("Aggregating all analysis JSON files into combined output")

    # Find all *_analysis.json files
    analysis_files = list(output_dir.glob("*_analysis.json"))

    if analysis_files:
        all_companies = []

        for analysis_file in analysis_files:
            try:
                with open(analysis_file, 'r', encoding='utf-8') as f:
                    company_data = json.load(f)
                    # Handle both single dict and list formats
                    if isinstance(company_data, list):
                        all_companies.extend(company_data)
                    else:
                        all_companies.append(company_data)
                logger.debug(f"Loaded {analysis_file.name}")
            except Exception as e:
                logger.error(f"Error reading {analysis_file.name}: {e}")

        # Save combined analysis
        combined_file = output_dir / "combined_analysis.json"
        with open(combined_file, 'w', encoding='utf-8') as f:
            json.dump(all_companies, f, indent=2, ensure_ascii=False)

        logger.info(f"Combined analysis saved: {combined_file.name}")
        print(f"✓ Combined analysis saved: {combined_file.name}")
        print(f"  Total companies: {len(all_companies)}")

        # Calculate statistics
        good_fits = sum(1 for c in all_companies if c.get('is_good_fit') == True)
        reach_out = sum(1 for c in all_companies if c.get('recommendation') == 'Reach out')
        skip = sum(1 for c in all_companies if c.get('recommendation') == 'Skip')
        research = sum(1 for c in all_companies if c.get('recommendation') == 'Research further')

        print(f"\n📊 Vendor Qualification Summary:")
        print(f"  ✅ Good fits: {good_fits}/{len(all_companies)}")
        print(f"  📞 Reach out: {reach_out}")
        print(f"  ❌ Skip: {skip}")
        print(f"  🔍 Research further: {research}")

        logger.info(f"Qualification summary: {good_fits} good fits, {reach_out} reach out, {skip} skip, {research} research further")

        # Show top good-fit companies
        if good_fits > 0:
            print(f"\n🎯 Top Good-Fit Companies:")
            good_fit_companies = [c for c in all_companies if c.get('is_good_fit') == True]
            for i, company in enumerate(good_fit_companies[:5], 1):
                print(f"  {i}. {company.get('company_name', 'Unknown')} - {company.get('recommendation', 'N/A')}")
                if company.get('contact_emails'):
                    print(f"     📧 {', '.join(company['contact_emails'][:2])}")
    else:
        logger.warning("No analysis files found to combine")
        print(f"⚠️  No analysis files found to combine")

    print(f"\n{'='*80}")
    print(f"All results saved to: {output_dir}")
    print(f"{'='*80}\n")

    logger.info(f"All results saved to: {output_dir}")
    logger.info("Session complete")
    logger.info("="*80)

    # Release lock (also handled by atexit)
    release_lock()
    logger.info("Lock released")


if __name__ == "__main__":
    asyncio.run(main())