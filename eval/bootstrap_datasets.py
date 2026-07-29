"""Upload immutable JSONL fixtures to LangSmith; never overwrites examples."""
from langsmith import Client
from dataset_seeds import DATASETS


def upload() -> None:
    client = Client()
    for name, rows in DATASETS.items():
        try:
            client.create_dataset(dataset_name=name, description="Weekly Intel versioned ground truth")
        except Exception:
            pass
        for row in rows:
            client.create_example(dataset_name=name, inputs=row["inputs"], outputs=row["outputs"],
                                  metadata={"split": row["split"], "version": "v1"})


if __name__ == "__main__":
    upload()
