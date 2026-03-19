"""Thin adapter layer over the sibling HueVault package."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from .bootstrap import APP_ROOT, ensure_local_paths
from .rendering import hue_difference, lab_to_hex

ensure_local_paths()

from huevault.converter import ColourConverter  # type: ignore[import-not-found]
from huevault.models import (  # type: ignore[import-not-found]
    CollisionMode,
    ColourFormat,
    ColourInput,
    DeduplicationMode,
    QuantizationConfig,
    RelationshipConfig,
    RuntimeConfig,
    SimilarityMode,
    SimilarityPolicy,
)
from huevault.relationships import RelationshipEngine  # type: ignore[import-not-found]
from huevault.service import HueVaultService  # type: ignore[import-not-found]
from huevault.similarity import SimilarityEngine  # type: ignore[import-not-found]


DEFAULT_RUNTIME_CONFIG = RuntimeConfig(
    quantization=QuantizationConfig(),
    deduplication_mode=DeduplicationMode.FLAG_ONLY,
    collision_mode=CollisionMode.ALLOW,
    relationships=RelationshipConfig(),
)


APP_POLICY_PRESETS: dict[str, SimilarityPolicy] = {
    "identity_match": SimilarityPolicy("identity_match", 0.5, SimilarityMode.THRESHOLD, description="Indistinguishable match"),
    "production_safe": SimilarityPolicy("production_safe", 1.5, SimilarityMode.THRESHOLD, description="Strict production-safe matching"),
    "visual_match": SimilarityPolicy("visual_match", 3.0, SimilarityMode.THRESHOLD, description="Comfortable visual similarity"),
    "close_match": SimilarityPolicy("close_match", 5.0, SimilarityMode.THRESHOLD, description="Tight perceptual grouping"),
    "design_variation": SimilarityPolicy("design_variation", 10.0, SimilarityMode.COMBINED, top_k=12, description="Broader design exploration"),
    "broad_similarity": SimilarityPolicy("broad_similarity", 18.0, SimilarityMode.THRESHOLD, description="Wide but bounded similarity"),
    "nearest_neighbours": SimilarityPolicy("nearest_neighbours", 100.0, SimilarityMode.TOP_K, top_k=8, description="Rank-only nearest neighbours"),
}


def get_service(db_path: str) -> HueVaultService:
    service = HueVaultService(db_path=db_path, runtime_config=DEFAULT_RUNTIME_CONFIG)
    for policy in APP_POLICY_PRESETS.values():
        service.register_policy(policy)
    return service


def parse_json_payload(text: str) -> list[dict[str, Any]]:
    payload = json.loads(text)
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("colours", "palette", "input_swatches", "canonical_colours"):
            value = payload.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, dict)]
        return [payload]
    raise ValueError("Unsupported JSON payload shape")


def parse_csv_payload(text: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text))
    return [dict(row) for row in reader]


def load_records_from_file(path: str) -> tuple[list[dict[str, Any]], str]:
    source_path = Path(path)
    text = source_path.read_text(encoding="utf-8-sig")
    if source_path.suffix.lower() == ".json":
        return parse_json_payload(text), source_path.name
    if source_path.suffix.lower() == ".csv":
        return parse_csv_payload(text), source_path.name
    raise ValueError("Only JSON and CSV files are supported")


def load_records_from_upload(file_name: str, content: bytes) -> tuple[list[dict[str, Any]], str]:
    text = content.decode("utf-8-sig")
    suffix = Path(file_name).suffix.lower()
    if suffix == ".json":
        return parse_json_payload(text), file_name
    if suffix == ".csv":
        return parse_csv_payload(text), file_name
    raise ValueError("Only JSON and CSV uploads are supported")


def sample_files() -> list[Path]:
    sample_dir = APP_ROOT / "sample_data"
    if not sample_dir.exists():
        return []
    return sorted(path for path in sample_dir.iterdir() if path.suffix.lower() in {".json", ".csv"})


def normalize_source_values(source_format: str, raw: Any) -> list[float] | str:
    fmt = source_format.lower()
    if fmt == "hex":
        if isinstance(raw, str):
            stripped = raw.strip()
            if stripped.startswith("{"):
                raw = json.loads(stripped)
        if isinstance(raw, dict):
            value = raw.get("hex")
            if value is None:
                raise ValueError("HEX row is missing 'hex'")
            return str(value)
        return str(raw).strip()
    if fmt == "rgb":
        if isinstance(raw, str):
            raw = json.loads(raw)
        if isinstance(raw, dict):
            return [float(raw["r"]), float(raw["g"]), float(raw["b"])]
        return [float(value) for value in raw]
    if fmt == "cmyk":
        if isinstance(raw, str):
            raw = json.loads(raw)
        if isinstance(raw, dict):
            return [float(raw["c"]), float(raw["m"]), float(raw["y"]), float(raw["k"])]
        return [float(value) for value in raw]
    if fmt == "lab":
        if isinstance(raw, str):
            raw = json.loads(raw)
        if isinstance(raw, dict):
            return [float(raw["l"]), float(raw["a"]), float(raw["b"])]
        return [float(value) for value in raw]
    raise ValueError(f"Unsupported source format: {source_format}")


def mapping_to_colour_input(mapping: dict[str, Any], index: int) -> ColourInput:
    if {"lab_l", "lab_a", "lab_b"}.issubset(mapping):
        source_values = [float(mapping["lab_l"]), float(mapping["lab_a"]), float(mapping["lab_b"])]
        return ColourInput(
            input_id=str(mapping.get("input_id") or mapping.get("colour_id") or f"row-{index:05d}"),
            source_id=str(mapping.get("source_id") or mapping.get("colour_id") or "") or None,
            name=str(mapping.get("name") or mapping.get("approximate_label") or "") or None,
            source_format=ColourFormat.LAB,
            source_values=source_values,
            source_profile=str(mapping.get("source_profile") or "") or None,
            provenance=_normalize_provenance(mapping.get("provenance"), mapping),
        )

    raw_source_values = mapping.get("source_values", mapping.get("raw_source_values"))
    source_format = str(mapping.get("source_format", "")).lower()
    if not source_format:
        raise ValueError("Row is missing source_format")
    if raw_source_values is None:
        raise ValueError("Row is missing source_values or raw_source_values")

    return ColourInput(
        input_id=str(mapping.get("input_id") or mapping.get("swatch_record_id") or mapping.get("source_name") or f"row-{index:05d}"),
        source_id=str(mapping.get("source_id") or mapping.get("source_palette_id") or mapping.get("embedded_colour_id") or "") or None,
        name=str(mapping.get("name") or mapping.get("source_name") or "") or None,
        source_format=ColourFormat(source_format),
        source_values=normalize_source_values(source_format, raw_source_values),
        source_profile=str(mapping.get("source_profile") or "") or None,
        provenance=_normalize_provenance(mapping.get("provenance"), mapping),
    )


def _normalize_provenance(provenance: Any, mapping: dict[str, Any]) -> dict[str, Any]:
    if provenance is None or provenance == "":
        data: dict[str, Any] = {}
    elif isinstance(provenance, dict):
        data = dict(provenance)
    elif isinstance(provenance, str):
        data = json.loads(provenance)
    else:
        raise ValueError("Provenance must be a JSON object or JSON string")

    extras = {}
    for key in (
        "source_palette_id",
        "swatch_record_id",
        "canonical_colour_id",
        "metadata_issue",
        "palette_name",
        "approximate_label",
    ):
        if mapping.get(key) not in (None, ""):
            extras[key] = mapping[key]
    if extras:
        data.setdefault("ui_import_metadata", extras)
    return data


def ingest_records(service: HueVaultService, records: list[dict[str, Any]], source_name: str) -> dict[str, Any]:
    ingested = 0
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for index, row in enumerate(records, start=1):
        try:
            colour_input = mapping_to_colour_input(row, index)
            stored = service.ingest_colour(colour_input)
            ingested += 1
            if stored.status != "active":
                warnings.append(
                    {
                        "source": source_name,
                        "row_number": index,
                        "level": "warning",
                        "message": f"Stored with status '{stored.status}'",
                        "input_id": colour_input.input_id,
                        "catalogue_id": stored.catalogue_id,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(
                {
                    "source": source_name,
                    "row_number": index,
                    "level": "error",
                    "message": str(exc),
                    "row_preview": json.dumps(row, sort_keys=True, ensure_ascii=True)[:500],
                }
            )

    return {
        "source": source_name,
        "attempted": len(records),
        "ingested": ingested,
        "warnings": warnings,
        "errors": errors,
    }


def get_catalogue_rows(service: HueVaultService) -> list[dict[str, Any]]:
    with service.catalogue._connect() as conn:
        rows = conn.execute("SELECT * FROM colours ORDER BY order_key, catalogue_id").fetchall()
    return [dict(row) for row in rows]


def get_colour_row(service: HueVaultService, identifier: str) -> dict[str, Any] | None:
    with service.catalogue._connect() as conn:
        row = conn.execute("SELECT * FROM colours WHERE catalogue_id = ?", (identifier,)).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM colours WHERE colour_id = ? ORDER BY catalogue_id LIMIT 1",
                (identifier,),
            ).fetchone()
    return dict(row) if row is not None else None


def resolve_policy(service: HueVaultService, preset_id: str, delta_e_threshold: float | None, mode: str | None, top_k: int | None) -> SimilarityPolicy:
    policy = service.get_policy(preset_id)
    selected_mode = SimilarityMode(mode) if mode else policy.mode
    return replace(
        policy,
        delta_e_threshold=delta_e_threshold if delta_e_threshold is not None else policy.delta_e_threshold,
        mode=selected_mode,
        top_k=top_k if top_k is not None else policy.top_k,
    )


def canonicalize_input(colour_input: ColourInput) -> dict[str, Any]:
    canonical = ColourConverter.canonicalize(colour_input, DEFAULT_RUNTIME_CONFIG.quantization)
    return {
        "colour_input": colour_input,
        "canonical": canonical,
        "hex": lab_to_hex(canonical.lab),
    }


def run_similarity(
    service: HueVaultService,
    query_identifier: str | None,
    manual_query: ColourInput | None,
    policy: SimilarityPolicy,
    hue_tolerance: float | None = None,
) -> list[dict[str, Any]]:
    candidates = service.catalogue.get_all_colours(include_inactive=True)
    if query_identifier:
        query_colour = service.get_colour(query_identifier)
    elif manual_query is not None:
        query_colour = ColourConverter.canonicalize(manual_query, DEFAULT_RUNTIME_CONFIG.quantization)
    else:
        raise ValueError("A similarity query requires either a catalogue identifier or a manual query colour")
    engine = SimilarityEngine()
    results = engine.search(query_colour, candidates, policy)

    row_lookup = {row["catalogue_id"]: row for row in get_catalogue_rows(service)}
    enriched: list[dict[str, Any]] = []
    for result in results:
        row = row_lookup.get(result.candidate_catalogue_id or "")
        if row is None:
            continue
        hue_gap = hue_difference(query_colour.lch_h, float(row["lch_h"]))
        if hue_tolerance is not None and hue_gap > hue_tolerance:
            continue
        enriched.append(
            {
                "query_colour_id": result.query_colour_id,
                "query_catalogue_id": getattr(query_colour, "catalogue_id", None),
                "candidate_catalogue_id": row["catalogue_id"],
                "candidate_colour_id": row["colour_id"],
                "name": row["name"],
                "delta_e": round(result.delta_e, 4),
                "relationship_band": result.relationship_type,
                "rank": len(enriched) + 1,
                "status": row["status"],
                "collision_group_id": row.get("collision_group_id"),
                "collision_rank": int(row.get("collision_rank") or 1),
                "collision_origin_catalogue_id": row.get("collision_origin_catalogue_id"),
                "is_collision_member": bool(row.get("is_collision_member")),
                "is_collision_origin": bool(row.get("is_collision_origin")),
                "hex": lab_to_hex((float(row["lab_l"]), float(row["lab_a"]), float(row["lab_b"]))),
                "hue_difference": round(hue_gap, 3),
                "provenance": row["provenance"],
            }
        )
    return enriched


def run_relationship_query(
    service: HueVaultService,
    query_identifier: str | None,
    manual_query: ColourInput | None,
    relationship_type: str,
    relationship_config: RelationshipConfig,
) -> list[dict[str, Any]]:
    candidates = service.catalogue.get_all_colours(include_inactive=True)
    if query_identifier:
        query_colour = service.get_colour(query_identifier)
    elif manual_query is not None:
        query_colour = ColourConverter.canonicalize(manual_query, DEFAULT_RUNTIME_CONFIG.quantization)
    else:
        raise ValueError("A relationship query requires either a catalogue identifier or a manual query colour")
    engine = RelationshipEngine(relationship_config)
    results = engine.find_relationships(query_colour, relationship_type, candidates)
    row_lookup = {row["catalogue_id"]: row for row in get_catalogue_rows(service)}

    enriched: list[dict[str, Any]] = []
    for index, result in enumerate(results, start=1):
        row = row_lookup.get(result.candidate_catalogue_id or "")
        if row is None:
            continue
        hue_gap = hue_difference(query_colour.lch_h, float(row["lch_h"]))
        enriched.append(
            {
                "query_colour_id": result.query_colour_id,
                "query_catalogue_id": getattr(query_colour, "catalogue_id", None),
                "candidate_catalogue_id": row["catalogue_id"],
                "candidate_colour_id": row["colour_id"],
                "name": row["name"],
                "relationship_type": result.relationship_type,
                "score": round(result.score or 0.0, 6),
                "rank": index,
                "hue_difference": round(hue_gap, 3),
                "status": row["status"],
                "collision_group_id": row.get("collision_group_id"),
                "collision_rank": int(row.get("collision_rank") or 1),
                "collision_origin_catalogue_id": row.get("collision_origin_catalogue_id"),
                "is_collision_member": bool(row.get("is_collision_member")),
                "is_collision_origin": bool(row.get("is_collision_origin")),
                "hex": lab_to_hex((float(row["lab_l"]), float(row["lab_a"]), float(row["lab_b"]))),
                "provenance": row["provenance"],
            }
        )
    return enriched


def current_catalogue_snapshot(service: HueVaultService) -> tuple[str, str]:
    rows = get_catalogue_rows(service)
    json_payload = json.dumps(rows, indent=2, sort_keys=True, ensure_ascii=True)
    if not rows:
        return json_payload, ""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=sorted(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return json_payload, buffer.getvalue()


def diagnostics_report(service: HueVaultService) -> dict[str, Any]:
    rows = get_catalogue_rows(service)
    duplicate_lab: dict[tuple[float, float, float], list[str]] = {}
    id_collisions: dict[str, list[str]] = {}
    missing_provenance: list[str] = []
    bounds_issues: list[str] = []
    lch_mismatches: list[str] = []
    id_mismatches: list[str] = []
    reproducibility_issues: list[str] = []

    for row in rows:
        catalogue_id = row["catalogue_id"]
        lab = (float(row["lab_l"]), float(row["lab_a"]), float(row["lab_b"]))
        lch = (float(row["lch_l"]), float(row["lch_c"]), float(row["lch_h"]))

        duplicate_lab.setdefault(tuple(round(value, 9) for value in lab), []).append(catalogue_id)
        id_collisions.setdefault(str(row["colour_id"]), []).append(catalogue_id)

        if row["provenance"] in (None, "", "{}"):
            missing_provenance.append(catalogue_id)
        if not (0.0 <= lab[0] <= 100.0 and -128.0 <= lab[1] <= 128.0 and -128.0 <= lab[2] <= 128.0):
            bounds_issues.append(f"{catalogue_id}: LAB out of bounds")
        if lch[1] < 0.0 or not (0.0 <= lch[2] <= 360.0):
            bounds_issues.append(f"{catalogue_id}: LCh out of bounds")

        recalculated_lch = ColourConverter.lab_to_lch(lab)
        if any(abs(left - right) > 1e-6 for left, right in zip(recalculated_lch, lch)):
            lch_mismatches.append(catalogue_id)

        expected_id = ColourConverter.generate_colour_id(recalculated_lch, DEFAULT_RUNTIME_CONFIG.quantization)
        if expected_id != row["colour_id"]:
            id_mismatches.append(catalogue_id)

        colour_input = ColourInput(
            input_id=str(row["input_id"]),
            source_id=row["source_id"],
            name=row["name"],
            source_format=ColourFormat(str(row["source_format"])),
            source_values=normalize_source_values(str(row["source_format"]), json.loads(row["source_values"])),
            source_profile=row["source_profile"],
            provenance=json.loads(row["provenance"]),
        )
        replay = ColourConverter.canonicalize(colour_input, DEFAULT_RUNTIME_CONFIG.quantization)
        if replay.colour_id != row["colour_id"] or any(abs(left - right) > 1e-6 for left, right in zip(replay.lab, lab)):
            reproducibility_issues.append(catalogue_id)

    return {
        "catalogue_size": len(rows),
        "duplicate_lab_values": {str(key): value for key, value in duplicate_lab.items() if len(value) > 1},
        "id_collisions": {key: value for key, value in id_collisions.items() if len(value) > 1},
        "missing_provenance": missing_provenance,
        "bounds_issues": bounds_issues,
        "lab_lch_consistency_failures": lch_mismatches,
        "id_quantization_failures": id_mismatches,
        "reproducibility_failures": reproducibility_issues,
        "transformation_log_count": len(service.catalogue.get_transformation_log()),
    }
