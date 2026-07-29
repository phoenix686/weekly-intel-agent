from eval.dataset_seeds import DATASETS
from eval.evaluators import classification_metrics, digest_checks, ndcg_at_k
from discovery.story_clusterer import assign_story_clusters


def main():
    expected, actual = [], []
    for row in DATASETS["intel-dedup-v1"]:
        left, right = row["inputs"]["left"], row["inputs"]["right"]
        clustered = assign_story_clusters([
            {**left, "source": "blog_scrape", "text": left["title"], "duplicate_count": 1},
            {**right, "source": "blog_scrape", "text": right["title"], "duplicate_count": 1},
        ])
        expected.append(row["outputs"]["class"])
        actual.append("same_story" if len(clustered) == 1 else "unrelated")
    assert classification_metrics(expected, actual, "same_story")["recall"] >= 0.8
    ranking = DATASETS["intel-ranking-v1"]
    assert ndcg_at_k([row["outputs"]["relevance"] for row in ranking[:10]]) >= 0.5
    assert all(digest_checks("<b>ok</b>", {}).values())
    print("executable eval smoke gates passed")


if __name__ == "__main__":
    main()
