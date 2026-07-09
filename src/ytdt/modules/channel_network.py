"""Channel Network module: breadth-first crawl over featured channels
(channelSections) and public subscriptions.

Each crawl level fetches channel details in id-batches of 50 and expands
outgoing links for all channels of the level in parallel. Edges pointing
outside the crawled node set are dropped at export, matching the original
tool's behaviour.
"""

from __future__ import annotations

from ..client import YouTubeClient
from ..errors import SkippableError
from ..graph import Graph
from ..models import Channel
from ..utils import chunked, unique

# Same parts as the Channel List module: network nodes carry the same
# fields as channel list rows, plus isSeed/seedRank.
DETAIL_PARTS = "id,snippet,topicDetails,statistics,brandingSettings,status"


def _node_attrs(item: dict, *, seed_rank: int | None) -> dict:
    row = Channel.from_api(item).to_row()
    row.pop("id")  # already the node name
    attrs = {
        "label": row.pop("title"),
        "isSeed": "yes" if seed_rank is not None else "no",
        "seedRank": seed_rank if seed_rank is not None else "",
        **row,
    }
    # the API serves counts as strings; graph tools need numbers
    for key in ("viewCount", "subscriberCount", "videoCount"):
        attrs[key] = int(attrs[key]) if attrs[key] != "" else ""
    return attrs


def _featured_channels(client: YouTubeClient, channel_id: str) -> list[str]:
    try:
        reply = client.get("channelSections", part="contentDetails", channelId=channel_id)
    except SkippableError:
        return []
    targets: list[str] = []
    for section in reply.get("items", []):
        targets.extend(section.get("contentDetails", {}).get("channels", []))
    return targets


def _subscriptions(client: YouTubeClient, channel_id: str) -> list[str]:
    try:
        items = client.paginate(
            "subscriptions", part="snippet", channelId=channel_id, maxResults=50
        )
        return [item["snippet"]["resourceId"]["channelId"] for item in items]
    except SkippableError:
        # most channels keep their subscriptions private
        return []


def crawl_channel_network(
    client: YouTubeClient,
    seed_ids: list[str],
    *,
    depth: int = 1,
) -> Graph:
    """Crawl the channel graph up to ``depth`` hops from the seeds.

    Links follow both featured channels and public subscriptions (most
    channels keep subscriptions private, so the latter contribute only
    where visible). Depth 0 keeps only relations among the seeds; each
    further level adds the channels linked from the previous one. Depth 2
    and beyond can grow very large — start small.
    """
    graph = Graph(directed=True)
    links: list[tuple[str, str]] = []
    frontier = unique(seed_ids)

    for level in range(depth + 1):
        new_ids = [cid for cid in frontier if cid not in graph.nodes]
        if not new_ids:
            break

        def fetch_batch(batch: list[str]) -> list[dict]:
            reply = client.get("channels", part=DETAIL_PARTS, id=",".join(batch), maxResults=50)
            return reply.get("items", [])

        batches = client.map(fetch_batch, chunked(new_ids), desc=f"channel details (depth {level})")
        items = {item["id"]: item for batch in batches for item in batch}
        for cid in new_ids:
            if cid in items:
                seed_rank = new_ids.index(cid) + 1 if level == 0 else None
                graph.add_node(cid, **_node_attrs(items[cid], seed_rank=seed_rank))
        resolved = [cid for cid in new_ids if cid in items]

        def outgoing(channel_id: str) -> list[str]:
            targets = _featured_channels(client, channel_id)
            targets += _subscriptions(client, channel_id)
            return unique(t for t in targets if t != channel_id)

        target_lists = client.map(outgoing, resolved, desc=f"channel links (depth {level})")
        next_frontier: list[str] = []
        for source, targets in zip(resolved, target_lists):
            for target in targets:
                links.append((source, target))
                next_frontier.append(target)
        frontier = unique(next_frontier)

    for source, target in links:
        if source in graph.nodes and target in graph.nodes:
            graph.add_edge(source, target)
    return graph
