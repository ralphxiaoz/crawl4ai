#!/usr/bin/env python3
"""
Universal Content Consolidator

Generic content deduplication and consolidation that works for ANY website.
Reduces token usage by removing cross-page duplication, boilerplate, and low-value content.

Applies AFTER page-level filtering, BEFORE sending to LLM.
"""

import re
from typing import Dict, Any, List, Set
from collections import Counter, defaultdict


class UniversalContentConsolidator:
    """
    Generic content deduplication that works for ANY website.

    Filters applied:
    1. Structural cleanup - Remove markdown artifacts, whitespace
    2. Cross-page boilerplate - Detect navigation/footers on 70%+ of pages
    3. Exact duplicate removal - Remove paragraphs appearing 6+ times
    4. Low-value lines - Remove nav items, CTAs appearing 20+ times
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize consolidator with configuration.

        Args:
            config: Configuration dict with thresholds:
                - boilerplate_threshold: Fraction of pages (0.0-1.0) for boilerplate detection
                - duplicate_min_occurrences: Min times content appears to be duplicate
                - short_line_freq_threshold: Min occurrences for short line removal
                - min_content_length: Min chars to consider as substantial content
        """
        self.boilerplate_threshold = config.get('boilerplate_threshold', 0.70)
        self.duplicate_min_occurrences = config.get('duplicate_min_occurrences', 6)
        self.short_line_freq_threshold = config.get('short_line_freq_threshold', 20)
        self.min_content_length = config.get('min_content_length', 50)

        # Stats for logging
        self.stats = {
            'original_chars': 0,
            'after_structural': 0,
            'after_boilerplate': 0,
            'after_duplicates': 0,
            'after_lowvalue': 0,
            'boilerplate_lines_found': 0,
            'duplicates_removed': 0,
            'lowvalue_lines_removed': 0
        }

    def consolidate(self, pages: List[str]) -> str:
        """
        Main pipeline: consolidate multi-page content into compact format.

        Args:
            pages: List of page contents (each page is markdown string)

        Returns:
            Consolidated content string
        """
        if not pages:
            return ""

        # Track original size
        combined_original = '\n\n'.join(pages)
        self.stats['original_chars'] = len(combined_original)

        # Step 1: Structural cleanup (each page)
        cleaned_pages = [self._clean_structure(page) for page in pages]
        self.stats['after_structural'] = sum(len(p) for p in cleaned_pages)

        # Step 2: Identify cross-page boilerplate
        boilerplate_lines = self._detect_boilerplate(cleaned_pages)
        self.stats['boilerplate_lines_found'] = len(boilerplate_lines)

        # Step 3: Remove boilerplate from pages
        deduplicated_pages = self._remove_boilerplate(cleaned_pages, boilerplate_lines)
        self.stats['after_boilerplate'] = sum(len(p) for p in deduplicated_pages)

        # Step 4: Combine all pages
        combined_content = '\n\n'.join(deduplicated_pages)

        # Step 5: Remove exact duplicates
        combined_content = self._deduplicate_content(combined_content)
        self.stats['after_duplicates'] = len(combined_content)

        # Step 6: Filter low-value lines
        combined_content = self._filter_low_value_lines(combined_content)
        self.stats['after_lowvalue'] = len(combined_content)

        # Step 7: Create final output with boilerplate header
        final_output = self._create_output(boilerplate_lines, combined_content)

        return final_output

    def _clean_structure(self, markdown: str) -> str:
        """
        Step 1: Remove structural artifacts and whitespace.
        Replaces the old custom_cleaning function.

        Removes:
        - Images: ![alt](url)
        - URLs from links (keeps text)
        - Empty markdown links: [](url)
        - Empty bullets
        - Punctuation-only lines
        - Excessive whitespace
        """
        if not markdown:
            return ""

        # 1. Remove images: ![alt](url)
        cleaned = re.sub(r'!\[([^\]]*)\]\([^)]+\)', '', markdown)

        # 2. Process links: [text](url) - keep text, remove URL (preserve mailto:/tel:)
        def link_replacer(match):
            text = match.group(1)
            url = match.group(2)
            if url.startswith(('mailto:', 'tel:')):
                return match.group(0)  # Keep entire link
            return text  # Keep only text

        cleaned = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', link_replacer, cleaned)

        # 3. Remove empty markdown links: [](url)
        cleaned = re.sub(r'\[\]\([^)]*\)', '', cleaned)

        # 4. Remove empty bullets (with optional whitespace)
        cleaned = re.sub(r'^\s*\*\s*$', '', cleaned, flags=re.MULTILINE)

        # 5. Remove lines with only punctuation/symbols
        cleaned = re.sub(r'^[\s\.,;:!?\-—–*#]+$', '', cleaned, flags=re.MULTILINE)

        # 6. Collapse multiple blank lines (3+ newlines -> 2)
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)

        # 7. Remove trailing whitespace per line
        lines = [line.rstrip() for line in cleaned.split('\n')]

        # 8. Remove leading/trailing blank lines
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()

        return '\n'.join(lines)

    def _detect_boilerplate(self, pages: List[str]) -> Set[str]:
        """
        Step 2: Find lines appearing on X% of pages (cross-page boilerplate).

        These are typically:
        - Navigation menus
        - Footer links
        - Site-wide CTAs

        Args:
            pages: List of cleaned page contents

        Returns:
            Set of boilerplate lines
        """
        if len(pages) <= 1:
            return set()

        # Build frequency map: line -> set of page indices
        line_to_pages = defaultdict(set)

        for page_idx, page in enumerate(pages):
            for line in page.split('\n'):
                stripped = line.strip()
                # Only track substantial lines (>10 chars to exclude structural elements)
                if len(stripped) > 10:
                    line_to_pages[stripped].add(page_idx)

        # Identify boilerplate: lines appearing on >= threshold % of pages
        min_pages = len(pages) * self.boilerplate_threshold
        boilerplate_lines = {
            line for line, page_set in line_to_pages.items()
            if len(page_set) >= min_pages
        }

        return boilerplate_lines

    def _remove_boilerplate(self, pages: List[str], boilerplate: Set[str]) -> List[str]:
        """
        Step 3: Remove boilerplate lines from individual pages.

        Args:
            pages: List of page contents
            boilerplate: Set of boilerplate lines to remove

        Returns:
            List of pages with boilerplate removed
        """
        if not boilerplate:
            return pages

        deduplicated_pages = []

        for page in pages:
            lines = page.split('\n')
            unique_lines = [
                line for line in lines
                if line.strip() not in boilerplate
            ]
            deduplicated_pages.append('\n'.join(unique_lines))

        return deduplicated_pages

    def _deduplicate_content(self, content: str) -> str:
        """
        Step 5: Remove exact duplicate paragraphs/lines.

        Finds content that appears multiple times and keeps only first occurrence.
        Works for ANY website - no site-specific patterns.

        Args:
            content: Combined content from all pages

        Returns:
            Deduplicated content
        """
        lines = content.split('\n')

        # Count occurrences of substantial lines (>min_content_length chars)
        line_counts = Counter()
        for line in lines:
            stripped = line.strip()
            if len(stripped) >= self.min_content_length:
                line_counts[stripped] += 1

        # Identify high-frequency duplicates
        duplicates = {
            line for line, count in line_counts.items()
            if count >= self.duplicate_min_occurrences
        }

        self.stats['duplicates_removed'] = sum(
            count - 1 for line, count in line_counts.items()
            if line in duplicates
        )

        # Keep only first occurrence of each duplicate
        seen = set()
        deduplicated_lines = []

        for line in lines:
            stripped = line.strip()

            if stripped in duplicates:
                if stripped not in seen:
                    deduplicated_lines.append(line)
                    seen.add(stripped)
                # else: skip duplicate occurrence
            else:
                deduplicated_lines.append(line)

        return '\n'.join(deduplicated_lines)

    def _filter_low_value_lines(self, content: str) -> str:
        """
        Step 6: Remove navigation items, empty bullets, and repetitive CTAs.

        Filters:
        - Lines with 1-3 words that appear 20+ times (navigation)
        - Common CTA patterns ("Learn more", "Back to menu", etc.)
        - Very short lines that are high-frequency

        Generic pattern-based filtering for ANY website.

        Args:
            content: Content string

        Returns:
            Filtered content
        """
        lines = content.split('\n')

        # Count occurrences to identify navigation
        line_freq = Counter(line.strip() for line in lines)

        # Common CTA patterns (universal across websites)
        cta_patterns = [
            r'^(learn|read|see|view|discover|explore|get|find|contact|book)\s+(more|now|us|started)$',
            r'^(back to|return to|go to)\s+\w+$',
            r'^(previous|next|continue|submit|send|sign up|log in)$',
        ]
        cta_regex = re.compile('|'.join(cta_patterns), re.IGNORECASE)

        filtered_lines = []
        removed_count = 0

        for line in lines:
            stripped = line.strip()
            word_count = len(stripped.split())

            # Keep if:
            # 1. Has 4+ words (likely real content)
            # 2. Has meaningful punctuation (sentences)
            # 3. Is a URL/heading marker (starts with # or contains ://)

            # Remove if:
            # 1. Empty or just whitespace
            # 2. Only punctuation/symbols
            # 3. 1-3 words AND appears >= threshold times (navigation)
            # 4. Matches CTA pattern

            if not stripped:
                continue

            if stripped in ['*', '**', '***', '####', '-', '--', '___']:
                removed_count += 1
                continue

            # Keep URL markers and headings
            if stripped.startswith('#') or '://' in stripped:
                filtered_lines.append(line)
                continue

            # Remove high-frequency short lines (navigation)
            if word_count <= 3 and line_freq[stripped] >= self.short_line_freq_threshold:
                removed_count += 1
                continue

            # Remove CTA patterns
            if word_count <= 4 and cta_regex.match(stripped):
                removed_count += 1
                continue

            filtered_lines.append(line)

        self.stats['lowvalue_lines_removed'] = removed_count

        return '\n'.join(filtered_lines)

    def _create_output(self, boilerplate: Set[str], content: str) -> str:
        """
        Step 7: Format final output with boilerplate section.

        Args:
            boilerplate: Set of boilerplate lines
            content: Page-specific content

        Returns:
            Formatted final output
        """
        if not boilerplate:
            return content

        output = "# SITE-WIDE ELEMENTS (Navigation, Footer, etc.)\n\n"

        # Sort and limit boilerplate display to avoid bloat
        sorted_boilerplate = sorted(boilerplate)[:100]  # Max 100 boilerplate lines
        output += '\n'.join(sorted_boilerplate)

        output += "\n\n" + "="*80 + "\n\n"
        output += "# PAGE-SPECIFIC CONTENT\n\n"
        output += content

        return output

    def get_stats(self) -> Dict[str, Any]:
        """
        Get consolidation statistics.

        Returns:
            Dict with character counts and reduction percentages
        """
        original = self.stats['original_chars']
        final = self.stats['after_lowvalue']

        return {
            **self.stats,
            'final_chars': final,
            'total_reduction_pct': round(100 * (1 - final / original), 1) if original > 0 else 0,
            'structural_reduction_pct': round(100 * (1 - self.stats['after_structural'] / original), 1) if original > 0 else 0,
            'boilerplate_reduction_pct': round(100 * (1 - self.stats['after_boilerplate'] / self.stats['after_structural']), 1) if self.stats['after_structural'] > 0 else 0,
            'duplicate_reduction_pct': round(100 * (1 - self.stats['after_duplicates'] / self.stats['after_boilerplate']), 1) if self.stats['after_boilerplate'] > 0 else 0,
            'lowvalue_reduction_pct': round(100 * (1 - self.stats['after_lowvalue'] / self.stats['after_duplicates']), 1) if self.stats['after_duplicates'] > 0 else 0,
        }
