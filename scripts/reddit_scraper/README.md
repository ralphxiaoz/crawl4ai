# Reddit Scraper with Optional LLM Analysis

A general-purpose Reddit scraper that uses **web scraping (no API credentials required)** with optional LLM-powered analysis. Built on top of [Crawl4AI](https://github.com/unclecode/crawl4ai).

## Features

- **No API Keys Required**: Uses web scraping via crawl4ai (default)
- **Flexible Search**: Search any subreddit with keyword filtering
- **Hybrid Approach**: Web scraping (default) or Reddit API (optional)
- **Comment Extraction**: Get post content + comment threads
- **LLM Analysis**: Optional AI-powered summarization and insights
- **Customizable Output**: Define your own analysis schema
- **Multiple Formats**: JSON, Markdown summaries, and combined results

## Why Web Scraping?

Reddit's API now requires manual approval and has restrictive policies. **Web scraping works immediately** without any credentials or approval process.

## Installation

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

**Note**: `praw` (Reddit API library) is optional. If you only use web scraping, you don't need it.

### 2. Configure LLM API Key (Optional)

If you want LLM analysis, set up your API key:

```bash
cp .env.example .env
nano .env  # or use your preferred editor
```

Add your LLM API key:
```bash
# For LLM analysis (optional)
DEEPSEEK_API_KEY=your_deepseek_key_here
# or
OPENAI_API_KEY=your_openai_key_here

# Reddit API credentials (OPTIONAL - only if using strategy: "api")
# REDDIT_CLIENT_ID=your_client_id_here
# REDDIT_CLIENT_SECRET=your_client_secret_here
# REDDIT_USER_AGENT=RedditScraper/1.0 by YourUsername
```

### 3. Customize Configuration

Edit `config.yaml` to set your search parameters:

```yaml
reddit:
  subreddit: "ProductManagement"  # Your target subreddit
  keywords:  # Filter posts by these keywords (OR logic)
    - "AI"
    - "LLM"
  limit: 10  # Number of posts to fetch
```

## Usage

### Quick Start (No Credentials Needed!)

Run with default configuration (web scraping mode):

```bash
cd /path/to/reddit_scraper
python reddit_scraper.py
```

That's it! The scraper will:
1. Scrape r/ProductManagement for posts with "AI" or "LLM" in the title
2. Extract post content and comments via web scraping
3. Save results to `outputs/` directory

**No Reddit API credentials required!**

### Advanced Configuration

#### Search Settings

```yaml
reddit:
  subreddit: "MachineLearning"
  keywords: ["GPT", "transformers", "neural networks"]
  sort_by: "hot"  # hot, new, top, rising, controversial
  time_filter: "week"  # hour, day, week, month, year, all
  limit: 20
  extract_comments: true
  max_comments: 100
```

#### LLM Analysis

Enable LLM analysis to automatically summarize and extract insights:

```yaml
llm:
  enabled: true
  provider: "deepseek/deepseek-chat"  # or "openai/gpt-4"

  system_prompt: |
    Analyze this Reddit post and extract key insights...

  output_schema:
    summary:
      type: "string"
      description: "Brief summary of the post"
    key_points:
      type: "array"
      description: "Main takeaways"
```

#### Custom Analysis Schema

Define your own fields to extract:

```yaml
output_schema:
  # Example: Product feedback analysis
  product_mentioned:
    type: "string"
    description: "Which product is being discussed?"

  user_sentiment:
    type: "string"
    description: "positive, negative, or neutral"

  pain_points:
    type: "array"
    description: "User pain points mentioned"

  feature_requests:
    type: "array"
    description: "Requested features or improvements"

  competitive_products:
    type: "array"
    description: "Competing products mentioned"
```

## Output Structure

Each run creates a timestamped directory:

```
outputs/
└── 20260105_143022_ProductManagement/
    ├── abc123_Post_Title_post.json          # Raw post data
    ├── abc123_Post_Title_comments.json      # Comment thread
    ├── abc123_Post_Title_analysis.json      # LLM analysis
    ├── abc123_Post_Title_summary.md         # Human-readable summary
    └── combined_results.json                # All posts combined
```

### Output Files

- **`*_post.json`**: Complete post metadata (title, author, score, etc.)
- **`*_comments.json`**: All comments with scores and timestamps
- **`*_analysis.json`**: LLM-extracted insights (if enabled)
- **`*_summary.md`**: Readable summary combining post + analysis
- **`combined_results.json`**: All posts in a single file

## Examples

### Example 1: Product Research

Track mentions of your product:

```yaml
reddit:
  subreddit: "SaaS"
  keywords: ["project management", "task tracking"]
  limit: 50

llm:
  enabled: true
  output_schema:
    tools_mentioned:
      type: "array"
      description: "Project management tools discussed"
    pain_points:
      type: "array"
      description: "User frustrations mentioned"
    pricing_discussion:
      type: "boolean"
      description: "Is pricing mentioned?"
```

### Example 2: Trend Analysis

Monitor AI/ML discussions:

```yaml
reddit:
  subreddit: "MachineLearning"
  keywords: ["LLM", "GPT", "Claude", "ChatGPT"]
  sort_by: "top"
  time_filter: "week"
  limit: 30

llm:
  enabled: true
  output_schema:
    main_topic:
      type: "string"
      description: "Primary topic of discussion"
    technical_level:
      type: "string"
      description: "beginner, intermediate, or advanced"
    trending_techniques:
      type: "array"
      description: "ML techniques mentioned"
```

### Example 3: Customer Support Intelligence

Monitor customer complaints:

```yaml
reddit:
  subreddit: "techsupport"
  keywords: ["Windows 11", "update", "bug"]
  sort_by: "new"
  limit: 100

llm:
  enabled: true
  output_schema:
    issue_type:
      type: "string"
      description: "Category of the issue"
    severity:
      type: "string"
      description: "low, medium, or high"
    workarounds_mentioned:
      type: "array"
      description: "Solutions suggested in comments"
```

## Customization

### Without LLM Analysis

To just scrape posts without AI analysis:

```yaml
llm:
  enabled: false
```

This saves API costs and runs faster.

### Scraping Strategy

```yaml
scraper:
  strategy: "web"      # Use web scraping (NO credentials needed) - DEFAULT
  # strategy: "api"    # Use Reddit API (requires credentials and approval)
  # strategy: "hybrid" # Try API first, fallback to web scraping
```

**Recommendation**: Use `"web"` (default) to avoid Reddit's API approval process and get started immediately.

## Troubleshooting

### "PRAW not available"

**This is OK!** PRAW is only needed if using `strategy: "api"`. If you're using web scraping (default), you can ignore this warning.

If you want to use the API:
```bash
pip install praw
```

### "crawl4ai not available"

Web scraping requires crawl4ai. Install it:
```bash
pip install crawl4ai beautifulsoup4
```

### "Reddit API credentials not found"

**This is OK!** Credentials are only needed if using `strategy: "api"`. Web scraping (default) doesn't need any credentials.

### "No posts found matching criteria"

- Try removing keyword filters (set `keywords: []`)
- Check your subreddit name (no `r/` prefix)
- Try different sort methods (`hot`, `new`, `top`)

### LLM Analysis Fails

- Verify your LLM API key is set correctly
- Check the `provider` matches your API key (e.g., `deepseek/deepseek-chat` needs `DEEPSEEK_API_KEY`)
- Ensure you have enough API credits

## Technical Notes

### Rate Limits

Reddit API has rate limits:
- Without authentication: 60 requests/minute
- With authentication: Higher limits (varies)

The scraper respects these limits automatically via PRAW.

### Comment Depth

Reddit comments are threaded. The scraper flattens them by default. Use `comment.depth` in the output to reconstruct the tree.

### Content Consolidation

The original company_scraper includes content consolidation (deduplication) for multi-page websites. This is disabled by default for Reddit since posts are typically unique.

Enable if needed:
```yaml
content:
  consolidation_enabled: true
```

## License

This scraper is part of the Crawl4AI project. See the main repository for license details.

## Support

For issues or questions:
1. Check the [Crawl4AI documentation](https://github.com/unclecode/crawl4ai)
2. Reddit API docs: https://www.reddit.com/dev/api
3. PRAW documentation: https://praw.readthedocs.io/

## Credits

Built on:
- [Crawl4AI](https://github.com/unclecode/crawl4ai) - Web crawling and LLM integration
- [PRAW](https://praw.readthedocs.io/) - Python Reddit API Wrapper
