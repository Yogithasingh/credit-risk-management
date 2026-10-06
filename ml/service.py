"""Loads versioned model artifacts and produces probabilities plus local sensitivities."""

from __future__ import annotations

import json
import hashlib
import threading
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from app import database
from ml.train import CATEGORICAL_FEATURES, FEATURE_LABELS, NUMERIC_FEATURES, train_model


class ModelService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._artifact: dict[str, Any] | None = None

    def startup(self) -> None:
        with database.connect() as db:
            active = db.execute("SELECT version FROM model_runs WHERE active=1 ORDER BY id DESC LIMIT 1").fetchone()
        if active:
            path = database.MODEL_DIR / f"{active['version']}.joblib"
            if path.exists():
                self._load(path)
                dataset_hash = hashlib.sha256(database.DATASET_PATH.read_bytes()).hexdigest()
                if self._artifact["metadata"].get("dataset", {}).get("sha256") == dataset_hash:
                    return
                with self._lock:
                    self._artifact = None
        self.train_and_activate()

    def _load(self, artifact_path: Path) -> None:
        artifact = joblib.load(artifact_path)
        if not isinstance(artifact, dict) or "model" not in artifact or "metadata" not in artifact:
            raise RuntimeError("The active model artifact has an unsupported format.")
        with self._lock:
            self._artifact = artifact

    def train_and_activate(self) -> dict[str, Any]:
        with self._lock:
            result = train_model(database.DATASET_PATH, database.MODEL_DIR)
            artifact = result["artifact"]
            metadata = result["metadata"]
            with database.connect() as db:
                db.execute("UPDATE model_runs SET active=0")
                db.execute(
                    "INSERT INTO model_runs(version,algorithm,trained_at,metadata,metrics,active) VALUES(?,?,?,?,?,1)",
                    (
                        metadata["model_version"],
                        metadata["algorithm"],
                        metadata["trained_at"],
                        json.dumps(metadata, separators=(",", ":")),
                        json.dumps(metadata["test_metrics"], separators=(",", ":")),
                    ),
                )
            self._artifact = artifact
            return metadata

    def is_ready(self) -> bool:
        return self._artifact is not None

    def info(self) -> dict[str, Any]:
        with self._lock:
            if self._artifact is None:
                raise RuntimeError("The model is not loaded.")
            return self._artifact["metadata"]

    def predict(self, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._artifact is None:
                raise RuntimeError("The model is not loaded.")
            artifact = self._artifact
            model = artifact["model"]
            current = pd.DataFrame([{feature: values.get(feature) for feature in artifact["features"]}])
            for feature in CATEGORICAL_FEATURES:
                current[feature] = current[feature].where(current[feature].notna(), float("nan"))
            classes = list(model.named_steps["classifier"].classes_)
            positive_index = classes.index(1)
            probability = float(model.predict_proba(current)[0, positive_index])
            factors: list[dict[str, Any]] = []
            baseline = artifact["baselines"]
            for feature in artifact["features"]:
                comparison = current.copy()
                comparison.loc[0, feature] = baseline[feature]
                reference_probability = float(model.predict_proba(comparison)[0, positive_index])
                delta = probability - reference_probability
                factors.append(
                    {
                        "feature": feature,
                        "label": FEATURE_LABELS[feature],
                        "value": values.get(feature),
                        "reference_value": baseline[feature],
                        "probability_change": round(delta, 4),
                        "direction": "higher" if delta > 0.0005 else "lower" if delta < -0.0005 else "little change",
                    }
                )
            factors.sort(key=lambda item: abs(item["probability_change"]), reverse=True)
            feature_ranges = artifact["feature_ranges"]
            warnings = []
            for feature in NUMERIC_FEATURES:
                observed = feature_ranges[feature]
                value = float(values.get(feature, observed["minimum"]))
                if value < observed["minimum"] or value > observed["maximum"]:
                    warnings.append(
                        {
                            "feature": feature,
                            "label": FEATURE_LABELS[feature],
                            "observed_minimum": round(observed["minimum"], 2),
                            "observed_maximum": round(observed["maximum"], 2),
                            "submitted_value": value,
                        }
                    )
            return {
                "probability_of_default": probability,
                "model_version": artifact["metadata"]["model_version"],
                "algorithm": artifact["metadata"]["algorithm"],
                "factors": factors[:5],
                "range_warnings": warnings,
            }


model_service = ModelService()
