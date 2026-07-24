import json
from pathlib import Path

import pandapower as pp

from app.grid.artifact_service import initialize_grid_package
from app.grid.settings import GridSettings


def test_generates_reloadable_and_reproducible_simbench_package(tmp_path: Path) -> None:
    settings = GridSettings(data_dir=tmp_path)

    first = initialize_grid_package(
        simbench_code="1-MV-urban--0-sw",
        topology_version="v1",
        force=True,
        settings=settings,
    )

    artifact_dir = Path(first["artifactPath"])
    expected_files = {
        "network.json",
        "std-types.json",
        "manifest.json",
        "id-mapping.json",
        "topology.json",
        "profile-metadata.json",
        "baseline-results.json",
    }
    assert {path.name for path in artifact_dir.iterdir()} == expected_files
    assert first["status"] == "generated"
    assert first["elementCounts"]["bus"] == 144
    assert first["elementCounts"]["line"] == 147
    assert first["elementCounts"]["trafo"] == 2
    assert first["elementCounts"]["switch"] == 305
    assert first["elementCounts"]["load"] == 139
    assert first["elementCounts"]["sgen"] == 134
    assert first["profileSummary"]["timeSteps"] == 35136
    assert first["baseline"]["converged"] is True
    assert first["validation"]["status"] == "passed"

    restored = pp.from_json(str(artifact_dir / "network.json"))
    assert len(restored.bus) == 144
    assert len(restored.line) == 147
    assert restored.profiles["load"].shape[0] == 35136

    mapping = json.loads((artifact_dir / "id-mapping.json").read_text(encoding="utf-8"))
    business_ids = [entry["businessId"] for entry in mapping["entries"]]
    assert len(business_ids) == len(set(business_ids))

    reused = initialize_grid_package(
        simbench_code="1-MV-urban--0-sw",
        topology_version="v1",
        force=False,
        settings=settings,
    )
    assert reused["status"] == "reused"
    assert reused["checksums"] == first["checksums"]

    regenerated = initialize_grid_package(
        simbench_code="1-MV-urban--0-sw",
        topology_version="v1",
        force=True,
        settings=settings,
    )
    assert regenerated["checksums"] == first["checksums"]
