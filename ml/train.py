"""Reproducible LendingClub default model training and evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sklearn
import xgboost
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import RandomizedSearchCV, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.svm import SVC
from sklearn.tree import DecisionTreeClassifier
from xgboost import XGBClassifier


RANDOM_STATE = 42
NUMERIC_FEATURES = [
    "loan_amount",
    "term_months",
    "annual_income",
    "dti",
    "prior_delinquencies",
    "fico_score",
    "recent_credit_inquiries",
    "open_accounts",
    "public_records",
    "revolving_balance",
    "revolving_utilization",
    "total_accounts",
]
CATEGORICAL_FEATURES = ["home_ownership", "purpose", "employment_length"]
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES
FEATURE_LABELS = {
    "loan_amount": "Requested loan amount",
    "term_months": "Loan term",
    "annual_income": "Annual income",
    "home_ownership": "Home ownership",
    "purpose": "Loan purpose",
    "dti": "Debt-to-income ratio",
    "prior_delinquencies": "Delinquencies in prior two years",
    "fico_score": "FICO score midpoint",
    "recent_credit_inquiries": "Recent credit inquiries",
    "open_accounts": "Open credit accounts",
    "public_records": "Public records",
    "revolving_balance": "Revolving balance",
    "revolving_utilization": "Revolving utilization",
    "total_accounts": "Total credit accounts",
    "employment_length": "Employment length",
}


def _make_features(source: pd.DataFrame) -> pd.DataFrame:
    fico_low = pd.to_numeric(source["fico_range_low"], errors="coerce")
    fico_high = pd.to_numeric(source["fico_range_high"], errors="coerce")
    result = pd.DataFrame(index=source.index)
    result["loan_amount"] = pd.to_numeric(source["loan_amnt"], errors="coerce")
    result["term_months"] = pd.to_numeric(source["term"].astype("string").str.extract(r"(\d+)", expand=False), errors="coerce")
    result["annual_income"] = pd.to_numeric(source["annual_inc"], errors="coerce")
    result["dti"] = pd.to_numeric(source["dti"], errors="coerce")
    result["prior_delinquencies"] = pd.to_numeric(source["delinq_2yrs"], errors="coerce")
    result["fico_score"] = (fico_low + fico_high) / 2
    result["recent_credit_inquiries"] = pd.to_numeric(source["inq_last_6mths"], errors="coerce")
    result["open_accounts"] = pd.to_numeric(source["open_acc"], errors="coerce")
    result["public_records"] = pd.to_numeric(source["pub_rec"], errors="coerce")
    result["revolving_balance"] = pd.to_numeric(source["revol_bal"], errors="coerce")
    result["revolving_utilization"] = pd.to_numeric(source["revol_util"], errors="coerce")
    result["total_accounts"] = pd.to_numeric(source["total_acc"], errors="coerce")
    result["home_ownership"] = source["home_ownership"].fillna(np.nan)
    result["purpose"] = source["purpose"].fillna(np.nan)
    result["employment_length"] = source["emp_length"].replace("", np.nan)
    return result[FEATURES]


def _pipeline(classifier: Any) -> Pipeline:
    numeric = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
            ("scaler", StandardScaler()),
        ]
    )
    categorical = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("encoder", OneHotEncoder(handle_unknown="ignore")),
        ]
    )
    preprocess = ColumnTransformer(
        transformers=[
            ("numeric", numeric, NUMERIC_FEATURES),
            ("categorical", categorical, CATEGORICAL_FEATURES),
        ],
        remainder="drop",
    )
    return Pipeline([("preprocess", preprocess), ("classifier", classifier)])


def _candidate_models() -> dict[str, Pipeline]:
    return {
        "Logistic Regression": _pipeline(
            LogisticRegression(max_iter=2000, class_weight="balanced", random_state=RANDOM_STATE)
        ),
        "Decision Tree": _pipeline(
            DecisionTreeClassifier(max_depth=5, min_samples_leaf=8, class_weight="balanced", random_state=RANDOM_STATE)
        ),
        "Random Forest": _pipeline(
            RandomForestClassifier(
                n_estimators=250,
                max_depth=None,
                min_samples_leaf=5,
                class_weight="balanced",
                random_state=RANDOM_STATE,
                n_jobs=1,
            )
        ),
        "Support Vector Machine": _pipeline(
            SVC(C=1.0, probability=True, class_weight="balanced", random_state=RANDOM_STATE)
        ),
        "XGBoost": _pipeline(
            XGBClassifier(
                objective="binary:logistic",
                eval_metric="logloss",
                tree_method="hist",
                n_estimators=180,
                max_depth=3,
                learning_rate=0.05,
                subsample=0.85,
                colsample_bytree=0.85,
                scale_pos_weight=4.0,
                n_jobs=1,
                random_state=RANDOM_STATE,
                verbosity=0,
            )
        ),
    }


def _probabilities(estimator: Pipeline, features: pd.DataFrame) -> np.ndarray:
    classes = list(estimator.named_steps["classifier"].classes_)
    positive_index = classes.index(1)
    return estimator.predict_proba(features)[:, positive_index]


def _metrics(labels: pd.Series | np.ndarray, probabilities: np.ndarray) -> dict[str, Any]:
    predicted = (probabilities >= 0.5).astype(int)
    matrix = confusion_matrix(labels, predicted, labels=[0, 1])
    return {
        "accuracy": round(float(accuracy_score(labels, predicted)), 4),
        "precision": round(float(precision_score(labels, predicted, zero_division=0)), 4),
        "recall": round(float(recall_score(labels, predicted, zero_division=0)), 4),
        "f1": round(float(f1_score(labels, predicted, zero_division=0)), 4),
        "roc_auc": round(float(roc_auc_score(labels, probabilities)), 4),
        "pr_auc": round(float(average_precision_score(labels, probabilities)), 4),
        "confusion_matrix": {"true_negative": int(matrix[0, 0]), "false_positive": int(matrix[0, 1]), "false_negative": int(matrix[1, 0]), "true_positive": int(matrix[1, 1])},
        "classification_threshold": 0.5,
    }


def _search_space(name: str) -> tuple[Pipeline, dict[str, list[Any]], int]:
    if name == "Logistic Regression":
        return (
            _pipeline(LogisticRegression(max_iter=2000, random_state=RANDOM_STATE)),
            {"classifier__C": [0.1, 1.0, 10.0], "classifier__class_weight": [None, "balanced"]},
            5,
        )
    if name == "Decision Tree":
        return (
            _pipeline(DecisionTreeClassifier(random_state=RANDOM_STATE)),
            {"classifier__max_depth": [3, 5, 8, None], "classifier__min_samples_leaf": [4, 8, 16], "classifier__class_weight": [None, "balanced"]},
            8,
        )
    if name == "Random Forest":
        return (
            _pipeline(RandomForestClassifier(n_estimators=250, random_state=RANDOM_STATE, n_jobs=1)),
            {"classifier__max_depth": [None, 8, 14], "classifier__min_samples_leaf": [3, 6, 10], "classifier__max_features": ["sqrt", 0.8], "classifier__class_weight": ["balanced", "balanced_subsample"]},
            8,
        )
    if name == "XGBoost":
        return (
            _pipeline(
                XGBClassifier(
                    objective="binary:logistic",
                    eval_metric="logloss",
                    tree_method="hist",
                    n_estimators=180,
                    n_jobs=1,
                    random_state=RANDOM_STATE,
                    verbosity=0,
                )
            ),
            {
                "classifier__max_depth": [2, 3, 4],
                "classifier__learning_rate": [0.03, 0.07, 0.12],
                "classifier__min_child_weight": [1, 5],
                "classifier__scale_pos_weight": [1.0, 4.0, 5.0],
                "classifier__reg_lambda": [1.0, 5.0],
            },
            8,
        )
    return (
        _pipeline(SVC(probability=True, random_state=RANDOM_STATE)),
        {"classifier__C": [0.1, 1.0, 10.0], "classifier__class_weight": [None, "balanced"], "classifier__gamma": ["scale", "auto"]},
        8,
    )


def train_model(dataset_path: Path, artifact_dir: Path) -> dict[str, Any]:
    if not dataset_path.exists():
        raise FileNotFoundError(f"Training dataset was not found: {dataset_path}")
    source = pd.read_csv(dataset_path, low_memory=False)
    required = {
        "id", "loan_status", "loan_amnt", "term", "annual_inc", "home_ownership", "purpose", "dti",
        "delinq_2yrs", "fico_range_low", "fico_range_high", "inq_last_6mths", "open_acc", "pub_rec",
        "revol_bal", "revol_util", "total_acc", "application_type", "emp_length",
    }
    missing_columns = sorted(required - set(source.columns))
    if missing_columns:
        raise ValueError(f"Dataset is missing required columns: {', '.join(missing_columns)}")

    duplicate_ids = int(source["id"].duplicated().sum())
    source = source.drop_duplicates(subset=["id"]).copy()
    individual = source[source["application_type"] == "Individual"].copy()
    joint_or_other = source[source["application_type"] != "Individual"].copy()
    mature = individual[individual["loan_status"].isin(["Fully Paid", "Charged Off"])].copy()
    unresolved = individual[~individual["loan_status"].isin(["Fully Paid", "Charged Off"])].copy()
    if mature["loan_status"].nunique() != 2 or mature.shape[0] < 100:
        raise ValueError("At least 100 completed loans across both outcomes are required for model training.")

    labels = (mature["loan_status"] == "Charged Off").astype(int)
    features = _make_features(mature)
    X_train_valid, X_test, y_train_valid, y_test = train_test_split(
        features, labels, test_size=0.20, random_state=RANDOM_STATE, stratify=labels
    )
    X_train, X_validation, y_train, y_validation = train_test_split(
        X_train_valid, y_train_valid, test_size=0.20, random_state=RANDOM_STATE, stratify=y_train_valid
    )

    comparison: list[dict[str, Any]] = []
    validation_models: dict[str, Pipeline] = {}
    for name, estimator in _candidate_models().items():
        estimator.fit(X_train, y_train)
        values = _probabilities(estimator, X_validation)
        validation_models[name] = estimator
        comparison.append({"model": name, **_metrics(y_validation, values)})
    comparison.sort(key=lambda item: (item["pr_auc"], item["recall"], item["roc_auc"]), reverse=True)
    selected_name = comparison[0]["model"]

    base_estimator, search_space, search_iterations = _search_space(selected_name)
    search = RandomizedSearchCV(
        base_estimator,
        param_distributions=search_space,
        n_iter=search_iterations,
        scoring="average_precision",
        cv=3,
        random_state=RANDOM_STATE,
        n_jobs=1,
        refit=True,
        error_score="raise",
    )
    search.fit(X_train_valid, y_train_valid)
    best_model = search.best_estimator_
    test_probabilities = _probabilities(best_model, X_test)
    test_metrics = _metrics(y_test, test_probabilities)

    importance = permutation_importance(
        best_model,
        X_test,
        y_test,
        n_repeats=5,
        random_state=RANDOM_STATE,
        scoring="average_precision",
        n_jobs=1,
    )
    feature_importance = [
        {
            "feature": feature,
            "label": FEATURE_LABELS[feature],
            "importance_mean": round(float(mean), 5),
            "importance_std": round(float(std), 5),
        }
        for feature, mean, std in zip(FEATURES, importance.importances_mean, importance.importances_std)
    ]
    feature_importance.sort(key=lambda item: item["importance_mean"], reverse=True)

    baselines: dict[str, Any] = {}
    feature_ranges: dict[str, dict[str, float]] = {}
    for feature in NUMERIC_FEATURES:
        values = pd.to_numeric(X_train_valid[feature], errors="coerce")
        baselines[feature] = float(values.median())
        feature_ranges[feature] = {"minimum": float(values.min()), "maximum": float(values.max())}
    for feature in CATEGORICAL_FEATURES:
        modes = X_train_valid[feature].mode(dropna=True)
        baselines[feature] = str(modes.iloc[0]) if not modes.empty else "Unknown"

    dataset_hash = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    status_counts = mature["loan_status"].value_counts(dropna=False).to_dict()
    full_status_counts = source["loan_status"].value_counts(dropna=False).to_dict()
    train_time = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    version = "credit-risk-{}-{}".format(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"), uuid.uuid4().hex[:8])
    metadata: dict[str, Any] = {
        "model_version": version,
        "algorithm": selected_name,
        "selection_criterion": "Highest validation PR-AUC; ties are resolved by default recall, then ROC-AUC.",
        "tuning": {"method": "RandomizedSearchCV", "scoring": "average_precision", "best_parameters": search.best_params_, "best_cross_validation_pr_auc": round(float(search.best_score_), 4), "folds": 3},
        "trained_at": train_time,
        "software": {"python": platform.python_version(), "pandas": pd.__version__, "scikit_learn": sklearn.__version__, "xgboost": xgboost.__version__},
        "dataset": {
            "name": dataset_path.name,
            "sha256": dataset_hash,
            "rows_after_duplicate_id_removal": int(source.shape[0]),
            "duplicate_ids_removed": duplicate_ids,
            "mature_rows_used": int(mature.shape[0]),
            "unresolved_rows_excluded": int(unresolved.shape[0]),
            "joint_or_other_rows_excluded": int(joint_or_other.shape[0]),
            "target": "loan_status == 'Charged Off' (1); loan_status == 'Fully Paid' (0)",
            "target_counts": {str(key): int(value) for key, value in status_counts.items()},
            "source_status_counts": {str(key): int(value) for key, value in full_status_counts.items()},
            "default_rate_among_matured": round(float(labels.mean()), 4),
            "issue_months": sorted(source["issue_d"].dropna().astype(str).unique().tolist()) if "issue_d" in source else [],
        },
        "split": {"strategy": "Stratified random split (one issue month only)", "random_state": RANDOM_STATE, "train_rows_for_selection": int(X_train.shape[0]), "validation_rows": int(X_validation.shape[0]), "training_rows_for_final_model": int(X_train_valid.shape[0]), "held_out_test_rows": int(X_test.shape[0])},
        "features": FEATURES,
        "feature_labels": FEATURE_LABELS,
        "feature_importance": feature_importance,
        "validation_comparison": comparison,
        "test_metrics": test_metrics,
        "class_imbalance": {"method": "Class weights are tuned for the selected classifier; no resampling is used."},
        "explanation_method": "One-feature-at-a-time sensitivity against a training-profile median/mode. Values are non-additive associations, not causal explanations or SHAP values.",
    }
    artifact = {
        "model": best_model,
        "metadata": metadata,
        "baselines": baselines,
        "feature_ranges": feature_ranges,
        "features": FEATURES,
    }
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"{version}.joblib"
    joblib.dump(artifact, artifact_path, compress=3)
    metadata_path = artifact_dir / f"{version}.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return {"artifact_path": artifact_path, "metadata_path": metadata_path, "artifact": artifact, "metadata": metadata}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/raw/accepted_2007_to_2018Q4.csv"))
    parser.add_argument("--artifacts", type=Path, default=Path("instance/models"))
    args = parser.parse_args()
    result = train_model(args.data.resolve(), args.artifacts.resolve())
    print(json.dumps({"artifact": str(result["artifact_path"]), "model_version": result["metadata"]["model_version"], "algorithm": result["metadata"]["algorithm"], "test_metrics": result["metadata"]["test_metrics"]}, indent=2))


if __name__ == "__main__":
    main()
