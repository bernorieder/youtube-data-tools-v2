# ytdt — YouTube Data Tools (Python)

A Python rewrite of [YouTube Data Tools](https://ytdt.digitalmethods.net/) as a
library, designed to be wrapped by both a CLI (included) and a future web
frontend. It extracts channel, video, and comment data from the
[YouTube Data API v3](https://developers.google.com/youtube/v3/) for research
purposes.

## Why the rewrite is faster and more reliable

- **Batched requests**: channel and video metadata is fetched 50 ids per API
  call instead of one call per id (up to 50× fewer requests and quota units).
- **Multithreading**: playlists, comment threads, reply pages, searches, and
  crawl levels are fetched in parallel (`max_workers`, default 8).
- **Inline replies**: comment threads whose replies fit in the
  `commentThreads` response need no extra `comments.list` requests at all.
- **Cheap channel resolution**: `@handles` and `/user/` URLs resolve via
  `channels.list` (1 quota unit) instead of `search.list` (100 units).
- **Structured retries**: transient API errors back off exponentially and
  retry; quota exhaustion raises `QuotaExceededError`; missing/private items
  raise typed `SkippableError`s instead of crashing a run.
- **Quota accounting**: every client tracks `call_count` and an estimate of
  `quota_used`.
- **Bug fixes**: `durationSec` now includes hours/days; the misspelled
  `defaultLAudioLanguage` column is `defaultAudioLanguage`; pseudonymization
  also applies to network labels and to @mentions inside comment text
  (hashed consistently with author names, so mentions stay linkable to the
  hashed `authorName` column). Bulk comment downloads
  (`fetch_comments_bulk`, or `ytdt video-comments` with several ids) are
  always pseudonymized.
- **Shorts detection**: the video list gains an `isShort` column
  (`yes`/`no`, empty = not determined), resolved **API-only** against each
  channel's unlisted Shorts system playlist (`UUSH` + channel id suffix)
  via plain `playlistItems.list` — no scraping, exact regardless of
  duration, 1 quota unit per 50 Shorts per channel. Note the playlist id
  scheme is undocumented and could stop working without notice. Detection
  is opt-in (`--shorts-detection`, or the `detect_shorts` library
  function); `--shorts-only`/`--longform-only` filter the
  output, and `--shorts-only --channel` reads the Shorts playlists
  directly so a channel's complete Shorts archive costs only a few units.
  Because a video's Shorts status never changes, results are cached
  permanently in a small SQLite file (`~/.ytdt/cache.db`, override with
  `YTDT_CACHE_DIR`, skip with `--no-cache`): reruns and resumed
  collections only check videos never seen before. Timely data (counts,
  comments, search results) is deliberately never cached. A "no" for a
  video younger than a week is not persisted, since the Shorts playlist
  can lag behind brand-new uploads.
- **Channel-owner flag**: every comment carries `isChannelOwner` ("yes"/"no"),
  marking comments made by the channel the video belongs to; user network
  nodes carry the same attribute. The flag survives pseudonymization, so
  the creator's role stays visible in bulk downloads.
- **Nested replies**: YouTube supports reply chains deeper than one level,
  but the API flattens them under the top-level comment. The library
  recovers the real structure from the @mention YouTube inserts into nested
  replies (`resolve_reply_targets`), so `isReplyToId`/`isReplyToName` and
  the comment interaction network reflect who actually answered whom; the
  `threadId` column preserves the flat grouping. Unresolvable mentions fall
  back to the top-level comment. Reply-count columns: `totalReplyCount`
  mirrors the API verbatim (thread-wide count on top-level comments, empty
  on replies), `inferredReplyCount` holds the reconstructed number of
  direct replies among retrieved comments, and `inferredLevel` the depth in
  the reconstructed tree (0 = top level, 1 = direct reply, 2 = reply to a
  reply, …), following mention chains as deep as they go — note this can
  exceed the threading depth YouTube's interface records (set
  `MAX_INFERRED_LEVEL` to saturate levels at a fixed depth instead). The API also often drops the space between
  an inserted mention and the comment text (in every text format); when the
  mention matches a thread participant, the exported text is normalized to
  what the UI shows (space restored, invisible prefix characters removed).

Output stays compatible with the original tool: the same CSV columns
(row order = search rank, `position` column) and Gephi-compatible `.gdf`
network files.

## Install

```bash
cd ytdt-py
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Set your API key once:

```bash
export YTDT_API_KEY="..."   # or pass api_key= / --api-key explicitly
```

## Library usage

```python
from ytdt import YouTubeClient, search_videos, fetch_videos, write_table

client = YouTubeClient()  # reads YTDT_API_KEY

# Video List module
ids = search_videos(client, "climate change", order="relevance")
videos = fetch_videos(client, ids)
write_table(videos, "videolist.csv")

# Video Comments module
from ytdt import fetch_comments, interaction_network, pseudonymize

comments = pseudonymize(fetch_comments(client, "aXnaHh40xnM"))
write_table(comments, "comments.csv", position=False)
interaction_network(comments).write_gdf("commentnet.gdf")

# Channel Network module
from ytdt import crawl_channel_network

graph = crawl_channel_network(client, ["UCtxGqPJPPi8ptAzB029jpYA"], depth=1)
graph.write_gdf("channelnet.gdf")
```

All module functions take the client as first argument, so a frontend can
create one client per job, attach an `on_progress` callback, and enforce its
own limits.

| Original module | Library functions |
| --- | --- |
| Channel Info | `channel_info`, `resolve_channel_id` |
| Channel List | `search_channels`, `fetch_channels` |
| Channel Network | `crawl_channel_network` |
| Video List | `search_videos`, `channel_video_ids`, `playlist_video_ids`, `fetch_videos`, `channel_details`, `detect_shorts`, `channel_shorts_ids`, `cotag_network`, `shared_tag_network` |
| Trending Videos *(new)* | `trending_videos`, `video_categories` |
| Video Comments | `video_info`, `fetch_comments`, `fetch_comments_bulk`, `author_counts`, `interaction_network`, `pseudonymize` |
| Video Co-comment Network | `cocomment_networks` |

## CLI usage

```bash
ytdt channel-info @BernhardRiederAmsterdam
ytdt channel-list --query "digital methods"
ytdt channel-network --ids UC...,UC... --depth 1
ytdt video-list --channel UCtxGqPJPPi8ptAzB029jpYA --cotag
ytdt video-list --channel UCX6OQ3DkcsbYNE6H8uQQuVA --shorts-only   # a channel's complete Shorts archive
ytdt video-list --query "climate" --shorts-detection               # fill isShort for search results
ytdt video-list --query "climate" --day-mode --published-after 2024-01-01T00:00:00Z --published-before 2024-01-08T00:00:00Z
ytdt trending-videos --region US,NL,DE --cotag             # one chart per region, one combined CSV
ytdt trending-videos --region FR --category 10 --limit 50  # French music chart (--list-categories for ids)
ytdt video-comments aXnaHh40xnM --limit 500 --pseudonymize
ytdt video-comments id1,id2,id3      # bulk: one CSV + user network, always pseudonymized
ytdt cocomment-network --query "elections" --max-comments 200
```

Progress goes to stderr, result file paths to stdout, so runs compose well in
scripts. `--output-dir` picks the target directory.

## Web interface

A NiceGUI-based web frontend lives in `src/ytdt_web/`, deliberately split
in two layers so the framework can be swapped: `jobs.py` is
framework-agnostic (module metadata, parameter handling, background
execution, progress state, output files) and `app.py` is the only file
that imports NiceGUI. One page per module; forms are "narrative" (pick a
source, then its parameters appear), progress is shown live, and result
files download from the page. Every finished run also produces a
plain-text report (module, run time, parameters, row/node counts per
file, and re-download links served under `/files/`) that can be copied
to the clipboard or downloaded as a .txt file.

```bash
pip install -e ".[web]"
export YTDT_API_KEY="..."
ytdt-web                       # http://127.0.0.1:8080
```

`YTDT_WEB_HOST`/`YTDT_WEB_PORT` configure the bind address,
`YTDT_WEB_OUTPUT` the directory for produced files (default
`ytdt_web_output/`).

## Tests

```bash
python -m pytest
```

The suite runs entirely offline against a fake API client.

## Citation

Rieder, Bernhard (2015). YouTube Data Tools (Version 2.0) [Software].
Available from https://ytdt.digitalmethods.net.

## License

GPL-3.0-or-later — see [LICENSE](LICENSE).
