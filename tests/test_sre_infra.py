def test_index_loads(client):
    resp = client.get("/projects/sre-infra")
    assert resp.status_code == 200
    assert b"SRE Infra Layer" in resp.data
    assert b"cache-aside" in resp.data.lower()


def test_index_shows_fakeredis_backend_by_default(client):
    resp = client.get("/projects/sre-infra")
    assert b"fakeredis" in resp.data


def test_api_stats_returns_queue_and_cache_shape(client):
    resp = client.get("/projects/sre-infra/api/stats")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "queued" in data["queue"]
    assert "failed" in data["queue"]
    assert "hits" in data["cache"]
    assert "misses" in data["cache"]


def test_cache_stats_reflect_a_real_miss_then_hit(client, app):
    from app.services import world_cache

    with app.app_context():
        world_cache.get_live_world()  # miss (empty cache) -> populates it
        stats_after_miss = world_cache.get_cache_stats()
        world_cache.get_live_world()  # hit
        stats_after_hit = world_cache.get_cache_stats()

    assert stats_after_hit["misses"] == stats_after_miss["misses"]
    assert stats_after_hit["hits"] == stats_after_miss["hits"] + 1
