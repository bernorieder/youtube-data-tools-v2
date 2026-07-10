"""Framework-agnostic job layer for YTDT frontends.

Everything a frontend needs lives here: module metadata (:data:`MODULES`),
parameter validation, execution in a background thread, live progress
state, and the resulting output files. Nothing in this module knows about
the web framework — switching frameworks means rewriting only the
presentation layer.

Parameters arrive as a plain dict (see each runner for its keys); list
values are real lists, booleans real booleans. Runners raise
``ValueError`` with a readable message for bad input; any exception ends
the job with ``status = "error"`` and the message in ``Job.error``.
"""

from __future__ import annotations

import csv
import os
import threading
from dataclasses import dataclass, field
from xml.etree import ElementTree
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ytdt import Channel, FactCache, YouTubeClient, modules, resolve_channel_ids
from ytdt.tabular import write_table
from ytdt.utils import unique


def _stamp() -> str:
    return datetime.now().strftime("%Y_%m_%d-%H_%M_%S")


def n_of(count: int, singular: str, plural: str | None = None) -> str:
    """Count phrase with a proper singular: "1 video", "2 videos"."""
    noun = singular if count == 1 else (plural or singular + "s")
    return f"{count:,} {noun}"


def file_stats(path: Path) -> str:
    """One-line content description of an output file for the run report."""
    if path.suffix == ".csv":
        with path.open(encoding="utf-8", newline="") as fh:
            rows = max(0, sum(1 for _ in csv.reader(fh)) - 1)  # minus header
        return n_of(rows, "row")
    if path.suffix == ".gexf":
        nodes = edges = 0
        for _, element in ElementTree.iterparse(path):
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "node":
                nodes += 1
            elif tag == "edge":
                edges += 1
            element.clear()
        return f"{n_of(nodes, 'node')}, {n_of(edges, 'edge')}"
    return n_of(path.stat().st_size, "byte")


def _shorts_options(client: YouTubeClient, p: dict, videos: list) -> list:
    """Apply the shorts detection/filter params shared by video-list and
    trending: ``shorts`` is one of "" (off), "detect", "only", "longform"."""
    mode = p.get("shorts", "")
    if not mode:
        return videos
    modules.detect_shorts(client, videos, cache=FactCache())
    if mode == "only":
        return [v for v in videos if v.is_short == "yes"]
    if mode == "longform":
        return [v for v in videos if v.is_short == "no"]
    return videos


# -- one runner per module: (client, params, output_dir) -> (files, summary)


def _channel_info(client: YouTubeClient, p: dict, outdir: Path) -> tuple:
    if not p.get("channel"):
        raise ValueError("Provide a channel id, URL, or @handle.")
    info = modules.channel_info(client, p["channel"])
    # flatten the raw API resource into the channel-list fields for display
    row = Channel.from_api(info).to_row()
    uploads = info.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", "")
    if uploads:
        row["uploadsPlaylist"] = uploads
    summary = row.get("title", "")
    if row.get("subscriberCount") != "":
        summary += f" — {int(row['subscriberCount']):,} subscribers"
    files = []
    playlists = modules.channel_playlists(client, row["id"])
    if playlists:
        files.append(outdir / f"channelplaylists_{row['id']}_{_stamp()}.csv")
        write_table(playlists, files[0], position=False)
    summary += f", {n_of(len(playlists), 'public playlist')}"
    return files, summary, row


def _channel_list(client: YouTubeClient, p: dict, outdir: Path) -> tuple[list[Path], str]:
    ids = resolve_channel_ids(client, list(p.get("ids") or []))
    if p.get("query"):
        ids += modules.search_channels(client, p["query"], **_search_kwargs(p))
    if not ids:
        raise ValueError("Provide a search query or channel ids.")
    channels = modules.fetch_channels(client, unique(ids))
    path = outdir / f"channellist{len(channels)}_{_stamp()}.csv"
    write_table(channels, path)
    return [path], n_of(len(channels), "channel")


def _channel_network(client: YouTubeClient, p: dict, outdir: Path) -> tuple[list[Path], str]:
    seeds = resolve_channel_ids(client, list(p.get("ids") or []))
    parts = [f"seeds{len(seeds)}"] if seeds else []
    if p.get("query"):
        found = modules.search_channels(client, p["query"], **_search_kwargs(p))
        seeds += found
        parts.append(f"search{len(found)}")
    if not seeds:
        raise ValueError("Provide a search query or seed channel ids.")
    graph = modules.crawl_channel_network(client, unique(seeds), depth=int(p.get("depth", 1)))
    desc = "_".join(parts)
    path = outdir / f"channelnet_{desc}_nodes{len(graph.nodes)}_{_stamp()}.gexf"
    graph.write_gexf(path)
    return [path], f"{n_of(len(graph.nodes), 'channel')}, {n_of(len(graph.edges), 'link')}"


def _video_list(client: YouTubeClient, p: dict, outdir: Path) -> tuple[list[Path], str]:
    ids = list(p.get("ids") or [])
    source = "seeds" if ids else ""
    parts = [f"seeds{len(ids)}"] if ids else []
    known_shorts: set[str] = set()
    if p.get("channels"):
        channels = resolve_channel_ids(client, list(p["channels"]))
        if p.get("shorts") == "only":
            id_lists = client.map(
                lambda c: modules.channel_shorts_ids(client, c), channels, desc="channel shorts"
            )
            known_shorts.update(vid for lst in id_lists for vid in lst)
            ids += [vid for lst in id_lists for vid in lst]
        else:
            ids += modules.channel_video_ids(client, channels)
        source = source or "channel"
        parts.append(
            f"channel_{channels[0]}" if len(channels) == 1 else f"channels{len(channels)}"
        )
    if p.get("playlist"):
        ids += modules.playlist_video_ids(client, p["playlist"].strip())
        source = source or "playlist"
        parts.append(f"playlist_{p['playlist'].strip()}")
    if p.get("query") or p.get("location"):
        found = modules.search_videos(
            client,
            p.get("query"),
            day_mode=bool(p.get("day_mode")),
            location=p.get("location"),
            location_radius=p.get("location_radius"),
            **_search_kwargs(p),
        )
        ids += found
        source = source or "search"
        parts.append(f"search{len(found)}")
    if not ids:
        raise ValueError("Provide a source: search, channel, playlist, or video ids.")

    videos = modules.fetch_videos(client, unique(ids))
    for video in videos:
        if video.video_id in known_shorts:
            video.is_short = "yes"
    videos = _shorts_options(client, p, videos)
    details = modules.channel_details(client, videos) if p.get("channel_details") else None
    rows: list = videos
    if details is not None:
        rows = [{**v.to_row(), **details.get(v.channel_id, {})} for v in videos]

    stamp = _stamp()
    files = [outdir / f"videolist_{source}{len(videos)}_{stamp}.csv"]
    write_table(rows, files[0])
    if p.get("cotag"):
        desc = "_".join(parts)
        tag_graph = modules.cotag_network(videos)
        files.append(outdir / f"videolist_tagnet_{desc}_nodes{len(tag_graph.nodes)}_{stamp}.gexf")
        tag_graph.write_gexf(files[-1])
        video_graph = modules.shared_tag_network(videos, channel_details=details)
        files.append(
            outdir / f"videolist_sharedtagnet_{desc}_nodes{len(video_graph.nodes)}_{stamp}.gexf"
        )
        video_graph.write_gexf(files[-1])
    return files, n_of(len(videos), "video")


def _trending(client: YouTubeClient, p: dict, outdir: Path) -> tuple[list[Path], str]:
    regions = unique(r.strip().upper() for r in (p.get("regions") or []) if r.strip())
    if not regions:
        raise ValueError("Provide at least one region code.")
    per_region = client.map(
        lambda region: modules.trending_videos(
            client,
            region_code=region,
            category_id=p.get("category") or None,
            limit=int(p["limit"]) if p.get("limit") else None,
        ),
        regions,
        desc="regions",
    )
    entries = [
        (region, rank, video)
        for region, vids in zip(regions, per_region)
        for rank, video in enumerate(vids, start=1)
    ]
    mode = p.get("shorts", "")
    if mode:
        modules.detect_shorts(client, [v for _, _, v in entries], cache=FactCache())
    if mode == "only":
        entries = [e for e in entries if e[2].is_short == "yes"]
    elif mode == "longform":
        entries = [e for e in entries if e[2].is_short == "no"]

    videos_by_id = {video.video_id: video for _, _, video in entries}
    videos = list(videos_by_id.values())
    details = modules.channel_details(client, videos) if p.get("channel_details") else None
    rows = [
        {"region": region, "rank": rank, **video.to_row(), **(details or {}).get(video.channel_id, {})}
        for region, rank, video in entries
    ]
    stamp = _stamp()
    desc = "-".join(regions) + (f"_cat{p['category']}" if p.get("category") else "")
    files = [outdir / f"trending_{desc}_videos{len(rows)}_{stamp}.csv"]
    write_table(rows, files[0], position=False)
    if p.get("cotag"):
        tag_graph = modules.cotag_network(videos)
        files.append(outdir / f"trending_tagnet_{desc}_nodes{len(tag_graph.nodes)}_{stamp}.gexf")
        tag_graph.write_gexf(files[-1])
        video_graph = modules.shared_tag_network(videos, channel_details=details)
        files.append(
            outdir / f"trending_sharedtagnet_{desc}_nodes{len(video_graph.nodes)}_{stamp}.gexf"
        )
        video_graph.write_gexf(files[-1])
    return files, f"{n_of(len(rows), 'chart entry', 'chart entries')}, {n_of(len(videos), 'distinct video')}"


def _video_comments(client: YouTubeClient, p: dict, outdir: Path) -> tuple[list[Path], str]:
    ids = unique(p.get("ids") or [])
    if not ids:
        raise ValueError("Provide one or more video ids.")
    limit = int(p["limit"]) if p.get("limit") else None
    if len(ids) == 1:
        comments = modules.fetch_comments(client, ids[0], limit=limit)
        if p.get("pseudonymize"):
            comments = modules.pseudonymize(comments)
        desc = ids[0]
    else:  # bulk downloads are always pseudonymized
        comments = modules.fetch_comments_bulk(client, ids, limit=limit)
        desc = f"bulk_seeds{len(ids)}"
    graph = modules.interaction_network(comments)
    stamp = _stamp()
    files = [
        outdir / f"videocomments_{desc}_comments_{stamp}.csv",
        outdir / f"videocomments_{desc}_usernetwork_nodes{len(graph.nodes)}_{stamp}.gexf",
    ]
    write_table(comments, files[0], position=False)
    graph.write_gexf(files[1])
    return files, f"{n_of(len(comments), 'comment')}, {n_of(len(graph.nodes), 'user')}"


def _cocomment_network(client: YouTubeClient, p: dict, outdir: Path) -> tuple[list[Path], str]:
    ids = list(p.get("ids") or [])
    parts = [f"seeds{len(ids)}"] if ids else []
    if p.get("query"):
        found = modules.search_videos(client, p["query"], **_search_kwargs(p))
        ids += found
        parts.append(f"search{len(found)}")
    if not ids:
        raise ValueError("Provide a search query or video ids.")
    video_graph, channel_graph = modules.cocomment_networks(
        client, unique(ids), max_comments=int(p.get("max_comments", 100))
    )
    stamp = _stamp()
    desc = "_".join(parts)
    files = [
        outdir / f"cocomment_{desc}_nodes{len(video_graph.nodes)}_{stamp}.gexf",
        outdir / f"cocomment_channels_{desc}_nodes{len(channel_graph.nodes)}_{stamp}.gexf",
    ]
    video_graph.write_gexf(files[0])
    channel_graph.write_gexf(files[1])
    return files, f"{n_of(len(video_graph.nodes), 'video')}, {n_of(len(channel_graph.nodes), 'channel')}"


def _search_kwargs(p: dict) -> dict:
    return {
        "order": p.get("order") or "relevance",
        "language": p.get("language") or None,
        "region_code": p.get("region_code") or None,
        "published_after": p.get("published_after") or None,
        "published_before": p.get("published_before") or None,
    }


@dataclass(frozen=True)
class Module:
    slug: str
    title: str
    description: str
    runner: Callable[[YouTubeClient, dict, Path], tuple[list[Path], str]]


MODULES = [
    Module(
        "channel-info",
        "Channel Info",
        "The full API record and public playlists for a single channel.",
        _channel_info,
    ),
    Module(
        "channel-list",
        "Channel List",
        "Channel information and statistics from a search or a list of channels.",
        _channel_list,
    ),
    Module(
        "channel-network",
        "Channel Network",
        "Crawl the network between channels via featured channels and public subscriptions.",
        _channel_network,
    ),
    Module(
        "video-list",
        "Video List",
        "Video metadata and statistics from a channel, playlist, search, or list of videos.",
        _video_list,
    ),
    Module(
        "trending-videos",
        "Trending Videos",
        "The most-popular (trending) chart per region, comparable across regions.",
        _trending,
    ),
    Module(
        "video-comments",
        "Video Comments",
        "Comments and the commenter interaction network for one or several videos.",
        _video_comments,
    ),
    Module(
        "cocomment-network",
        "Co-comment Network",
        "Networks of videos and channels connected by shared commenters.",
        _cocomment_network,
    ),
]

MODULES_BY_SLUG = {module.slug: module for module in MODULES}


@dataclass
class Job:
    """One collection run, executing in a background thread.

    Frontends poll the public attributes for live state: ``status``
    (pending → running → done | error), the current progress phase
    (``desc``, ``done``, ``total``), and on completion ``files``,
    ``summary``, ``calls``, and ``quota``. :meth:`report` renders a
    plain-text account of a finished run.
    """

    module: str
    params: dict[str, Any]
    output_dir: Path
    api_key: str | None = None
    status: str = "pending"
    desc: str = ""
    done: int = 0
    total: int = 0
    error: str = ""
    summary: str = ""
    data: dict[str, Any] | None = None  # structured result for on-page display
    files: list[Path] = field(default_factory=list)
    calls: int = 0
    quota: int = 0
    started: str = ""
    finished: str = ""
    _client: YouTubeClient | None = field(default=None, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)

    def start(self, *, client_factory: Callable[..., YouTubeClient] | None = None) -> "Job":
        """Run the job in a daemon thread; returns immediately."""
        factory = client_factory or YouTubeClient
        self._thread = threading.Thread(target=self._run, args=(factory,), daemon=True)
        self._thread.start()
        return self

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _run(self, client_factory: Callable[..., YouTubeClient]) -> None:
        self.status = "running"
        self.started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            self._client = client_factory(
                api_key=self.api_key or os.environ.get("YTDT_API_KEY"),
                on_progress=self._on_progress,
            )
            self.output_dir.mkdir(parents=True, exist_ok=True)
            runner = MODULES_BY_SLUG[self.module].runner
            result = runner(self._client, self.params, self.output_dir)
            self.files, self.summary = result[0], result[1]
            self.data = result[2] if len(result) > 2 else None
            self.status = "done"
        except Exception as exc:  # shown to the user; never kills the server
            self.error = str(exc) or exc.__class__.__name__
            self.status = "error"
        finally:
            self.finished = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if self._client is not None:
                self.calls = self._client.call_count
                self.quota = self._client.quota_used

    def report(self, file_url: Callable[[Path], str] | None = None) -> str:
        """Plain-text report of a finished run: module, time, parameters,
        result summary, and the output files with basic content stats.

        ``file_url`` maps an output file to a link where it can be
        downloaded again; without it the report lists file names only.
        """
        lines = [
            f"YouTube Data Tools — {MODULES_BY_SLUG[self.module].title}",
            f"Run: {self.started}" + (f" → {self.finished}" if self.finished else ""),
            "",
            "Parameters:",
        ]
        for key, value in self.params.items():
            if value in (None, "", [], False):
                continue  # unset parameters don't shape the result
            if isinstance(value, bool):
                value = "yes"
            elif isinstance(value, list):
                value = ", ".join(str(item) for item in value)
            lines.append(f"   {key}: {value}")
        lines.append("")
        if self.status == "error":
            lines.append(f"Error: {self.error}")
        else:
            lines.append(f"Result: {self.summary}")
        lines.append(f"API usage: {n_of(self.calls, 'call')}, ~{n_of(self.quota, 'quota unit')}")
        if self.data:
            lines += ["", "Data:"]
            lines += [f"   {key}: {value}" for key, value in self.data.items()]
        if self.files:
            lines += ["", "Files (available for seven days):"]
            for path in self.files:
                name = file_url(path) if file_url is not None else path.name
                lines.append(f"   {name} ({file_stats(path)})")
        return "\n".join(lines)

    def _on_progress(self, desc: str, done: int, total: int) -> None:
        self.desc, self.done, self.total = desc, done, total
        if self._client is not None:
            self.calls = self._client.call_count
            self.quota = self._client.quota_used
