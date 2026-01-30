# Crawl4AI + Deepseek Company Website Analyzer

Automatically crawl company websites and extract structured information using Deepseek AI.

## Features

- ✅ **Adaptive Crawling**: Intelligently explores websites to find relevant content
- ✅ **Deepseek Analysis**: Uses Deepseek AI to extract structured information
- ✅ **Content Filtering**: Reduces API costs by 70-90% by filtering out navigation, ads, footers
- ✅ **YAML Configuration**: Easy to customize what you want to extract
- ✅ **Dual Output**: Get both JSON (structured) and human-readable summaries
- ✅ **Timestamped Files**: Track multiple crawls over time
- ✅ **Raw Content Preserved**: Keep original crawled content for re-analysis
- ✅ **Cost Tracking**: Real-time logging of content reduction and estimated savings

## Content Filtering (Cost Optimization)

**NEW**: The script now includes intelligent content filtering that can reduce your Deepseek API costs by **70-90%** while keeping all important information!

### How It Works

When content filtering is enabled, the script automatically:
1. Removes navigation menus, footers, headers, ads, and social widgets
2. Filters out low-value content (short snippets, link farms)
3. Keeps only the "meaty" content that matters
4. Sends the filtered content to Deepseek instead of everything

### Cost Savings Example

**Without filtering:**
- Full page content: 200,000 characters → ~50,000 tokens
- Cost per site: ~$0.014
- 100 sites: ~$1.40

**With filtering (default settings):**
- Filtered content: 40,000 characters → ~10,000 tokens
- Cost per site: ~$0.003
- 100 sites: ~$0.30
- **Savings: 79%** 💰

### Configuring Content Filtering

Edit the `content_filter` section in `config.yaml`:

```yaml
content_filter:
  # Enable/disable filtering
  enabled: true

  # Threshold (0.0-1.0): How aggressive to filter
  # Lower = keep more content, Higher = keep less
  threshold: 0.48

  # Threshold type
  threshold_type: "dynamic"  # or "fixed"

  # Minimum words per block
  min_word_threshold: 10
```

### Threshold Guide

| Threshold | Effect | Use Case |
|-----------|--------|----------|
| **0.3-0.4** | Lenient - keeps most content | When you want to be safe and keep everything |
| **0.48** | Balanced (default) - removes obvious junk | Most websites, general use |
| **0.5-0.6** | Moderate - stricter filtering | Clean blogs/articles |
| **0.7+** | Very strict - only highest quality | Research, maximum cost savings |

### When to Adjust

**If you're missing information:**
- Lower the threshold: `threshold: 0.35`
- Or disable filtering: `enabled: false`

**If you want more savings:**
- Raise the threshold: `threshold: 0.6`
- The script will show you the reduction % after each crawl

**To remove short text blocks:**
- Increase min_word_threshold: `min_word_threshold: 15`

### Monitoring Savings

The script automatically logs your savings:

```
  📉 Content filtering: 215,437 → 43,087 chars (80.0% reduction)
  💰 Estimated savings: ~43,087 tokens (~$0.0121)
```

Check the log files in `logs/` for detailed statistics.

## Quick Start

### 1. Setup

Make sure you have your Deepseek API key:

```bash
export DEEPSEEK_API_KEY='your-deepseek-api-key-here'
```

To make it permanent, add to your `~/.bashrc` or `~/.zshrc`:
```bash
echo 'export DEEPSEEK_API_KEY="your-key-here"' >> ~/.bashrc
source ~/.bashrc
```

### 2. Configure What to Extract

Edit `config.yaml` to customize:

- **system_prompt**: Define what information you want Deepseek to find
- **output_schema**: Define the structure of the JSON output
- **crawl_settings**: Adjust how many pages to crawl, depth, strategy, etc.

Example: To extract different information, just edit the `system_prompt` section:

```yaml
system_prompt: |
  Extract pricing information and customer testimonials from this website.
  Include:
  - Pricing tiers or plans
  - Customer reviews or testimonials
  - Case studies mentioned
```

### 3. Add Websites to Crawl

Edit `websites.txt` and add one website per line:

```
https://company1.com
https://company2.com
www.company3.com
company4.com
```

(The script automatically adds `https://` if missing)

### 4. Run the Script

```bash
cd my_scripts
python3 crawl_with_deepseek.py
```

Or make it executable:
```bash
chmod +x crawl_with_deepseek.py
./crawl_with_deepseek.py
```

## Output Files

For each website, three files are created in the `outputs/` directory:

1. **`{timestamp}_{domain}_raw.md`**
   - Full crawled content in Markdown format
   - All pages combined
   - Useful for manual review or re-analysis

2. **`{timestamp}_{domain}_analysis.json`**
   - Structured JSON data extracted by Deepseek
   - Follows the schema defined in `config.yaml`
   - Easy to parse programmatically

3. **`{timestamp}_{domain}_summary.txt`**
   - Human-readable summary
   - Includes key contact information
   - Great for quick review

## Configuration Options

### Crawl Strategies

**Statistical** (Default - Recommended):
- Fast and efficient
- Works without extra API calls
- Great for finding specific information like contact details
- Uses keyword matching and statistical analysis

```yaml
crawl_settings:
  strategy: "statistical"
```

**Embedding** (Semantic):
- Uses AI embeddings for semantic understanding
- Better for conceptual/abstract queries
- Slower and may use more API calls
- Can use local models or API-based embeddings

```yaml
crawl_settings:
  strategy: "embedding"
```

### Crawl Settings Explained

```yaml
crawl_settings:
  max_pages: 30           # Maximum pages to crawl per website
  max_depth: 3            # How many link-clicks deep to go (1=homepage only)
  confidence_threshold: 0.7  # Stop when 70% confident (0.0-1.0)
  top_k_links: 5          # Number of best links to follow per page
```

**Recommendations:**
- **Quick scan**: max_pages=10, max_depth=2
- **Normal**: max_pages=30, max_depth=3 (default)
- **Thorough**: max_pages=50, max_depth=5

### Deepseek Settings

```yaml
deepseek:
  model: "deepseek/deepseek-chat"  # Model to use
  temperature: 0.7      # Creativity (0.0=deterministic, 1.0=creative)
  max_tokens: 4000      # Maximum response length
```

## Example Use Cases

### Use Case 1: Find Contact Information

Edit `config.yaml`:
```yaml
system_prompt: |
  Extract all contact information:
  - Email addresses (support, sales, contact, info)
  - Phone numbers
  - Physical addresses
  - Contact form URLs

output_schema:
  emails: {type: array}
  phones: {type: array}
  addresses: {type: array}
```

### Use Case 2: Research Company Services

Edit `config.yaml`:
```yaml
system_prompt: |
  Identify:
  - What services does this company offer?
  - What industries do they serve?
  - What technologies do they use?
  - Who are their typical customers?
```

### Use Case 3: Competitive Analysis

Edit `config.yaml`:
```yaml
system_prompt: |
  Extract competitive intelligence:
  - Company size and revenue (if public)
  - Key differentiators or unique selling points
  - Target market
  - Pricing model (subscription, one-time, custom, etc.)
  - Customer testimonials or case studies
```

## Troubleshooting

### Error: "DEEPSEEK_API_KEY environment variable not set"

Set your API key:
```bash
export DEEPSEEK_API_KEY='your-api-key'
```

### Error: "config.yaml not found"

Make sure you're running the script from the `my_scripts/` directory:
```bash
cd my_scripts
python3 crawl_with_deepseek.py
```

### Error: "No websites found in websites.txt"

Edit `websites.txt` and add at least one website URL.

### Crawling Takes Too Long

Reduce the crawl settings in `config.yaml`:
```yaml
crawl_settings:
  max_pages: 10    # Reduce from 30
  max_depth: 2     # Reduce from 3
```

### Not Finding All Contact Info

Try these adjustments:

1. **Increase crawl depth**:
   ```yaml
   max_pages: 50
   max_depth: 4
   ```

2. **Be more specific in prompt**:
   ```yaml
   system_prompt: |
     Search carefully for ALL email addresses including:
     - General contact emails
     - Department-specific emails (sales@, support@, hr@)
     - Personal emails of team members
     - Emails in footer, contact pages, about pages
   ```

## Tips for Best Results

1. **Be Specific in Prompts**: The more detailed your system_prompt, the better the extraction
2. **Use Appropriate Strategy**: Statistical for specific info, Embedding for research
3. **Check Raw Content**: If extraction misses something, check the `_raw.md` file
4. **Adjust Thresholds**: Lower confidence_threshold if stopping too early
5. **Monitor Token Usage**: Watch the console output for API usage

## File Structure

```
my_scripts/
├── config.yaml                    # Configuration file (edit this!)
├── websites.txt                   # List of websites to crawl
├── crawl_with_deepseek.py        # Main script
├── outputs/                       # All results saved here
│   ├── 20250128_120000_company1_raw.md
│   ├── 20250128_120000_company1_analysis.json
│   ├── 20250128_120000_company1_summary.txt
│   └── ...
└── README_DEEPSEEK.md            # This file
```

## Advanced: Programmatic Usage

You can also import and use the functions programmatically:

```python
from crawl_with_deepseek import load_config, crawl_and_analyze_website
from crawl4ai import AsyncWebCrawler, BrowserConfig

config = load_config("config.yaml")

async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as crawler:
    result = await crawl_and_analyze_website(
        crawler,
        "https://example.com",
        config,
        Path("outputs")
    )
    print(result['analysis'])
```

## Support

For issues with:
- **Crawl4ai**: Check https://github.com/unclecode/crawl4ai
- **Deepseek API**: Check https://www.deepseek.com/
- **This script**: Review error messages and adjust config.yaml settings

Happy crawling! 🚀
