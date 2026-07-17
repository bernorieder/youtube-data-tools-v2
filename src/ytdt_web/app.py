"""NiceGUI presentation layer for YouTube Data Tools.

This is the only module that imports the framework. It renders one page
per module: a "narrative" form (pick a source first, then its parameters
appear), a run button, live progress from the job layer, and download
buttons for the produced files. All actual work happens in
:mod:`ytdt_web.jobs`.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from nicegui import app, run, ui

from . import turnstile
from .jobs import MODULES, Job, Module

OUTPUT_ROOT = Path(os.environ.get("YTDT_WEB_OUTPUT", "ytdt_web_output"))
# result files live in a real files/ subfolder, served at /files/... so the
# on-disk layout matches the report links
FILES_DIR = OUTPUT_ROOT / "files"
FILES_DIR.mkdir(parents=True, exist_ok=True)
app.add_static_files("/files", str(FILES_DIR))

# faint grey frame around all text/number/select fields
for _field in (ui.input, ui.textarea, ui.number, ui.select):
    _field.default_props("outlined dense")

VIDEO_ORDERS = ["relevance", "date", "rating", "title", "viewCount"]
CHANNEL_ORDERS = VIDEO_ORDERS + ["videoCount"]

# at most this many jobs may run at once per browser session
MAX_JOBS_PER_SESSION = 5
_session_jobs: dict[str, list[Job]] = {}

HEAD_HTML = """
<style>
  body { background: #ffffff; color: #171717; }
  .ytdt-label { font-size: 0.8rem; letter-spacing: 0.1em; text-transform: uppercase; color: #737373; }
  .ytdt-muted { color: #737373; }
  .ytdt-card { border: 1px solid #d4d4d4; border-radius: 2px; }
  .ytdt-card:hover { border-color: #171717; }
  a.ytdt-nav { color: #737373; text-decoration: none; }
  a.ytdt-nav:hover { color: #171717; }
  .q-field--outlined .q-field__control:before { border-color: #d4d4d4; }
  .ytdt-info { color: #525252; font-size: 0.92rem; }
  .ytdt-info a { color: #171717; }
  .ytdt-info ul { list-style: disc; padding-left: 1.3rem; }
  .ytdt-info h5 { font-size: 1.1rem; margin: 1.5rem 0 0.4rem; color: #171717; }
</style>
"""


# Turnstile widget script plus a small poller that renders the widget into
# any .ytdt-turnstile placeholder once both the script and the (websocket-
# rendered) element exist; %s receives the sitekey.
TURNSTILE_HTML = """
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
<script>
  setInterval(function () {
    if (!window.turnstile) return;
    document.querySelectorAll(".ytdt-turnstile:not([data-rendered])").forEach(function (el) {
      el.dataset.rendered = "1";
      window.turnstile.render(el, { sitekey: "%s", theme: "light" });
    });
  }, 400);
</script>
"""


# favicon: YT over DT in the interface's dark grey on a white rounded tile
FAVICON_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="6" fill="#ffffff"/>
  <text x="16" y="9" text-anchor="middle" dominant-baseline="middle"
        font-family="Helvetica, Arial, sans-serif" font-size="15" font-weight="bold"
        fill="#525252">YT</text>
  <text x="16" y="23" text-anchor="middle" dominant-baseline="middle"
        font-family="Helvetica, Arial, sans-serif" font-size="15" font-weight="bold"
        fill="#525252">DT</text>
</svg>
"""


# info-only pages (no form, no job): (nav title, path)
TEXT_PAGES = [("FAQ", "/faq"), ("Privacy", "/privacy")]


def frame(subtitle: str = "") -> ui.column:
    """Shared page chrome: monochrome palette, header with module nav."""
    ui.colors(
        primary="#171717",
        secondary="#525252",
        accent="#171717",
        dark="#171717",
        positive="#171717",
        negative="#7f1d1d",
        info="#525252",
        warning="#a16207",
    )
    ui.add_head_html(HEAD_HTML)
    if turnstile.enabled():
        ui.add_head_html(TURNSTILE_HTML % turnstile.site_key())
    with ui.header().classes("bg-white text-black py-4").style("border-bottom: 1px solid #d4d4d4"):
        with ui.row().classes(
            "w-full px-6 gap-x-8 gap-y-1 items-baseline wrap justify-center"
        ):
            ui.link("YouTube Data Tools", "/").classes("text-lg no-underline text-black")
            with ui.row().classes("gap-4 text-sm items-baseline justify-center"):
                for module in MODULES:
                    ui.link(module.title, f"/{module.slug}").classes("ytdt-nav")
                for title, path in TEXT_PAGES:
                    ui.link(title, path).classes("ytdt-nav")
    return ui.column().classes("w-full max-w-3xl mx-auto px-6 py-6 gap-5")


def split_ids(text: str) -> list[str]:
    return [part.strip() for part in text.replace(",", "\n").splitlines() if part.strip()]


def section_label(text: str) -> None:
    ui.label(text).classes("ytdt-label mt-3")


def narrative_radio(label: str, options: dict[str, str]):
    """An initially unselected choice; dependent fields appear on selection."""
    section_label(label)
    return ui.radio(options, value=None).props("inline")


def step(radio, *values: str) -> ui.column:
    """A container that appears when the radio holds one of ``values``."""
    column = ui.column().classes("w-full gap-3")
    column.bind_visibility_from(radio, "value", backward=lambda v: v in values)
    return column


def search_fields(orders: list[str]) -> Callable[[], dict]:
    """Shared search parameters; returns a getter for their values."""
    with ui.row().classes("gap-4 items-end"):
        order = ui.select(orders, value="relevance", label="Order").classes("w-36")
        language = ui.input("Language", placeholder="e.g. de").classes("w-28")
        region = ui.input("Region code", placeholder="e.g. NL").classes("w-28")
    with ui.row().classes("gap-4"):
        after = ui.input("Published after", placeholder="YYYY-MM-DD").props(
            'hint="YYYY-MM-DD or YYYY-MM-DDThh:mm:ssZ"'
        ).classes("w-60")
        before = ui.input("Published before", placeholder="YYYY-MM-DD").props(
            'hint="YYYY-MM-DD or YYYY-MM-DDThh:mm:ssZ"'
        ).classes("w-60")
    return lambda: {
        "order": order.value,
        "language": language.value,
        "region_code": region.value,
        "published_after": after.value,
        "published_before": before.value,
    }


def run_panel(module: Module, build_params: Callable[[], dict], *, bind_to=None) -> None:
    """Run button, live progress, result downloads, and the run report."""
    state: dict = {"job": None, "rendered": False}
    base_url = str(ui.context.client.request.base_url).rstrip("/")

    def file_url(path: Path) -> str:
        return f"{base_url}/files/{quote(path.relative_to(FILES_DIR).as_posix())}"

    with ui.column().classes("w-full gap-2 mt-1") as panel:
        if turnstile.enabled():
            ui.element("div").classes("ytdt-turnstile")
        run_button = ui.button(f"Run {module.title}", on_click=lambda: start()).classes("mb-3")
        bar = ui.linear_progress(value=0.0, show_value=False).classes("w-full").props("instant-feedback")
        bar.visible = False
        phase = ui.label("").classes("text-sm")
        meta = ui.label("").classes("text-sm ytdt-muted")
        error = ui.label("").classes("text-sm").style("color:#7f1d1d")
        results = ui.column().classes("gap-2 w-full")
    if bind_to is not None:
        panel.bind_visibility_from(bind_to, "value", backward=lambda v: v not in (None, ""))

    async def start() -> None:
        error.text = ""
        running = _session_jobs.setdefault(app.storage.browser["id"], [])
        running[:] = [j for j in running if j.status in ("pending", "running")]
        if len(running) >= MAX_JOBS_PER_SESSION:
            error.text = (
                f"At most {MAX_JOBS_PER_SESSION} jobs can run at the same time — "
                "please wait for one to finish."
            )
            return
        if turnstile.enabled():
            token = await ui.run_javascript(
                "document.querySelector('[name=cf-turnstile-response]')?.value || ''"
            )
            ok = await run.io_bound(turnstile.verify, token)
            ui.run_javascript("window.turnstile && window.turnstile.reset()")  # tokens are single-use
            if not ok:
                error.text = "The bot-protection check did not pass — please try again."
                return
        state["job"] = None
        state["rendered"] = False
        results.clear()
        phase.text = "starting…"
        meta.text = ""
        bar.visible = True
        bar.props(add="indeterminate")
        run_button.disable()
        job = Job(
            module=module.slug,
            params=build_params(),
            output_dir=FILES_DIR,
        )
        state["job"] = job.start()
        running.append(job)

    def refresh() -> None:
        job: Job | None = state["job"]
        if job is None or state["rendered"]:
            return
        if job.status == "running":
            if job.total:
                bar.props(remove="indeterminate")
                bar.value = min(1.0, job.done / job.total)
                phase.text = f"{job.desc}: {job.done}/{job.total}"
            elif job.desc:
                phase.text = f"{job.desc}…"
            meta.text = f"{job.calls} API calls · ~{job.quota} quota units"
            return
        if job.status in ("done", "error"):
            state["rendered"] = True
            bar.visible = False
            run_button.enable()
            meta.text = f"{job.calls} API calls · ~{job.quota} quota units"
            if job.status == "error":
                phase.text = ""
                error.text = job.error
                return
            phase.text = job.summary
            with results:
                for path in job.files:
                    ui.button(
                        path.name, on_click=lambda path=path: ui.download(path)
                    ).props("outline no-caps").classes("font-mono text-xs")
                report = job.report(file_url)
                report_name = f"report_{module.slug}_{datetime.now().strftime('%Y_%m_%d-%H_%M_%S')}.txt"
                ui.label("Report").classes("ytdt-label mt-6")
                ui.textarea(value=report).props("readonly autogrow").classes(
                    "w-full font-mono"
                ).style("font-size: 0.60rem")
                with ui.row().classes("gap-2"):
                    ui.button("Copy to clipboard", on_click=lambda: copy_report(report))
                    ui.button(
                        "Download as .txt",
                        on_click=lambda: ui.download.content(report, report_name),
                    )

    def copy_report(report: str) -> None:
        ui.clipboard.write(report)
        ui.notify("Report copied")

    ui.timer(0.15, refresh)


# -- module info texts ---------------------------------------------------------
#
# One function per module, rendered at the top of its page instead of the
# short description from the registry. Free space for manual information:
# markdown by default, but any ui.* element can be added here.


def info_channel_info() -> None:
    ui.markdown(
        """
Retrieves the full record for a single channel, specified by channel id, channel URL, or
@handle, via the
[channels/list](https://developers.google.com/youtube/v3/docs/channels/list) API endpoint,
and the channel's public playlists via the
[playlists/list](https://developers.google.com/youtube/v3/docs/playlists/list) API endpoint.

The module creates two **outputs**:

- the full channel record, shown directly in the run report below;
- a CSV file with one row per public playlist of the channel, only created if the channel has any.
"""
    ).classes("ytdt-info")


def info_channel_list() -> None:
    ui.markdown(
        """
Creates a list of channel information and statistics from one of two sources: a search
query (up to 500 results) or a list of channel ids, channel URLs, or @handles.

For additional information, check the documentation for the
[channels/list](https://developers.google.com/youtube/v3/docs/channels/list) (channel
information) and
[search/list](https://developers.google.com/youtube/v3/docs/search/list) (channel search)
API endpoints.

The module creates one **output**:

- a CSV file where each row is a channel, described by variables such as title, creation
  date, subscriber count, and video count.
"""
    ).classes("ytdt-info")


def info_channel_network() -> None:
    ui.markdown(
        """
Crawls a network of channels connected through featured channels and public subscriptions,
starting from a set of seed channels. Seeds come from a search query (up to 500 results)
or a list of channel ids, channel URLs, or @handles. Featured channels are retrieved via
[channelSections/list](https://developers.google.com/youtube/v3/docs/channelSections/list)
and subscriptions via
[subscriptions/list](https://developers.google.com/youtube/v3/docs/subscriptions/list);
most channels keep their subscriptions private, so the latter only contribute where public.

Crawl depth sets how far from the seeds the crawl goes: depth 0 keeps only the relations
between the seeds, and each further step adds the channels linked from the previous one.

**Warning**: many seeds combined with higher crawl depths can produce very large networks
and take a long time to collect — start small.

The module creates one **output**:

- a network file in GEXF format containing the crawled channel network; nodes carry the
  same variables as the Channel List CSV file.
"""
    ).classes("ytdt-info")


def info_video_list() -> None:
    ui.markdown(
        """
Creates a list of video metadata and statistics from one of four sources: one or more
channels, a playlist, a search query (up to 500 results), or a list of video ids.

For additional information, check the documentation for the
[search/list](https://developers.google.com/youtube/v3/docs/search/list) (video search)
and [videos/list](https://developers.google.com/youtube/v3/docs/videos/list) (video
metadata) API endpoints, or refer to
[this video](https://www.youtube.com/watch?v=ewCtzyNjELM) for a detailed discussion of the
module.

**Warning**: the search/list API endpoint has serious limitations in terms of temporal
coverage and consistency that can strongly affect the reliability of your findings. For
more information, check out
[this paper](https://www.tandfonline.com/doi/full/10.1080/1369118X.2025.2591767)
([open access preprint](https://arxiv.org/abs/2506.11727)).

The module creates up to three **outputs**:

- a CSV file where each row is a video, described by variables such as title, duration,
  tags, and view, like, and comment counts;
- (optional) a network file in GEXF format of co-occurring tags;
- (optional) a network file in GEXF format connecting videos that share tags.
"""
    ).classes("ytdt-info")


def info_trending() -> None:
    ui.markdown(
        """
Retrieves the "most popular" (trending) chart via the
[videos/list](https://developers.google.com/youtube/v3/docs/videos/list) API endpoint, for
one or several regions, optionally restricted to a single video category. The API caps
each chart at around 200 videos.

The module creates up to three **outputs**:

- a CSV file where each row is a chart entry — region, rank, and the same video variables
  as in the Video List module; when several regions are requested, the same video can
  appear once per region, making the file directly comparable across regions;
- with the tag networks option, a network file in GEXF format of co-occurring tags;
- with the tag networks option, a network file in GEXF format connecting videos that share
  tags.
"""
    ).classes("ytdt-info")


def info_video_comments() -> None:
    ui.markdown(
        """
Retrieves the comment sections of one or more videos, specified by video ids, via the
[commentThreads/list](https://developers.google.com/youtube/v3/docs/commentThreads/list)
and [comments/list](https://developers.google.com/youtube/v3/docs/comments/list) API
endpoints. The API flattens reply threads deeper than one level; where possible, the
actual reply structure is reconstructed from the @mentions YouTube inserts (unresolvable
replies are attributed to the thread's top-level comment, and the reconstructed values are
kept in separate `inferred` columns). Comments made by the video's channel owner are flagged
in the `isChannelOwner` column;

**Warning**: With more than one video id, the comments of all videos are combined into a
single CSV file (the `videoId` column identifies each comment's video) and a single user network.
In this bulk mode, author names and channel ids are always pseudonymized, and a single run
is limited to 100 video ids.

The module creates two **outputs**:

- a CSV file containing all retrievable comments, both top-level comments and replies;
- a network file in GEXF format mapping the interactions between users in the comment
  section.
"""
    ).classes("ytdt-info")


def info_cocomment_network() -> None:
    ui.markdown(
        """
Creates a network of videos based on co-commenting: when a user comments on two videos, a
link is made between them, and the more users comment on both, the stronger the link. A
configurable number of top-level comments per video is taken into account, ranked by
relevance; the channel owner is not counted. The videos are selected through a search
query (up to 500 results) or a list of video ids.

For additional information, check the documentation for the
[videos/list](https://developers.google.com/youtube/v3/docs/videos/list) (video metadata),
[search/list](https://developers.google.com/youtube/v3/docs/search/list) (video search),
and
[commentThreads/list](https://developers.google.com/youtube/v3/docs/commentThreads/list)
(comments) API endpoints.

The module creates two **outputs**:

- a network file in GEXF format connecting videos through shared commenters;
- a network file in GEXF format aggregating the same connections at the channel
  level.
"""
    ).classes("ytdt-info")


INFOS: dict[str, Callable[[], None]] = {
    "channel-info": info_channel_info,
    "channel-list": info_channel_list,
    "channel-network": info_channel_network,
    "video-list": info_video_list,
    "trending-videos": info_trending,
    "video-comments": info_video_comments,
    "cocomment-network": info_cocomment_network,
}


# -- module forms ------------------------------------------------------------


def form_channel_info(module: Module) -> None:
    section_label("Parameters")
    channel = ui.input("Channel id, URL, or @handle").classes("w-96")
    run_panel(module, lambda: {"channel": channel.value}, bind_to=channel)


def form_channel_list(module: Module) -> None:
    source = narrative_radio("Source", {"search": "Search", "ids": "List of channel ids"})
    with step(source, "search"):
        query = ui.input("Search query").classes("w-96")
        get_search = search_fields(CHANNEL_ORDERS)
    with step(source, "ids"):
        ids = ui.textarea("Channel ids, URLs, or @handles (one per line or comma-separated)").classes("w-128")

    def params() -> dict:
        if source.value == "search":
            return {"query": query.value, **get_search()}
        return {"ids": split_ids(ids.value or "")}

    run_panel(module, params, bind_to=source)


def form_channel_network(module: Module) -> None:
    source = narrative_radio("Seed channels", {"search": "Search", "ids": "List of channel ids"})
    with step(source, "search"):
        query = ui.input("Search query").classes("w-96")
        get_search = search_fields(CHANNEL_ORDERS)
    with step(source, "ids"):
        ids = ui.textarea("Seed channels: ids, URLs, or @handles (one per line or comma-separated)").classes("w-128")
    with step(source, "search", "ids"):
        section_label("Crawl")
        depth = ui.radio(
            {0: "seeds only", 1: "distance 1", 2: "distance 2"}, value=1
            # {0: "seeds only", 1: "distance 1", 2: "distance 2", 3: "distance 3"}, value=1
        ).props("inline")

    def params() -> dict:
        p: dict = {"depth": depth.value}
        if source.value == "search":
            p.update({"query": query.value, **get_search()})
        else:
            p["ids"] = split_ids(ids.value or "")
        return p

    run_panel(module, params, bind_to=source)


def form_video_list(module: Module) -> None:
    source = narrative_radio(
        "Source",
        {"search": "Search", "channel": "Channels", "playlist": "Playlist", "ids": "List of video ids"},
    )
    with step(source, "search"):
        query = ui.input("Search query").classes("w-96")
        get_search = search_fields(VIDEO_ORDERS)
        day_mode = ui.checkbox("one search per day of the timeframe (needs published after/before)")
        with ui.row().classes("gap-4"):
            location = ui.input("Location", placeholder="52.37,4.89").classes("w-44")
            radius = ui.input("Radius", placeholder="10km").classes("w-28")
    with step(source, "channel"):
        channels = ui.textarea(
            "Channel ids, URLs, or @handles (one per line or comma-separated; uploads are listed)"
        ).classes("w-128")
    with step(source, "playlist"):
        playlist = ui.input("Playlist id").classes("w-96")
    with step(source, "ids"):
        ids = ui.textarea("Video ids (one per line or comma-separated)").classes("w-128")

    with step(source, "search", "channel", "playlist", "ids"):
        section_label("Extras")
        cotag = ui.checkbox("tag networks (co-occurring tags + videos linked by shared tags)")
        shorts = ui.checkbox("detect Shorts (adds isShort column, can take a long time to run)")
        channel_details = ui.checkbox("add channel details to each video (adds several channel_ columns)")

    def params() -> dict:
        p: dict = {
            "shorts": "detect" if shorts.value else "",
            "cotag": cotag.value,
            "channel_details": channel_details.value,
        }
        if source.value == "search":
            p.update({
                "query": query.value,
                "day_mode": day_mode.value,
                "location": location.value,
                "location_radius": radius.value,
                **get_search(),
            })
        elif source.value == "channel":
            p["channels"] = split_ids(channels.value or "")
        elif source.value == "playlist":
            p["playlist"] = playlist.value
        else:
            p["ids"] = split_ids(ids.value or "")
        return p

    run_panel(module, params, bind_to=source)


def form_trending(module: Module) -> None:
    section_label("Parameters")
    regions = ui.input("Region codes (comma-separated)", value="US").classes("w-96")
    with ui.row().classes("gap-4"):
        category = ui.input("Category id (optional)", placeholder="10 = Music").classes("w-44")
        limit = ui.number("Max per region", value=None, min=1, max=200).classes("w-36")
    section_label("Extras")
    cotag = ui.checkbox("tag networks (co-occurring tags + videos linked by shared tags)")
    shorts = ui.checkbox("detect Shorts (adds isShort column, can take a long time to run)")
    channel_details = ui.checkbox("add channel details to each video (adds several channel_ columns)")

    def params() -> dict:
        return {
            "regions": split_ids(regions.value or ""),
            "category": category.value,
            "limit": limit.value,
            "shorts": "detect" if shorts.value else "",
            "cotag": cotag.value,
            "channel_details": channel_details.value,
        }

    run_panel(module, params, bind_to=regions)


def form_video_comments(module: Module) -> None:
    section_label("Parameters")
    ids = ui.textarea("Video ids (one per line or comma-separated)").classes("w-128")
    with ui.row().classes("gap-4 items-end"):
        limit = ui.number("Max top-level comments per video", value=None, min=1).classes("w-70")
        pseudonymize = ui.checkbox("pseudonymize usernames")

    def params() -> dict:
        return {
            "ids": split_ids(ids.value or ""),
            "limit": limit.value,
            "pseudonymize": pseudonymize.value,
        }

    run_panel(module, params, bind_to=ids)


def form_cocomment_network(module: Module) -> None:
    source = narrative_radio("Source", {"search": "Search", "ids": "List of video ids"})
    with step(source, "search"):
        query = ui.input("Search query").classes("w-96")
        get_search = search_fields(VIDEO_ORDERS)
    with step(source, "ids"):
        ids = ui.textarea("Video ids (one per line or comma-separated)").classes("w-128")
    with step(source, "search", "ids"):
        max_comments = ui.number("Top-level comments per video", value=100, min=1).classes("w-56")

    def params() -> dict:
        p: dict = {"max_comments": max_comments.value or 100}
        if source.value == "search":
            p.update({"query": query.value, **get_search()})
        else:
            p["ids"] = split_ids(ids.value or "")
        return p

    run_panel(module, params, bind_to=source)


FORMS: dict[str, Callable[[Module], None]] = {
    "channel-info": form_channel_info,
    "channel-list": form_channel_list,
    "channel-network": form_channel_network,
    "video-list": form_video_list,
    "trending-videos": form_trending,
    "video-comments": form_video_comments,
    "cocomment-network": form_cocomment_network,
}


@ui.page("/")
def index() -> None:
    with frame():
        ui.label("YouTube Data Tools").classes("text-3xl")
        ui.label(
            "Extract channel, video, and comment data from the YouTube API v3 "
            "for research. Pick a module to begin."
        ).classes("ytdt-muted")
        if not os.environ.get("YTDT_API_KEY"):
            ui.label("No YTDT_API_KEY set on the server — runs will fail.").style("color:#7f1d1d")
        with ui.grid(columns=2).classes("w-full gap-4 mt-2"):
            for module in MODULES:
                with ui.link(target=f"/{module.slug}").classes("no-underline"):
                    with ui.column().classes("ytdt-card p-4 gap-1 w-full"):
                        ui.label(module.title).classes("text-lg text-black")
                        ui.label(module.description).classes("text-sm ytdt-muted")
        with ui.row().classes("text-sm ytdt-muted mt-2 gap-1"):
            ui.label("Looking for the previous version?")
            ui.link("The old YTDT is still available.", "/old/").classes("ytdt-nav underline")


def make_page(module: Module) -> None:
    @ui.page(f"/{module.slug}")
    def page() -> None:
        with frame(module.title):
            ui.label(module.title).classes("text-2xl")
            INFOS[module.slug]()
            FORMS[module.slug](module)


for _module in MODULES:
    make_page(_module)


# -- info-only pages -----------------------------------------------------------


def info_faq() -> None:
    ui.markdown(
        """
##### What is this?

YouTube Data Tools (YTDT) is a collection of simple modules for extracting data from the
YouTube platform via the [YouTube API v3](https://developers.google.com/youtube/v3/). It is
not a mashup or fully developed analytics software, but a means for researchers to collect
data in standard file formats to analyze further in other software packages.

##### Who develops YTDT?

YTDT is written and maintained by [Bernhard Rieder](http://rieder.polsys.net), Associate
Professor in [Media Studies](http://mediastudies.nl) at the
[University of Amsterdam](http://www.uva.nl) and researcher with the
[Digital Methods Initiative](https://www.digitalmethods.net).

Development and maintenance of this tool are financed by the Dutch
[Platform Digitale Infrastructuur Social Science and Humanities](https://pdi-ssh.nl/) as
part of the [CAT4SMR project](https://cat4smr.humanities.uva.nl/).

Changes or new modules are announced on [@RiederB](https://twitter.com/RiederB/) and
[@cat4smr](https://twitter.com/cat4smr), but for questions and support please refer to the
help section below.

##### How can I cite YTDT?

There is currently no publication on YTDT. But the different citation standards provide
guidelines for how to cite software, e.g. APA: Rieder, Bernhard (2015). YouTube Data Tools
(Version 2.0) [Software]. Available from https://ytdt.digitalmethods.net.

Alternatively, you can cite this
[blog post](http://thepoliticsofsystems.net/2015/05/exploring-youtube/).

If you are interested in the kind of work that can be done with this tool, check out this
[research paper](http://journals.sagepub.com/doi/full/10.1177/1354856517736982).

##### What kind of files does YTDT generate?

It creates network files in
[GEXF format](https://gexf.net/) (an XML-based
format that specifies a graph) and
[CSV files](https://www.howtogeek.com/348960/what-is-a-csv-file-and-how-do-i-open-it/) for
tabular data.

These files can then be analyzed and visualized with network analysis software such as the
powerful and easy to use [Gephi](http://gephi.org/) or statistical tools such as R, Excel,
or SPSS.

##### I don't know how to use YTDT, can you help me?

There is a
[collection of introductory videos](https://www.youtube.com/playlist?list=PLVTuM_sR1CecX8pgQaTfnDvxo9g_j_RO0),
and each module page describes what the module does and links to the relevant sections of
the API documentation. Most importantly, making sense of the data requires a good
understanding of YouTube's basic architecture; the
[API documentation](https://developers.google.com/youtube/v3/) has comprehensive
descriptions of entities and metrics.

We provide limited user support through
[GitHub](https://github.com/bernorieder/YouTube-Data-Tools-v2/issues). Please do not use
social media or email.

##### What are channel or video ids and how can I find them?

Many of the modules require a video id or channel id as input. These can normally be found
in the respective YouTube URLs.

For example, in the URL https://www.youtube.com/watch?v=**BNM4kEUEcp8**, the code after
the "=" sign is the video id.

Channel ids have a format similar to **UCtxGqPJPPi8ptAzB029jpYA** and can be found via the
[Channel Info module](/channel-info): paste the channel URL into the form and the channel
id will be in the result. All modules that take channels as input also accept channel URLs
and @handles directly.

##### Where is the video network module?

YouTube removed the "relatedVideos" API endpoint in August 2023 and, as a consequence, this
module had to be retired.

##### The tool does not work (correctly)!

While YTDT is very simple software, this can happen for all kinds of reasons. Most
problems are due to limitations or bugs in YouTube's API and cannot easily be solved on
our side. Sometimes the tool will fail because users have been using it too heavily.

High quality bug reports are much appreciated. If you have no experience with reporting
bugs effectively, please read
[this piece](http://www.chiark.greenend.org.uk/~sgtatham/bugs.html). In short: developers
need context to debug a tool, so please include the parameters you used, the browser you
are using, a screenshot of the interface, the data files, and a description of what you
did and how the problem manifests itself. Without this information it can be very hard to
replicate a problem, let alone fix it.

Please submit issues or bug reports via
[GitHub](https://github.com/bernorieder/YouTube-Data-Tools-v2/issues). Please do not use
social media or email.

##### I want to make crawls with higher crawl depth!

Since the public version of the tool runs on a shared server, the interface caps crawl
depth (currently at 3) due to resource constraints. But you can always get the source code
(see below) and use the underlying library, which has no such limit. You may still run out
of RAM, but networks with more than 100,000 nodes should be easily doable with 4 GB.

##### Can you add feature X to YTDT?

We cannot make any guarantees, but if you post a feature request on
[GitHub](https://github.com/bernorieder/YouTube-Data-Tools-v2/), we will definitely consider
it. Please do not use social media or email.

##### Where is the source code?

The full source code is available on
[GitHub](https://github.com/bernorieder/YouTube-Data-Tools-v2). You'll also find installation
instructions there.
"""
    ).classes("ytdt-info")


def info_privacy() -> None:
    ui.markdown(
        """
##### When you use the YouTube Data Tools, we collect the following data:

Your IP address and the URL through which you query our web tool will be logged by our web
server. This is required to monitor web server use, and detect errors and abuse. These data
are not shared with anyone.

##### The data you retrieve from YouTube are handled the following way:

The result files a module produces are stored temporarily on our server so you can download
them, and are deleted regularly. To improve performance, the Shorts detection feature keeps
a cache of which videos are Shorts; this cache contains video ids only, no user-level data.
"""
    ).classes("ytdt-info")


def make_text_page(title: str, path: str, renderer: Callable[[], None]) -> None:
    @ui.page(path)
    def page() -> None:
        with frame(title):
            ui.label(title).classes("text-2xl")
            renderer()


make_text_page("Frequently Asked Questions", "/faq", info_faq)
make_text_page("Privacy Policy", "/privacy", info_privacy)


def main() -> None:
    ui.run(
        title="YouTube Data Tools",
        favicon=FAVICON_SVG,
        host=os.environ.get("YTDT_WEB_HOST", "127.0.0.1"),
        port=int(os.environ.get("YTDT_WEB_PORT", "8080")),
        # needed for the per-browser-session job cap (app.storage.browser);
        # set YTDT_WEB_STORAGE_SECRET for stable sessions across restarts
        storage_secret=os.environ.get("YTDT_WEB_STORAGE_SECRET") or secrets.token_hex(16),
        # YTDT_WEB_RELOAD=1 restarts the server on source changes (dev only)
        reload=os.environ.get("YTDT_WEB_RELOAD", "") == "1",
        show=False,
        dark=False,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
