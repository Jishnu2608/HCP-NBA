"""Propensity models: probability that an outreach is engaged with, or leads to a fill.

Champion is a logistic regression, chosen because each prediction decomposes exactly into
per-feature contributions that the rationale can quote. A gradient-boosting challenger is
trained alongside and reported, so reviewers can see what the simple model gives up.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import DATA_DIR
from app.models import ModelVersion

MODEL_DIR = DATA_DIR / "models"
HOLDOUT_DAYS = 60


@dataclass
class Spec:
    name: str
    label: str
    numeric: list[str]
    categorical: list[str]
    # Feature whose value alone is the "no model" baseline the champion must beat.
    baseline_feature: str


def _build(spec: Spec, classifier) -> Pipeline:
    pre = ColumnTransformer(
        [
            ("num", StandardScaler(), spec.numeric),
            (
                "cat",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                spec.categorical,
            ),
        ]
    )
    return Pipeline([("pre", pre), ("clf", classifier)])


def _auc(y, p) -> float | None:
    return round(float(roc_auc_score(y, p)), 4) if len(set(y)) > 1 else None


def train(spec: Spec, rows: list[dict], as_of: date) -> tuple[Pipeline, dict]:
    """Fits on history before the holdout window, scores the holdout, then refits on everything."""
    df = pd.DataFrame(rows)
    cols = spec.numeric + spec.categorical
    cutoff = datetime.combine(as_of - timedelta(days=HOLDOUT_DAYS), time.min)
    train_df, test_df = df[df.ts < cutoff], df[df.ts >= cutoff]
    y_train, y_test = train_df[spec.label], test_df[spec.label]

    champion = _build(spec, LogisticRegression(max_iter=2000)).fit(train_df[cols], y_train)
    p_champion = champion.predict_proba(test_df[cols])[:, 1]
    challenger = _build(
        spec, HistGradientBoostingClassifier(max_depth=3, max_iter=150, random_state=0)
    ).fit(train_df[cols], y_train)
    p_challenger = challenger.predict_proba(test_df[cols])[:, 1]

    metrics = {
        "label": spec.label,
        "n_train": int(len(train_df)),
        "n_test": int(len(test_df)),
        "positive_rate": round(float(df[spec.label].mean()), 4),
        "holdout_days": HOLDOUT_DAYS,
        "auc": _auc(y_test, p_champion),
        "auc_baseline": _auc(y_test, test_df[spec.baseline_feature]),
        "auc_challenger_gbm": _auc(y_test, p_challenger),
        "brier": round(float(brier_score_loss(y_test, p_champion)), 4),
        "brier_base_rate": round(
            float(brier_score_loss(y_test, np.full(len(y_test), y_train.mean()))), 4
        ),
    }
    final = _build(spec, LogisticRegression(max_iter=2000)).fit(df[cols], df[spec.label])
    return final, metrics


def predict(model: Pipeline, spec: Spec, rows: list[dict]) -> np.ndarray:
    if not rows:
        return np.array([])
    return model.predict_proba(pd.DataFrame(rows)[spec.numeric + spec.categorical])[:, 1]


def contributions(model: Pipeline, spec: Spec, row: dict, top: int = 5) -> list[dict]:
    """Exact per-feature push on the log-odds for one prediction, largest first.

    Numeric features are measured against the population average, so a positive number
    means "higher than typical and that raises the probability".
    """
    cols = spec.numeric + spec.categorical
    x = model.named_steps["pre"].transform(pd.DataFrame([row])[cols])[0]
    coef = model.named_steps["clf"].coef_[0]
    names = model.named_steps["pre"].get_feature_names_out()
    out = []
    for name, value, weight in zip(names, x, coef, strict=True):
        kind, _, feature = name.partition("__")
        if kind == "cat":
            if value == 0:
                continue
            feature, _, category = feature.partition("_")
            # One-hot names are "<column>_<category>"; columns here have no underscore clash
            # except plan_type, handled by matching against the declared categorical columns.
            for col in spec.categorical:
                if name == f"cat__{col}_{row[col]}":
                    feature, category = col, row[col]
            out.append({"feature": feature, "value": category, "contribution": float(weight)})
        else:
            out.append(
                {"feature": feature, "value": row[feature], "contribution": float(weight * value)}
            )
    out.sort(key=lambda c: abs(c["contribution"]), reverse=True)
    return [{**c, "contribution": round(c["contribution"], 3)} for c in out[:top]]


# --- Registry ----------------------------------------------------------------


def register(
    db: Session, spec: Spec, model: Pipeline, metrics: dict, as_of: date, model_dir: Path
) -> ModelVersion:
    model_dir.mkdir(parents=True, exist_ok=True)
    version = (
        db.scalar(select(func.max(ModelVersion.version)).where(ModelVersion.name == spec.name)) or 0
    ) + 1
    path = model_dir / f"{spec.name}_v{version}.joblib"
    joblib.dump(model, path)
    db.execute(update(ModelVersion).where(ModelVersion.name == spec.name).values(is_active=False))
    row = ModelVersion(
        name=spec.name,
        version=version,
        algorithm="logistic_regression",
        as_of_date=as_of,
        metrics=metrics,
        features=spec.numeric + spec.categorical,
        artifact_path=str(path),
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


@lru_cache(maxsize=16)
def _load(path: str) -> Pipeline:
    return joblib.load(path)


def load_active(db: Session, name: str) -> Pipeline | None:
    row = db.scalar(select(ModelVersion).where(ModelVersion.name == name, ModelVersion.is_active))
    return _load(row.artifact_path) if row else None
