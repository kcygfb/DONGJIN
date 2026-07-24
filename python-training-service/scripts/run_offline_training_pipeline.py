from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SERVICE_DIR = Path(__file__).resolve().parents[1]
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from app.grid.datasets.builder import (  # noqa: E402
    DatasetBuildRequest,
    build_dataset,
)
from app.grid.scenarios.models import (  # noqa: E402
    EventType,
    ScenarioBatchRequest,
)
from app.grid.scenarios.service import (  # noqa: E402
    generate_scenario_batch,
)
from app.grid.training.trainer import (  # noqa: E402
    OfflineTrainingRequest,
    train_offline_model,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate visible offline grid scenarios, build a white-box "
            "GNN dataset, and train one versioned GCN model."
        )
    )
    parser.add_argument(
        "--samples-per-type",
        type=int,
        default=8,
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=20260723,
    )
    parser.add_argument(
        "--event-types",
        nargs="+",
        choices=[value.value for value in EventType],
        default=[value.value for value in EventType],
    )
    parser.add_argument("--maximum-epochs", type=int, default=120)
    args = parser.parse_args()

    batch = generate_scenario_batch(
        ScenarioBatchRequest(
            samplesPerType=args.samples_per_type,
            randomSeed=args.random_seed,
            eventTypes=[
                EventType(value) for value in args.event_types
            ],
        )
    )
    dataset = build_dataset(
        DatasetBuildRequest(
            batchId=batch["batchId"],
            randomSeed=args.random_seed,
        )
    )
    model = train_offline_model(
        OfflineTrainingRequest(
            datasetId=dataset["datasetId"],
            randomSeed=args.random_seed,
            maximumEpochs=args.maximum_epochs,
        )
    )
    print(
        json.dumps(
            {
                "scenarioBatch": {
                    "batchId": batch["batchId"],
                    "artifactPath": batch["artifactPath"],
                    "indexPath": batch["indexPath"],
                },
                "dataset": {
                    "datasetId": dataset["datasetId"],
                    "artifactPath": dataset["artifactPath"],
                    "visibleSamplesPath": dataset[
                        "visibleSamplesPath"
                    ],
                    "previewPath": dataset["previewPath"],
                },
                "model": {
                    "modelId": model["modelId"],
                    "status": model["status"],
                    "artifactPath": model["artifactPath"],
                    "metricsPath": model["metricsPath"],
                    "testPredictionsPath": model[
                        "testPredictionsPath"
                    ],
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
