#!/usr/bin/env python3
"""
General-Purpose Reddit Scraper with Optional LLM Analysis

This script scrapes Reddit posts and comments using a hybrid approach:
1. Reddit API (PRAW) - preferred method
2. Web scraping (crawl4ai) - fallback when API is unavailable

Features:
- Subreddit search with keyword filtering
- Post + comment extraction
- Optional LLM analysis and summarization
- Flexible output formats (JSON, markdown, summaries)

Configuration is loaded from config.yaml.
Results are saved with timestamps in the outputs/ directory.
"""

import asyncio
import json
import os
import yaml
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field, create_model

# Reddit API
try:
    import praw
    PRAW_AVAILABLE = True
except ImportError:
    PRAW_AVAILABLE = False
    print("⚠️  Warning: praw not installed. API scraping will not work.")
    print("Install with: pip install praw")

# Web scraping fallback
try:
    from crawl4ai import (
        AsyncWebCrawler,
        BrowserConfig,
        CrawlerRunConfig,
        LLMConfig,
        CacheMode
    )
    from crawl4ai.extraction_strategy import LLMExtractionStrategy
    CRAWL4AI_AVAILABLE = True
except ImportError:
    CRAWL4AI_AVAILABLE = False
    print("⚠️  Warning: crawl4ai not installed. Web scraping fallback will not work.")
    print("Install with: pip install crawl4ai")


def setup_logging(script_dir: Path) -> logging.Logger:
    """Setup logging to both file and console"""
    log_dir = script_dir / "logs"
    log_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"reddit_scraper_{timestamp}.log"

    logger = logging.getLogger("reddit_scraper")
    logger.setLevel(logging.DEBUG)
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
                if line and not line.startswith('#'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        if key not in os.environ:
                            os.environ[key] = value


def load_config(config_path: str = "config.yaml") -> Dict[str, Any]:
    """Load configuration from YAML file"""
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    return config


def create_pydantic_model_from_schema(schema: Dict[str, Any]) -> type[BaseModel]:
    """Create a Pydantic model from the YAML schema definition"""
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

    return create_model('RedditAnalysis', **fields)


def matches_keywords(text: str, keywords: List[str]) -> bool:
    """Check if text contains any of the keywords (case-insensitive)"""
    if not keywords:
        return True  # No keywords = match all

    text_lower = text.lower()
    return any(keyword.lower() in text_lower for keyword in keywords)


class WebScraperClient:
    """Reddit web scraper using crawl4ai"""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.crawler = None

        if not CRAWL4AI_AVAILABLE:
            self.logger.warning("crawl4ai not available. Web scraper cannot be initialized.")

    def is_available(self) -> bool:
        """Check if web scraper is available"""
        return CRAWL4AI_AVAILABLE

    async def get_posts(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Fetch posts from Reddit via web scraping"""
        if not self.is_available():
            raise Exception("crawl4ai not available for web scraping")

        reddit_config = config['reddit']
        subreddit_name = reddit_config['subreddit']
        keywords = reddit_config.get('keywords', [])
        sort_by = reddit_config.get('sort_by', 'hot')
        limit = reddit_config.get('limit', 10)

        self.logger.info(f"Web scraping r/{subreddit_name}")
        self.logger.debug(f"Sort: {sort_by}, Limit: {limit}, Keywords: {keywords}")

        # Build Reddit URL
        url = f"https://old.reddit.com/r/{subreddit_name}/"
        if sort_by == 'new':
            url += 'new/'
        elif sort_by == 'top':
            time_filter = reddit_config.get('time_filter', 'month')
            url += f'top/?t={time_filter}'
        elif sort_by == 'rising':
            url += 'rising/'
        elif sort_by == 'controversial':
            time_filter = reddit_config.get('time_filter', 'month')
            url += f'controversial/?t={time_filter}'
        # 'hot' is default, no extra path needed

        self.logger.debug(f"Scraping URL: {url}")

        try:
            browser_config = BrowserConfig(headless=True, verbose=False)
            run_config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                wait_for_images=False
            )

            async with AsyncWebCrawler(config=browser_config) as crawler:
                print(f"🔍 Scraping r/{subreddit_name}...")
                result = await crawler.arun(url=url, config=run_config)

                if not result.success:
                    raise Exception(f"Failed to scrape page: {result.error_message}")

                # Parse posts from HTML
                posts = self._parse_posts_from_html(result.html, keywords, limit)

                self.logger.info(f"Scraped {len(posts)} posts matching criteria")
                return posts

        except Exception as e:
            self.logger.error(f"Error during web scraping: {e}")
            raise

    def _parse_posts_from_html(self, html: str, keywords: List[str], limit: int) -> List[Dict[str, Any]]:
        """Parse Reddit posts from HTML"""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, 'html.parser')
        posts = []

        # Find all post containers (old Reddit format)
        post_elements = soup.find_all('div', {'data-type': 'link'})

        for element in post_elements:
            if len(posts) >= limit:
                break

            try:
                # Extract post data
                title_elem = element.find('a', class_='title')
                if not title_elem:
                    continue

                title = title_elem.get_text(strip=True)

                # Filter by keywords
                if keywords and not matches_keywords(title, keywords):
                    continue

                # Extract other data
                post_id = element.get('data-fullname', '').replace('t3_', '')
                permalink = element.get('data-permalink', '')
                url = element.get('data-url', '')

                # Score
                score_elem = element.find('div', class_='score unvoted')
                score = 0
                if score_elem:
                    score_text = score_elem.get('title', '0')
                    try:
                        score = int(score_text)
                    except:
                        score = 0

                # Author
                author_elem = element.find('a', class_='author')
                author = author_elem.get_text(strip=True) if author_elem else '[deleted]'

                # Number of comments
                comments_elem = element.find('a', class_='comments')
                num_comments = 0
                if comments_elem:
                    comments_text = comments_elem.get_text(strip=True)
                    match = re.search(r'(\d+)', comments_text)
                    if match:
                        num_comments = int(match.group(1))

                # Timestamp (harder to get from old Reddit, so we'll skip for now)
                created_utc = datetime.now().timestamp()

                post_data = {
                    'id': post_id,
                    'title': title,
                    'author': author,
                    'created_utc': created_utc,
                    'score': score,
                    'upvote_ratio': 0.0,  # Not easily available from old Reddit
                    'num_comments': num_comments,
                    'url': url,
                    'permalink': f"https://reddit.com{permalink}" if permalink else url,
                    'selftext': '',  # Need to fetch individual post to get this
                    'is_self': 'self.' in url,
                    'link_flair_text': None,
                    'subreddit': element.get('data-subreddit', ''),
                    'permalink_path': permalink  # Keep for comment fetching
                }

                posts.append(post_data)
                self.logger.debug(f"Parsed post: {title[:50]}...")

            except Exception as e:
                self.logger.warning(f"Error parsing post element: {e}")
                continue

        return posts

    async def get_comments(self, post_data: Dict[str, Any], max_comments: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetch comments for a post via web scraping"""
        try:
            permalink = post_data.get('permalink_path') or post_data.get('permalink')
            if not permalink:
                self.logger.warning("No permalink available for comment scraping")
                return []

            # Use old.reddit.com for easier parsing
            if permalink.startswith('http'):
                url = permalink.replace('www.reddit.com', 'old.reddit.com')
            else:
                url = f"https://old.reddit.com{permalink}"

            self.logger.debug(f"Fetching comments from: {url}")

            browser_config = BrowserConfig(headless=True, verbose=False)
            run_config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                wait_for_images=False
            )

            async with AsyncWebCrawler(config=browser_config) as crawler:
                result = await crawler.arun(url=url, config=run_config)

                if not result.success:
                    self.logger.warning(f"Failed to fetch comments: {result.error_message}")
                    return []

                # Also update selftext from the post page
                comments = self._parse_comments_from_html(result.html, max_comments)

                # Update post selftext
                selftext = self._parse_selftext_from_html(result.html)
                post_data['selftext'] = selftext

                self.logger.debug(f"Parsed {len(comments)} comments")
                return comments

        except Exception as e:
            self.logger.error(f"Error fetching comments: {e}")
            return []

    def _parse_selftext_from_html(self, html: str) -> str:
        """Parse post selftext from HTML"""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, 'html.parser')

        # Find the post content
        content_elem = soup.find('div', class_='usertext-body')
        if content_elem:
            return content_elem.get_text(strip=True)

        return ''

    def _parse_comments_from_html(self, html: str, max_comments: Optional[int] = None) -> List[Dict[str, Any]]:
        """Parse comments from post page HTML"""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, 'html.parser')
        comments = []

        # Find all comment containers
        comment_elements = soup.find_all('div', class_='comment')

        for element in comment_elements:
            if max_comments and len(comments) >= max_comments:
                break

            try:
                # Extract comment data
                body_elem = element.find('div', class_='usertext-body')
                if not body_elem:
                    continue

                body = body_elem.get_text(strip=True)

                # Author
                author_elem = element.find('a', class_='author')
                author = author_elem.get_text(strip=True) if author_elem else '[deleted]'

                # Score
                score_elem = element.find('span', class_='score unvoted')
                score = 0
                if score_elem:
                    score_text = score_elem.get('title', '0')
                    try:
                        score = int(score_text)
                    except:
                        score = 0

                # Comment ID
                comment_id = element.get('data-fullname', '').replace('t1_', '')

                comment_data = {
                    'id': comment_id,
                    'author': author,
                    'body': body,
                    'score': score,
                    'created_utc': datetime.now().timestamp(),
                    'is_submitter': False,
                    'depth': 0  # Simplified - not calculating depth
                }

                comments.append(comment_data)

            except Exception as e:
                self.logger.warning(f"Error parsing comment: {e}")
                continue

        return comments


class RedditAPIClient:
    """Reddit API client using PRAW"""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.reddit = None

        if not PRAW_AVAILABLE:
            self.logger.warning("PRAW not available. API client cannot be initialized.")
            return

        # Initialize Reddit API client
        client_id = os.getenv('REDDIT_CLIENT_ID')
        client_secret = os.getenv('REDDIT_CLIENT_SECRET')
        user_agent = os.getenv('REDDIT_USER_AGENT', 'RedditScraper/1.0')

        if not client_id or not client_secret:
            self.logger.warning("Reddit API credentials not found in environment variables")
            return

        try:
            self.reddit = praw.Reddit(
                client_id=client_id,
                client_secret=client_secret,
                user_agent=user_agent
            )
            self.logger.info("Reddit API client initialized successfully")
        except Exception as e:
            self.logger.error(f"Failed to initialize Reddit API client: {e}")
            self.reddit = None

    def is_available(self) -> bool:
        """Check if API client is available"""
        return self.reddit is not None

    def get_posts(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Fetch posts from Reddit API based on config"""
        if not self.is_available():
            raise Exception("Reddit API client not available")

        reddit_config = config['reddit']
        subreddit_name = reddit_config['subreddit']
        keywords = reddit_config.get('keywords', [])
        sort_by = reddit_config.get('sort_by', 'hot')
        time_filter = reddit_config.get('time_filter', 'month')
        limit = reddit_config.get('limit', 10)

        self.logger.info(f"Fetching posts from r/{subreddit_name}")
        self.logger.debug(f"Sort: {sort_by}, Limit: {limit}, Keywords: {keywords}")

        try:
            subreddit = self.reddit.subreddit(subreddit_name)
            posts = []

            # Get posts based on sort method
            if sort_by == 'hot':
                submissions = subreddit.hot(limit=limit * 3)  # Fetch more to filter by keywords
            elif sort_by == 'new':
                submissions = subreddit.new(limit=limit * 3)
            elif sort_by == 'top':
                submissions = subreddit.top(time_filter=time_filter, limit=limit * 3)
            elif sort_by == 'rising':
                submissions = subreddit.rising(limit=limit * 3)
            elif sort_by == 'controversial':
                submissions = subreddit.controversial(time_filter=time_filter, limit=limit * 3)
            else:
                self.logger.warning(f"Unknown sort method: {sort_by}, defaulting to hot")
                submissions = subreddit.hot(limit=limit * 3)

            # Filter by keywords and convert to dict
            for submission in submissions:
                if len(posts) >= limit:
                    break

                if matches_keywords(submission.title, keywords):
                    post_data = {
                        'id': submission.id,
                        'title': submission.title,
                        'author': str(submission.author) if submission.author else '[deleted]',
                        'created_utc': submission.created_utc,
                        'score': submission.score,
                        'upvote_ratio': submission.upvote_ratio,
                        'num_comments': submission.num_comments,
                        'url': submission.url,
                        'permalink': f"https://reddit.com{submission.permalink}",
                        'selftext': submission.selftext,
                        'is_self': submission.is_self,
                        'link_flair_text': submission.link_flair_text,
                        'subreddit': str(submission.subreddit),
                        'submission_obj': submission  # Keep for comment fetching
                    }
                    posts.append(post_data)
                    self.logger.debug(f"Added post: {submission.title[:50]}...")

            self.logger.info(f"Fetched {len(posts)} posts matching criteria")
            return posts

        except Exception as e:
            self.logger.error(f"Error fetching posts: {e}")
            raise

    def get_comments(self, submission_obj, max_comments: Optional[int] = None,
                     comment_sort: str = "best") -> List[Dict[str, Any]]:
        """Fetch comments for a submission"""
        try:
            submission_obj.comment_sort = comment_sort
            submission_obj.comments.replace_more(limit=0)  # Skip "load more comments"

            comments = []
            for comment in submission_obj.comments.list():
                if max_comments and len(comments) >= max_comments:
                    break

                if hasattr(comment, 'body'):  # Skip MoreComments objects
                    comment_data = {
                        'id': comment.id,
                        'author': str(comment.author) if comment.author else '[deleted]',
                        'body': comment.body,
                        'score': comment.score,
                        'created_utc': comment.created_utc,
                        'is_submitter': comment.is_submitter,
                        'depth': comment.depth if hasattr(comment, 'depth') else 0
                    }
                    comments.append(comment_data)

            self.logger.debug(f"Fetched {len(comments)} comments")
            return comments

        except Exception as e:
            self.logger.error(f"Error fetching comments: {e}")
            return []


async def analyze_post_with_llm(
    post_data: Dict[str, Any],
    comments: List[Dict[str, Any]],
    config: Dict[str, Any],
    logger: logging.Logger
) -> Optional[Dict[str, Any]]:
    """Analyze a Reddit post with LLM"""
    llm_config_dict = config['llm']

    if not llm_config_dict.get('enabled', False):
        logger.debug("LLM analysis disabled")
        return None

    logger.info(f"Analyzing post with LLM: {post_data['title'][:50]}...")

    # Combine post and comments into context
    context = f"# Post Title: {post_data['title']}\n\n"
    context += f"**Author:** {post_data['author']}\n"
    context += f"**Score:** {post_data['score']} | **Comments:** {post_data['num_comments']}\n\n"

    if post_data.get('selftext'):
        context += f"## Post Content:\n{post_data['selftext']}\n\n"

    if comments:
        context += f"## Top Comments ({len(comments)} total):\n\n"
        for i, comment in enumerate(comments[:20], 1):  # Top 20 comments
            context += f"**Comment {i}** (Score: {comment['score']}):\n"
            context += f"{comment['body']}\n\n"

    # Create temporary HTML for crawl4ai
    temp_html = f"<html><body><pre>{context}</pre></body></html>"

    try:
        # Setup LLM extraction strategy
        provider = llm_config_dict['provider']
        api_token_key = None

        if 'openai' in provider:
            api_token_key = 'OPENAI_API_KEY'
        elif 'deepseek' in provider:
            api_token_key = 'DEEPSEEK_API_KEY'
        elif 'anthropic' in provider:
            api_token_key = 'ANTHROPIC_API_KEY'

        api_token = os.getenv(api_token_key) if api_token_key else None

        if not api_token:
            logger.warning(f"API token for {provider} not found. Skipping LLM analysis.")
            return None

        llm_config = LLMConfig(
            provider=provider,
            api_token=api_token,
            temperature=llm_config_dict.get('temperature', 0.7),
            max_tokens=llm_config_dict.get('max_tokens', 4000)
        )

        # Create Pydantic model from schema
        AnalysisModel = create_pydantic_model_from_schema(llm_config_dict['output_schema'])

        extraction_strategy = LLMExtractionStrategy(
            llm_config=llm_config,
            schema=llm_config_dict['output_schema'],
            instruction=llm_config_dict['system_prompt']
        )

        # Run analysis
        browser_config = BrowserConfig(headless=True, verbose=False)
        analysis_config = CrawlerRunConfig(
            extraction_strategy=extraction_strategy,
            cache_mode=CacheMode.BYPASS
        )

        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(
                url=f"raw:{temp_html}",
                config=analysis_config
            )

            if result.success:
                analysis_data = json.loads(result.extracted_content)
                logger.info("LLM analysis completed successfully")
                return analysis_data
            else:
                logger.error(f"LLM analysis failed: {result.error_message}")
                return None

    except Exception as e:
        logger.error(f"Error during LLM analysis: {e}")
        return None


async def process_posts(
    posts: List[Dict[str, Any]],
    client,  # Can be RedditAPIClient or WebScraperClient
    config: Dict[str, Any],
    output_dir: Path,
    logger: logging.Logger
) -> List[Dict[str, Any]]:
    """Process all posts: extract comments and optionally analyze with LLM"""
    results = []

    for i, post_data in enumerate(posts, 1):
        print(f"\n{'='*80}")
        print(f"Processing post {i}/{len(posts)}: {post_data['title']}")
        print(f"{'='*80}\n")

        logger.info(f"Processing post {i}/{len(posts)}: {post_data['title'][:50]}...")

        # Extract comments if enabled
        comments = []
        if config['reddit'].get('extract_comments', True):
            max_comments = config['reddit'].get('max_comments')

            # Handle different client types
            if isinstance(client, RedditAPIClient) and client.is_available():
                comment_sort = config['reddit'].get('comment_sort', 'best')
                submission_obj = post_data.get('submission_obj')
                if submission_obj:
                    comments = client.get_comments(
                        submission_obj,
                        max_comments=max_comments,
                        comment_sort=comment_sort
                    )
                    print(f"📝 Extracted {len(comments)} comments via API")
            elif isinstance(client, WebScraperClient) and client.is_available():
                comments = await client.get_comments(post_data, max_comments=max_comments)
                print(f"📝 Extracted {len(comments)} comments via web scraping")

        # Remove submission object from data (not JSON serializable)
        post_data_clean = {k: v for k, v in post_data.items() if k not in ['submission_obj', 'permalink_path']}

        # LLM analysis
        llm_analysis = None
        if config['llm'].get('enabled', False):
            print(f"\n🤖 Analyzing with LLM...")
            llm_analysis = await analyze_post_with_llm(post_data_clean, comments, config, logger)

            if llm_analysis:
                print(f"✓ LLM analysis complete")
            else:
                print(f"✗ LLM analysis failed")

        # Save individual post files
        post_id = post_data['id']
        base_filename = f"{post_id}_{post_data['title'][:30]}"
        # Clean filename
        base_filename = re.sub(r'[^\w\s-]', '', base_filename).strip().replace(' ', '_')

        # Save raw post data
        if config['output'].get('save_raw_posts', True):
            raw_file = output_dir / f"{base_filename}_post.json"
            with open(raw_file, 'w', encoding='utf-8') as f:
                json.dump(post_data_clean, f, indent=2, ensure_ascii=False)
            logger.debug(f"Saved raw post: {raw_file.name}")

        # Save comments
        if config['output'].get('save_comments', True) and comments:
            comments_file = output_dir / f"{base_filename}_comments.json"
            with open(comments_file, 'w', encoding='utf-8') as f:
                json.dump(comments, f, indent=2, ensure_ascii=False)
            logger.debug(f"Saved comments: {comments_file.name}")

        # Save LLM analysis
        if config['output'].get('save_llm_analysis', True) and llm_analysis:
            analysis_file = output_dir / f"{base_filename}_analysis.json"
            with open(analysis_file, 'w', encoding='utf-8') as f:
                json.dump(llm_analysis, f, indent=2, ensure_ascii=False)
            logger.debug(f"Saved LLM analysis: {analysis_file.name}")

        # Save human-readable summary
        if config['output'].get('save_summary', True):
            summary_file = output_dir / f"{base_filename}_summary.md"
            with open(summary_file, 'w', encoding='utf-8') as f:
                f.write(f"# {post_data['title']}\n\n")
                f.write(f"**Author:** {post_data['author']} | **Score:** {post_data['score']} | **Comments:** {post_data['num_comments']}\n\n")
                f.write(f"**URL:** {post_data['permalink']}\n\n")

                if post_data.get('selftext'):
                    f.write(f"## Post Content\n\n{post_data['selftext']}\n\n")

                if llm_analysis:
                    f.write(f"## LLM Analysis\n\n")
                    for key, value in llm_analysis.items():
                        if value:
                            f.write(f"**{key.replace('_', ' ').title()}:** {value}\n\n")

                if comments:
                    f.write(f"## Top Comments\n\n")
                    for comment in comments[:10]:
                        f.write(f"- **{comment['author']}** (Score: {comment['score']}): {comment['body'][:200]}...\n\n")

            logger.debug(f"Saved summary: {summary_file.name}")

        # Build result
        result = {
            'post': post_data_clean,
            'comments': comments,
            'llm_analysis': llm_analysis,
            'files': {
                'post': f"{base_filename}_post.json",
                'comments': f"{base_filename}_comments.json" if comments else None,
                'analysis': f"{base_filename}_analysis.json" if llm_analysis else None,
                'summary': f"{base_filename}_summary.md"
            }
        }

        results.append(result)

    return results


async def main():
    """Main execution function"""
    script_dir = Path(__file__).parent

    # Setup logging
    logger = setup_logging(script_dir)
    logger.info("="*80)
    logger.info("Reddit Scraper - Starting")
    logger.info("="*80)

    # Load .env file
    logger.debug("Loading environment variables")
    load_env_file()

    # Load configuration
    config_file = script_dir / "config.yaml"
    logger.debug(f"Loading configuration from {config_file}")

    if not config_file.exists():
        print(f"❌ Error: config.yaml not found at {config_file}")
        logger.error(f"config.yaml not found")
        return

    config = load_config(config_file)
    logger.info("Configuration loaded successfully")

    # Setup output directory
    base_output_dir = script_dir / config['output']['output_dir']
    base_output_dir.mkdir(exist_ok=True)

    # Create run-specific subfolder
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    subreddit = config['reddit']['subreddit']
    output_dir = base_output_dir / f"{run_timestamp}_{subreddit}"
    output_dir.mkdir(exist_ok=True)

    logger.info(f"Output directory: {output_dir}")

    print(f"\n{'='*80}")
    print(f"Reddit Scraper")
    print(f"{'='*80}")
    print(f"\nConfiguration:")
    print(f"  Subreddit: r/{config['reddit']['subreddit']}")
    print(f"  Keywords: {config['reddit'].get('keywords', 'All posts')}")
    print(f"  Sort: {config['reddit'].get('sort_by', 'hot')}")
    print(f"  Limit: {config['reddit'].get('limit', 10)} posts")
    print(f"  Strategy: {config['scraper'].get('strategy', 'hybrid')}")
    print(f"  LLM Analysis: {'Enabled' if config['llm'].get('enabled') else 'Disabled'}")
    print(f"  Output: {output_dir}")

    # Initialize clients
    reddit_client = RedditAPIClient(logger)
    web_scraper = WebScraperClient(logger)

    # Fetch posts
    posts = []
    strategy = config['scraper'].get('strategy', 'hybrid')
    active_client = None

    try:
        if strategy in ['api', 'hybrid'] and reddit_client.is_available():
            print(f"\n🔍 Fetching posts via Reddit API...")
            logger.info("Using Reddit API to fetch posts")
            posts = reddit_client.get_posts(config)
            active_client = reddit_client
            print(f"✓ Fetched {len(posts)} posts via API")

        elif strategy == 'web' or (strategy == 'hybrid' and not reddit_client.is_available()):
            if not web_scraper.is_available():
                print(f"❌ Error: crawl4ai not available for web scraping")
                logger.error("crawl4ai not available")
                print("Please install crawl4ai: pip install crawl4ai")
                return

            print(f"\n🌐 Fetching posts via web scraping...")
            logger.info("Using web scraping to fetch posts")
            posts = await web_scraper.get_posts(config)
            active_client = web_scraper
            print(f"✓ Fetched {len(posts)} posts via web scraping")

        else:
            print(f"❌ Unknown strategy: {strategy}")
            logger.error(f"Unknown strategy: {strategy}")
            return

    except Exception as e:
        print(f"❌ Error fetching posts: {e}")
        logger.error(f"Error fetching posts: {e}")
        import traceback
        traceback.print_exc()
        return

    if not posts:
        print("❌ No posts found matching criteria")
        logger.warning("No posts found")
        return

    # Process posts
    print(f"\n{'='*80}")
    print(f"Processing {len(posts)} posts...")
    print(f"{'='*80}")

    results = await process_posts(posts, active_client, config, output_dir, logger)

    # Save combined output
    if config['output'].get('save_combined', True):
        combined_file = output_dir / "combined_results.json"
        with open(combined_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved combined results: {combined_file.name}")
        print(f"\n💾 Saved combined results: {combined_file.name}")

    # Print summary
    print(f"\n{'='*80}")
    print(f"PROCESSING COMPLETE")
    print(f"{'='*80}\n")

    print(f"✓ Processed: {len(results)} posts")
    print(f"📁 Output directory: {output_dir}")

    analyzed_count = sum(1 for r in results if r.get('llm_analysis'))
    if analyzed_count > 0:
        print(f"🤖 LLM analyzed: {analyzed_count} posts")

    logger.info("="*80)
    logger.info(f"Session complete. Processed {len(results)} posts")
    logger.info("="*80)


if __name__ == "__main__":
    asyncio.run(main())
