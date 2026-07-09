from __future__ import annotations

from ytdt_web.jobs import MODULES_BY_SLUG, Job


def video_item(vid: str) -> dict:
    return {
        "id": vid,
        "snippet": {
            "channelId": "UCx",
            "channelTitle": "chan",
            "publishedAt": "2024-01-01T00:00:00Z",
            "title": f"video {vid}",
            "description": "",
            "categoryId": "22",
            "tags": ["a", "b"],
        },
        "contentDetails": {"duration": "PT1M"},
        "statistics": {"viewCount": "10", "likeCount": "1", "commentCount": "2"},
    }


def handler(endpoint, params):
    if endpoint == "videos":
        return {"items": [video_item(v) for v in params["id"].split(",")]}
    if endpoint == "videoCategories":
        return {"items": [{"id": "22", "snippet": {"title": "People & Blogs"}}]}
    raise AssertionError(endpoint)


def test_video_list_job_produces_files_and_progress(tmp_path, make_client):
    def factory(**kwargs):
        client = make_client(handler)
        client.on_progress = kwargs.get("on_progress")
        return client

    job = Job(
        module="video-list",
        params={"ids": ["v1", "v2"], "cotag": True},
        output_dir=tmp_path,
    )
    job.start(client_factory=factory)
    job.wait(5)
    assert job.status == "done", job.error
    assert job.summary == "2 videos"
    assert len(job.files) == 3  # csv + tagnet + sharedtagnet
    assert all(path.exists() for path in job.files)
    assert job.calls >= 1
    # progress flowed from the client's on_progress into the job state
    assert job.desc == "video details"
    assert job.done == job.total == 1


def test_job_surfaces_errors(tmp_path, make_client):
    def factory(**kwargs):
        return make_client(handler)

    job = Job(module="video-list", params={}, output_dir=tmp_path)
    job.start(client_factory=factory)
    job.wait(5)
    assert job.status == "error"
    assert "Provide a source" in job.error


def test_channel_info_job_returns_display_data(tmp_path, make_client):
    channel_id = "UCabcdefghijklmnopqrstuv"

    def info_handler(endpoint, params):
        if endpoint == "channels":
            return {
                "items": [{
                    "id": channel_id,
                    "snippet": {"title": "Test Channel", "publishedAt": "2020-01-01T00:00:00Z"},
                    "statistics": {"subscriberCount": "12345", "viewCount": "1", "videoCount": "2"},
                    "contentDetails": {"relatedPlaylists": {"uploads": "UUabc"}},
                }]
            }
        if endpoint == "playlists":
            assert params["channelId"] == channel_id
            return {
                "items": [{
                    "id": "PL1",
                    "snippet": {
                        "title": "Lectures",
                        "publishedAt": "2021-01-01T00:00:00Z",
                        "description": "a\nb",
                    },
                    "contentDetails": {"itemCount": 4},
                }]
            }
        raise AssertionError(endpoint)

    def factory(**kwargs):
        return make_client(info_handler)

    job = Job(module="channel-info", params={"channel": channel_id}, output_dir=tmp_path)
    job.start(client_factory=factory)
    job.wait(5)
    assert job.status == "done", job.error
    assert job.summary == "Test Channel — 12,345 subscribers, 1 public playlist"
    assert job.data["title"] == "Test Channel"
    assert job.data["uploadsPlaylist"] == "UUabc"
    # the public playlists land in a CSV output
    assert len(job.files) == 1
    content = job.files[0].read_text(encoding="utf-8")
    assert "PL1" in content
    assert "https://www.youtube.com/playlist?list=PL1" in content
    assert "Lectures" in content


def test_job_report_covers_params_files_and_links(tmp_path, make_client):
    def factory(**kwargs):
        return make_client(handler)

    job = Job(
        module="video-list",
        params={"ids": ["v1", "v2"], "cotag": True, "query": ""},
        output_dir=tmp_path,
    )
    job.start(client_factory=factory)
    job.wait(5)
    assert job.status == "done", job.error

    report = job.report(lambda path: f"http://host/files/{path.name}")
    assert "YouTube Data Tools — Video List" in report
    assert f"Run: {job.started} → {job.finished}" in report
    assert "ids: v1, v2" in report
    assert "cotag: yes" in report
    assert "query" not in report  # empty parameters are omitted
    assert "Result: 2 videos" in report
    # one line per file: the re-download link with content stats appended
    assert f"http://host/files/{job.files[0].name} (2 rows)" in report
    assert "2 nodes" in report and "1 edge" in report  # proper singular
    for path in job.files:
        assert f"http://host/files/{path.name} (" in report
    # without a URL builder the report falls back to plain file names
    plain = job.report()
    assert "http://host" not in plain
    assert f"{job.files[0].name} (2 rows)" in plain


def test_all_modules_registered():
    assert set(MODULES_BY_SLUG) == {
        "channel-info",
        "channel-list",
        "channel-network",
        "video-list",
        "trending-videos",
        "video-comments",
        "cocomment-network",
    }
