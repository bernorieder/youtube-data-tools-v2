"""Command-line interface: one subcommand per YTDT module.

Examples::

    ytdt channel-info @BernhardRiederAmsterdam
    ytdt channel-list --query "digital methods"
    ytdt video-list --channel UCtxGqPJPPi8ptAzB029jpYA --cotag
    ytdt video-list --query "climate change"
    ytdt trending-videos --region US,DE,FR --cotag
    ytdt video-comments aXnaHh40xnM --limit 500 --pseudonymize
    ytdt video-comments id1,id2,id3            # bulk: always pseudonymized
    ytdt cocomment-network --ids id1,id2,id3 --max-comments 200
    ytdt channel-network --ids UC...,UC... --depth 1

The API key is read from ``--api-key`` or the ``YTDT_API_KEY`` environment
variable. Output files are written to ``--output-dir`` (default: current
directory) with timestamped names.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import __version__, modules
from .cache import FactCache
from .client import YouTubeClient
from .models import Channel, Video
from .resolve import resolve_channel_ids
from .errors import YTDTError
from .tabular import write_table
from .utils import unique


def _timestamp() -> str:
    return datetime.now().strftime("%Y_%m_%d-%H_%M_%S")


def _progress(desc: str, done: int, total: int) -> None:
    sys.stderr.write(f"\r{desc}: {done}/{total}")
    if done >= total:
        sys.stderr.write("\n")
    sys.stderr.flush()


def _parse_ids(args: argparse.Namespace) -> list[str]:
    ids: list[str] = []
    if getattr(args, "ids", None):
        ids += [part.strip() for part in args.ids.split(",") if part.strip()]
    if getattr(args, "ids_file", None):
        text = Path(args.ids_file).read_text(encoding="utf-8")
        ids += [part.strip() for part in text.replace(",", "\n").splitlines() if part.strip()]
    return unique(ids)


def _outfile(args: argparse.Namespace, name: str) -> Path:
    directory = Path(args.output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / name


def _report(path: Path) -> None:
    print(path)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--api-key", help="YouTube API key (default: YTDT_API_KEY env var)")
    parser.add_argument("--output-dir", default=".", help="directory for output files")
    parser.add_argument("--workers", type=int, default=8, help="parallel request threads")
    parser.add_argument("--quiet", action="store_true", help="suppress progress output")


def _add_search_options(parser: argparse.ArgumentParser, *, orders: list[str]) -> None:
    parser.add_argument("--order", choices=orders, default="relevance")
    parser.add_argument("--language", help="ISO 639-1 relevance language")
    parser.add_argument("--region-code", help="ISO 3166-1 alpha-2 region code")
    parser.add_argument(
        "--published-after", help="YYYY-MM-DD or RFC 3339 UTC timestamp (YYYY-MM-DDThh:mm:ssZ)"
    )
    parser.add_argument("--published-before", help="YYYY-MM-DD or RFC 3339 UTC timestamp")


def _add_shorts_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--shorts-detection",
        action="store_true",
        help="fill the isShort column via the channels' Shorts system playlists (API-only)",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--shorts-only", action="store_true", help="keep only Shorts (implies --shorts-detection)"
    )
    group.add_argument(
        "--longform-only",
        action="store_true",
        help="keep only regular videos (implies --shorts-detection)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="skip the persistent Shorts-status cache (~/.ytdt/cache.db)",
    )


VIDEO_ORDERS = ["relevance", "date", "rating", "title", "viewCount"]
CHANNEL_ORDERS = VIDEO_ORDERS + ["videoCount"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ytdt", description="YouTube Data Tools")
    parser.add_argument("--version", action="version", version=f"ytdt {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("channel-info", help="full API record for one channel")
    p.add_argument("channel", help="channel id, URL, or @handle")
    _add_common(p)

    p = sub.add_parser("channel-list", help="tabular channel data from a search or ids")
    p.add_argument("--query", help="channel search query")
    p.add_argument("--ids", help="comma-separated channel ids, URLs, or @handles")
    p.add_argument("--ids-file", help="file with channel ids/URLs/@handles (one per line or comma-separated)")
    _add_search_options(p, orders=CHANNEL_ORDERS)
    _add_common(p)

    p = sub.add_parser("channel-network", help="crawl featured-channel/subscription network")
    p.add_argument("--query", help="channel search query for seeds")
    p.add_argument("--ids", help="comma-separated seed channel ids, URLs, or @handles")
    p.add_argument("--ids-file", help="file with seed channel ids/URLs/@handles")
    p.add_argument("--depth", type=int, default=1, choices=[0, 1, 2, 3])
    _add_search_options(p, orders=CHANNEL_ORDERS)
    _add_common(p)

    p = sub.add_parser("video-list", help="tabular video data from a channel/playlist/search/ids")
    p.add_argument(
        "--channel", help="comma-separated channel ids, URLs, or @handles (uploads are listed)"
    )
    p.add_argument("--playlist", help="playlist id")
    p.add_argument("--query", help="video search query")
    p.add_argument("--ids", help="comma-separated video ids")
    p.add_argument("--ids-file", help="file with video ids")
    p.add_argument("--day-mode", action="store_true", help="one search per day of the timeframe")
    p.add_argument("--location", help="lat,lng point, e.g. 37.42307,-122.08427")
    p.add_argument("--location-radius", help="radius, e.g. 10km")
    p.add_argument(
        "--cotag",
        action="store_true",
        help="also write tag networks (.gexf): co-occurring tags, and videos linked by shared tags",
    )
    p.add_argument(
        "--channeldetails",
        action="store_true",
        help="add channel_ columns (publishedAt, daysActive, country, view/subscriber/videoCount) per video",
    )
    _add_shorts_options(p)
    _add_search_options(p, orders=VIDEO_ORDERS)
    _add_common(p)

    p = sub.add_parser("trending-videos", help="most-popular (trending) chart per region")
    p.add_argument(
        "--region",
        default="US",
        help="comma-separated ISO 3166-1 region codes (one chart per region)",
    )
    p.add_argument("--category", help="restrict the chart to one video category id")
    p.add_argument("--limit", type=int, help="max videos per region (API caps charts at ~200)")
    p.add_argument(
        "--list-categories",
        action="store_true",
        help="print the region's video category ids and exit",
    )
    p.add_argument(
        "--cotag",
        action="store_true",
        help="also write tag networks (.gexf): co-occurring tags, and videos linked by shared tags",
    )
    p.add_argument(
        "--channeldetails",
        action="store_true",
        help="add channel_ columns (publishedAt, daysActive, country, view/subscriber/videoCount) per video",
    )
    _add_shorts_options(p)
    _add_common(p)

    p = sub.add_parser(
        "video-comments",
        help="comments and user interaction network for one or more videos",
    )
    p.add_argument("videos", nargs="?", help="video id(s), comma separated")
    p.add_argument("--ids-file", help="file with video ids (one per line or comma-separated)")
    p.add_argument("--limit", type=int, help="max top-level comments per video (relevance-ranked)")
    p.add_argument(
        "--pseudonymize",
        action="store_true",
        help="hash author names and comment ids (always on for more than one video)",
    )
    _add_common(p)

    p = sub.add_parser("cocomment-network", help="video network based on shared commenters")
    p.add_argument("--query", help="video search query")
    p.add_argument("--ids", help="comma-separated video ids")
    p.add_argument("--ids-file", help="file with video ids")
    p.add_argument("--max-comments", type=int, default=100, help="top-level comments per video")
    _add_search_options(p, orders=VIDEO_ORDERS)
    _add_common(p)

    return parser


def _shorts_mode(args: argparse.Namespace) -> str | None:
    """isShort value to keep ("yes"/"no"), or None to keep everything."""
    if args.shorts_only:
        return "yes"
    if args.longform_only:
        return "no"
    return None


def _search_kwargs(args: argparse.Namespace) -> dict:
    return {
        "order": args.order,
        "language": args.language,
        "region_code": args.region_code,
        "published_after": args.published_after,
        "published_before": args.published_before,
    }


def run(args: argparse.Namespace) -> None:
    client = YouTubeClient(
        api_key=args.api_key,
        max_workers=args.workers,
        on_progress=None if args.quiet else _progress,
    )
    stamp = _timestamp()

    if args.command == "channel-info":
        info = modules.channel_info(client, args.channel)
        print(json.dumps(info, indent=2, ensure_ascii=False))
        playlists = modules.channel_playlists(client, info["id"])
        if playlists:
            path = _outfile(args, f"channelplaylists_{info['id']}_{stamp}.csv")
            _report(write_table(playlists, path, position=False))

    elif args.command == "channel-list":
        missing: list[str] = []
        ids = resolve_channel_ids(client, _parse_ids(args), missing=missing)
        if args.query:
            ids += modules.search_channels(client, args.query, **_search_kwargs(args))
        if not ids and not missing:
            raise SystemExit("Provide --query, --ids, or --ids-file.")
        channels = modules.fetch_channels(client, unique(ids))
        found = {channel.channel_id for channel in channels}
        missing += [cid for cid in unique(ids) if cid not in found]
        rows = channels + [Channel.missing(ref) for ref in missing]
        path = _outfile(args, f"channellist{len(channels)}_{stamp}.csv")
        _report(write_table(rows, path))

    elif args.command == "channel-network":
        # unresolvable seeds are skipped: the network output has no place for markers
        seeds = resolve_channel_ids(client, _parse_ids(args), missing=[])
        source_parts = [f"seeds{len(seeds)}"] if seeds else []
        if args.query:
            found = modules.search_channels(client, args.query, **_search_kwargs(args))
            seeds += found
            source_parts.append(f"search{len(found)}")
        if not seeds:
            raise SystemExit("Provide --query, --ids, or --ids-file.")
        graph = modules.crawl_channel_network(client, unique(seeds), depth=args.depth)
        desc = "_".join(source_parts)
        path = _outfile(args, f"channelnet_{desc}_nodes{len(graph.nodes)}_{stamp}.gexf")
        _report(graph.write_gexf(path))

    elif args.command == "video-list":
        ids = _parse_ids(args)
        source = "seeds" if ids else ""
        source_parts = [f"seeds{len(ids)}"] if ids else []
        known_shorts: set[str] = set()
        missing_channels: list[str] = []
        if args.channel:
            channels = resolve_channel_ids(
                client,
                [c.strip() for c in args.channel.split(",") if c.strip()],
                missing=missing_channels,
            )
            if args.shorts_only:
                # read the Shorts playlists directly instead of fetching
                # (and then discarding) the channels' long-form uploads
                id_lists = client.map(
                    lambda c: modules.channel_shorts_ids(client, c), channels, desc="channel shorts"
                )
                known_shorts.update(vid for lst in id_lists for vid in lst)
                ids += [vid for lst in id_lists for vid in lst]
            else:
                ids += modules.channel_video_ids(client, channels)
            source = source or "channel"
            source_parts.append(
                f"channel_{channels[0]}" if len(channels) == 1 else f"channels{len(channels)}"
            )
        if args.playlist:
            ids += modules.playlist_video_ids(client, args.playlist)
            source = source or "playlist"
            source_parts.append(f"playlist_{args.playlist.strip()}")
        if args.query or args.location:
            found = modules.search_videos(
                client,
                args.query,
                day_mode=args.day_mode,
                location=args.location,
                location_radius=args.location_radius,
                **_search_kwargs(args),
            )
            ids += found
            source = source or "search"
            source_parts.append(f"search{len(found)}")
        if not ids and not missing_channels:
            raise SystemExit("Provide --channel, --playlist, --query, --ids, or --ids-file.")
        videos = modules.fetch_videos(client, unique(ids))
        want = _shorts_mode(args)
        for video in videos:
            if video.video_id in known_shorts:
                video.is_short = "yes"
        if args.shorts_detection or want:
            modules.detect_shorts(
                client, videos, cache=None if args.no_cache else FactCache()
            )
        if want:
            videos = [v for v in videos if v.is_short == want]
        details = modules.channel_details(client, videos) if args.channeldetails else None
        rows: list = videos
        if details is not None:
            rows = [{**v.to_row(), **details.get(v.channel_id, {})} for v in videos]
        # marker rows for unresolvable channel refs (CSV only, never the networks)
        rows = list(rows) + [
            Video(title=f"[channel not found: {ref}]") for ref in missing_channels
        ]
        path = _outfile(args, f"videolist_{source}{len(videos)}_{stamp}.csv")
        _report(write_table(rows, path))
        if args.cotag:
            desc = "_".join(source_parts)
            tag_graph = modules.cotag_network(videos)
            _report(tag_graph.write_gexf(_outfile(
                args, f"videolist_tagnet_{desc}_nodes{len(tag_graph.nodes)}_{stamp}.gexf"
            )))
            video_graph = modules.shared_tag_network(videos, channel_details=details)
            _report(video_graph.write_gexf(_outfile(
                args, f"videolist_sharedtagnet_{desc}_nodes{len(video_graph.nodes)}_{stamp}.gexf"
            )))

    elif args.command == "trending-videos":
        regions = unique(r.strip().upper() for r in args.region.split(",") if r.strip())
        if not regions:
            raise SystemExit("Provide at least one region code via --region.")
        if args.list_categories:
            categories = modules.video_categories(client, region_code=regions[0])
            for category_id, label in categories.items():
                print(f"{category_id}\t{label}")
        else:
            per_region = client.map(
                lambda region: modules.trending_videos(
                    client, region_code=region, category_id=args.category, limit=args.limit
                ),
                regions,
                desc="regions",
            )
            # rank = position on the region's chart, kept even when
            # --shorts-only/--longform-only filter rows out afterwards
            entries = [
                (region, rank, video)
                for region, vids in zip(regions, per_region)
                for rank, video in enumerate(vids, start=1)
            ]
            want = _shorts_mode(args)
            if args.shorts_detection or want:
                modules.detect_shorts(
                    client,
                    [video for _, _, video in entries],
                    cache=None if args.no_cache else FactCache(),
                )
            if want:
                entries = [e for e in entries if e[2].is_short == want]
            # the same video can trend in several regions: one row per
            # region in the CSV, but one node per video in the networks
            videos_by_id = {video.video_id: video for _, _, video in entries}
            videos = list(videos_by_id.values())
            details = modules.channel_details(client, videos) if args.channeldetails else None
            rows = [
                {
                    "region": region,
                    "rank": rank,
                    **video.to_row(),
                    **(details or {}).get(video.channel_id, {}),
                }
                for region, rank, video in entries
            ]
            desc = "-".join(regions) + (f"_cat{args.category}" if args.category else "")
            path = _outfile(args, f"trending_{desc}_videos{len(rows)}_{stamp}.csv")
            _report(write_table(rows, path, position=False))
            if args.cotag:
                tag_graph = modules.cotag_network(videos)
                _report(tag_graph.write_gexf(_outfile(
                    args, f"trending_tagnet_{desc}_nodes{len(tag_graph.nodes)}_{stamp}.gexf"
                )))
                video_graph = modules.shared_tag_network(videos, channel_details=details)
                _report(video_graph.write_gexf(_outfile(
                    args, f"trending_sharedtagnet_{desc}_nodes{len(video_graph.nodes)}_{stamp}.gexf"
                )))

    elif args.command == "video-comments":
        ids = []
        if args.videos:
            ids += [part.strip() for part in args.videos.split(",") if part.strip()]
        if args.ids_file:
            text = Path(args.ids_file).read_text(encoding="utf-8")
            ids += [p.strip() for p in text.replace(",", "\n").splitlines() if p.strip()]
        ids = unique(ids)
        if not ids:
            raise SystemExit("Provide video id(s) or --ids-file.")
        if len(ids) == 1:
            comments = modules.fetch_comments(client, ids[0], limit=args.limit)
            if args.pseudonymize:
                comments = modules.pseudonymize(comments)
            desc = ids[0]
        else:
            # bulk downloads are always pseudonymized
            comments = modules.fetch_comments_bulk(client, ids, limit=args.limit)
            desc = f"bulk_seeds{len(ids)}"
        graph = modules.interaction_network(comments)
        _report(write_table(
            comments, _outfile(args, f"videocomments_{desc}_comments_{stamp}.csv"), position=False
        ))
        _report(graph.write_gexf(_outfile(
            args, f"videocomments_{desc}_usernetwork_nodes{len(graph.nodes)}_{stamp}.gexf"
        )))

    elif args.command == "cocomment-network":
        ids = _parse_ids(args)
        source_parts = [f"seeds{len(ids)}"] if ids else []
        if args.query:
            found = modules.search_videos(client, args.query, **_search_kwargs(args))
            ids += found
            source_parts.append(f"search{len(found)}")
        if not ids:
            raise SystemExit("Provide --query, --ids, or --ids-file.")
        video_graph, channel_graph = modules.cocomment_networks(
            client, unique(ids), max_comments=args.max_comments
        )
        desc = "_".join(source_parts)
        _report(video_graph.write_gexf(_outfile(
            args, f"cocomment_{desc}_nodes{len(video_graph.nodes)}_{stamp}.gexf"
        )))
        _report(channel_graph.write_gexf(_outfile(
            args, f"cocomment_channels_{desc}_nodes{len(channel_graph.nodes)}_{stamp}.gexf"
        )))

    if not args.quiet:
        sys.stderr.write(
            f"done: {client.call_count} API calls, ~{client.quota_used} quota units\n"
        )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        run(args)
    except YTDTError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("\ninterrupted\n")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
