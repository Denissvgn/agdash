"""The synthetic store behind the figure screens' acceptance run fills every figure."""

from datetime import UTC, datetime, timedelta

from screens import LONG_TITLE, build
from test_api import local_client


def test_every_figure_screen_has_data(tmp_path):
    # The live board reads the last 30 minutes of the real clock, so the store is built at it.
    now = datetime.now(UTC)
    url = f"sqlite:///{tmp_path / 'screens.db'}"
    ids = build(url, now)
    since = (now - timedelta(days=30)).isoformat()
    with local_client(url) as client:

        def get(path, **params):
            answer = client.get(f"/api/{path}", params=params)
            assert answer.status_code == 200, (path, answer.text)
            return answer.json()

        # History and Analytics: sessions started per day, usage, tools and activity.
        assert sum(day["sessions"] for day in get("analytics/started")["days"]) > 50
        assert len(get("analytics/usage", since=since)["rows"]) == 4
        assert get("analytics/tools", since=since)["errors"] > 0
        assert len(get("analytics/series", since=since)["series"]) == 4
        assert any(get("analytics/activity", since=since)["hour_of_week"])

        # Live: nested sessions with recent events.
        assert len(get("live/activity")["sessions"]) == 5

        # A session's page: a long session across a night, linked to a run.
        long = get(f"sessions/{ids['long_session']}")
        assert long["title"] == LONG_TITLE and long["child_count"] == 1
        mapping = get(f"sessions/{ids['long_session']}/mapping")["connections"][0]
        assert mapping["runs"] and mapping["project"]["name"] == "Orchard"

        # Projects: activity lanes per project, and agents both declared and observed.
        lanes = get("projects/activity", days=7)["lanes"]
        assert {lane["project"]["name"] for lane in lanes if lane["project"]} == {
            "Orchard",
            "Meadow",
        }
        orchard = next(
            group
            for group in get("projects/board")["groups"]
            if group["project"] and group["project"]["name"] == "Orchard"
        )
        agents = {
            agent["name"]: (agent["assignments"], agent["live_sessions"])
            for agent in orchard["agents"]
        }
        assert agents == {"Robin": (2, 0), "Wren": (1, 1)}

        # Settings: files read per harness, a format that changed lately, archived lines.
        read = {source["harness"]: source for source in get("sources")["sources"]}
        assert read["claude_code"]["files"] == 420 and read["pi"]["files"] == 0
        assert [item["recent"] for item in read["claude_code"]["formats"]] == [True, False]
        assert get("storage")["store"]["sessions"] > 50
        assert get("storage/archive")["chunks"] == 6
