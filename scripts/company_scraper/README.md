# ITAD Company Research Pipeline

Two-step pipeline for validating company URLs and analyzing websites using Crawl4AI + Deepseek.

## Pipeline Overview

```
Step 1: url_validator.py          Step 2: scraper.py
┌─────────────────────┐          ┌─────────────────────────┐
│ input/companies.csv  │          │ companies_validated.csv  │
│ (name + URL)         │──────▶   │ (validated URLs)         │──────▶  outputs/
│                      │          │                          │         ├── *_analysis.json
│ - HTTP validation    │          │ - BFS deep crawl         │         ├── *_raw.md
│ - URL research       │          │ - Deepseek analysis      │         ├── combined_analysis.json
│ - LinkedIn search    │          │ - Vendor qualification   │         └── progress.json
└─────────────────────┘          └─────────────────────────┘
```

## Quick Start

### 1. Setup

```bash
# API keys (add to .env or export)
export DEEPSEEK_API_KEY='your-key'
export SERPER_API_KEY='your-key'   # or BRAVE_API_KEY
```

### 2. Prepare Input

Create `input/companies.csv` (tab or comma separated):

```
Company,URL
Acme Recycling,https://acmerecycling.com
Beta IT Solutions,
```

Companies without URLs will be researched automatically.

### 3. Run the Pipeline

```bash
# Step 1: Validate and research URLs
python3 url_validator.py

# Step 2: Crawl and analyze websites
python3 scraper.py
```

## Step 1: URL Validator (`url_validator.py`)

Validates existing company URLs and researches missing ones.

**Phases:**
1. **HTTP Validation** — Fetches each URL and checks if content matches the company name
2. **URL Research** — Uses Brave/Serper search to find URLs for missing or low-confidence companies
3. **LinkedIn Search** — Finds LinkedIn company pages for all companies

**Input:** `input/companies.csv`
**Output:** `input/companies_validated.csv` (TSV with confidence scores, status, LinkedIn URLs)

**Search Providers** (configured in `config.yaml`):
- `serper` — Google results via Serper.dev API
- `brave` — Brave Search API (also checks OpenClaw config)

## Step 2: Website Scraper (`scraper.py`)

Crawls validated company websites and analyzes them with Deepseek AI.

**What it does:**
1. BFS deep-crawls each company website (configurable depth/pages)
2. Filters and consolidates content to reduce token usage
3. Sends content to Deepseek for structured analysis
4. Evaluates vendor fit for ITAD procurement

**Input:** `input/companies_validated.csv` (output of Step 1)
**Output:** `outputs/<timestamp>_<N>_companies_scraped/` containing per-company files and `combined_analysis.json`

### Checkpoint/Resume

The scraper tracks progress in `progress.json` inside the output directory. If it crashes, resume from where it left off:

```bash
# Fresh run
python3 scraper.py

# Resume after crash — skips already-completed companies
python3 scraper.py --resume <output_dir_name>

# Example:
python3 scraper.py --resume 20260209_143000_457_companies_scraped
```

**Monitor progress** while running:

```bash
# Watch progress file
watch -n 10 cat outputs/20260209_*/progress.json

# Quick summary
python3 -c "
import json, sys
p = json.load(open(sys.argv[1]))
print(f'Progress: {p[\"completed\"]}/{p[\"total_companies\"]} | Failed: {p[\"failed\"]} | Status: {p[\"status\"]}')
" outputs/20260209_*/progress.json
```

**How it works:**
- `progress.json` is updated after every company (atomic writes)
- On resume, companies with `status: success` AND an existing `_analysis.json` file are skipped
- Partially-processed companies (crashed mid-crawl) are re-processed
- Aggregation (`combined_analysis.json`) is regenerated from all files on disk

## Configuration

All settings are in `config.yaml`:

| Section | Controls |
|---------|----------|
| `url_validator` | Batch size, timeouts, confidence thresholds, search provider |
| `deepseek` | Model, temperature, max tokens, output language |
| `crawl_settings` | Max pages, max depth, strategy |
| `content_filter` | Pruning threshold for reducing content sent to LLM |
| `content_consolidation` | Cross-page deduplication and boilerplate removal |
| `system_prompt` | What Deepseek extracts and how it evaluates vendors |
| `output_schema` | JSON structure of the analysis output |

### Key Settings

```yaml
deepseek:
  output_language: "English"   # Language for LLM output

crawl_settings:
  max_pages: 30                # Pages per website
  max_depth: 3                 # Link depth

content_filter:
  enabled: true
  threshold: 0.15              # Lower = keep more content
```

## Output Files

Per company:
- `{name}_analysis.json` — Structured analysis (vendor qualification, contacts, services)
- `{name}_raw.md` — Full crawled markdown content
- `{name}_filtered.md` — Content after filtering (if enabled)
- `{name}_deepseek_input.json` — Exact payload sent to Deepseek
- `{name}_summary.txt` — Human-readable summary

Aggregated:
- `combined_analysis.json` — All companies in one file
- `progress.json` — Run status and per-company tracking

## File Structure

```
company_scraper/
├── config.yaml              # All configuration
├── url_validator.py         # Step 1: URL validation + research
├── scraper.py               # Step 2: Crawl + analyze
├── content_consolidator.py  # Content deduplication engine
├── input/
│   ├── companies.csv            # Your input (company names + URLs)
│   └── companies_validated.csv  # Output of Step 1, input to Step 2
├── outputs/
│   └── YYYYMMDD_HHMMSS_N_companies_scraped/
│       ├── progress.json
│       ├── combined_analysis.json
│       └── {company}_*.json/md/txt
├── logs/                    # Timestamped log files
└── README.md
```

## Tips

- **For large runs (100+ companies)**: Run inside `tmux` and use `--resume` if it crashes
- **Reduce costs**: Keep `content_filter.enabled: true` — saves 70-90% on Deepseek tokens
- **Spot-check first**: Run on 5-10 companies before doing a full batch
- **Check logs**: Detailed logs in `logs/` directory for debugging
