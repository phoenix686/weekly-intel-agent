from discovery.story_clusterer import assign_story_clusters, canonicalize_url, diversify


def item(title, url, tags=None):
    return {"title": title, "url": url, "source": "blog_scrape", "text": title * 3,
            "fetched_at": "2026-07-29T00:00:00+00:00", "duplicate_count": 1,
            "tags": tags or []}


def test_canonical_url_removes_tracking():
    assert canonicalize_url("HTTPS://Example.com/a/?utm_source=x&b=2#x") == "https://example.com/a?b=2"


def test_same_story_keeps_supporting_coverage():
    stories = assign_story_clusters([
        item("OpenAI model escapes sandbox security test", "https://a/1"),
        item("OpenAI model escaped sandbox security test", "https://b/2"),
    ])
    assert len(stories) == 1
    assert len(stories[0]["supporting_sources"]) == 1


def test_diversify_selects_distinct_topics():
    values = [{**item(f"Agent {i}", f"https://a/{i}", ["agents"]), "keep": True}
              for i in range(3)]
    values.append({**item("Security", "https://s", ["security"]), "keep": True})
    assert {tuple(x["tags"]) for x in diversify(values, 2)} == {("agents",), ("security",)}
