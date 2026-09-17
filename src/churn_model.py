"""Predictive churn modeling and early-warning scoring engine."""

from __future__ import annotations

import logging
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
from sklearn.base import ClassifierMixin
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    classification_report,
    confusion_matrix,
    precision_recall_curve,
    roc_auc_score,
    roc_curve,
)
from sklearn.model_selection import GridSearchCV, StratifiedKFold, train_test_split
from sklearn.preprocessing import StandardScaler
from statsmodels.stats.outliers_influence import variance_inflation_factor
from statsmodels.tools.tools import add_constant

from src.data_pipeline import run_pipeline
from src.paths import CHURN_ALERTS_PATH, FULL_FUNNEL_FEATURES_PATH, PROCESSED_DIR

LOGGER = logging.getLogger(__name__)

TARGET = "churned"
ID_COL = "customer_id"
RAW_CATEGORICALS = ("acquisition_channel", "contract_type")
DEFAULT_THRESHOLD = 0.65
RANDOM_STATE = 42
VIF_THRESHOLD = 10.0

NUMERIC_CANDIDATES = [
    "sales_touchpoints",
    "cac_usd",
    "monthly_recurring_revenue",
    "avg_weekly_logins",
    "feature_adoption_score",
    "support_tickets_raised",
    "days_since_last_login",
    "ltv_est",
    "ltv_cac_ratio",
    "high_risk_inactivity",
    "engagement_index",
]
# Deterministic transforms of other columns — excluded from the GLM to keep VIF finite.
LOGIT_EXCLUDE = {
    "ltv_est",
    "ltv_cac_ratio",
    "engagement_index",
    "high_risk_inactivity",
}


@dataclass
class ModelReport:
    name: str
    accuracy: float
    precision: float
    recall: float
    f1: float
    roc_auc: float
    pr_auc: float
    brier: float
    confusion: np.ndarray
    classification_report: str
    extras: dict[str, Any] = field(default_factory=dict)


class ChurnScoringEngine:
    """End-to-end logistic baseline + gradient-boosted production scorer."""

    def __init__(self, random_state: int = RANDOM_STATE) -> None:
        self.random_state = random_state
        self.feature_columns_: list[str] = []
        self.logit_features_: list[str] = []
        self.scaler_: StandardScaler | None = None
        self.logit_: LogisticRegression | None = None
        self.production_model_: ClassifierMixin | None = None
        self.production_name_: str = "xgboost"
        self.reports_: dict[str, ModelReport] = {}
        self.feature_importances_: pd.DataFrame | None = None
        self.odds_ratios_: pd.DataFrame | None = None
        self.vif_: pd.DataFrame | None = None
        self.grid_best_params_: dict[str, Any] | None = None
        self.X_test_: pd.DataFrame | None = None
        self.y_test_: pd.Series | None = None
        self.fitted_: bool = False

    def _dummy_columns(self, df: pd.DataFrame) -> list[str]:
        return [
            c
            for c in df.columns
            if c.startswith("acquisition_channel_") or c.startswith("contract_type_")
        ]

    def _model_frame(self, df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series | None]:
        dummy_cols = self._dummy_columns(df)
        cols = [c for c in NUMERIC_CANDIDATES if c in df.columns] + dummy_cols
        missing = [c for c in cols if c not in df.columns]
        if missing:
            raise ValueError(f"Scoring frame missing columns: {missing}")
        X = df.loc[:, cols].apply(pd.to_numeric, errors="coerce").astype(float)
        if X.isna().any().any():
            raise ValueError("Scoring frame contains nulls after numeric coercion")
        y = df[TARGET].astype(int) if TARGET in df.columns else None
        return X, y

    def compute_vif(self, X: pd.DataFrame) -> pd.DataFrame:
        """Variance Inflation Factor for the logistic design matrix."""
        design = add_constant(X, has_constant="add")
        records = []
        for i, col in enumerate(design.columns):
            if col == "const":
                continue
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                vif = float(variance_inflation_factor(design.values, i))
            records.append({"feature": col, "vif": vif})
        return pd.DataFrame(records).sort_values("vif", ascending=False).reset_index(drop=True)

    def _drop_high_vif(self, X: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Iteratively drop the highest-VIF feature until all VIF <= 10 (keep >= 2 feats)."""
        current = X.copy()
        history = self.compute_vif(current)
        while len(current.columns) > 2 and history["vif"].max() > VIF_THRESHOLD:
            drop_col = history.iloc[0]["feature"]
            LOGGER.info("Dropping %s from logistic model (VIF=%.1f)", drop_col, history.iloc[0]["vif"])
            current = current.drop(columns=[drop_col])
            history = self.compute_vif(current)
        return current, history

    def _fit_logistic(
        self, X_train: pd.DataFrame, y_train: pd.Series, X_test: pd.DataFrame, y_test: pd.Series
    ) -> ModelReport:
        logit_frame = X_train.drop(columns=[c for c in LOGIT_EXCLUDE if c in X_train.columns])
        reduced, vif = self._drop_high_vif(logit_frame)
        self.logit_features_ = list(reduced.columns)
        self.vif_ = vif

        scaler = StandardScaler()
        X_train_s = scaler.fit_transform(reduced)
        X_test_s = scaler.transform(X_test[self.logit_features_])
        self.scaler_ = scaler

        logit = LogisticRegression(
            max_iter=1000,
            class_weight="balanced",
            solver="lbfgs",
            random_state=self.random_state,
        )
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=RuntimeWarning)
            logit.fit(X_train_s, y_train)
        self.logit_ = logit
        return self._evaluate("logistic_regression", logit, X_test_s, y_test)

    def _wald_odds_ratios(self, X_scaled: np.ndarray, y: np.ndarray) -> pd.DataFrame:
        assert self.logit_ is not None
        beta = np.concatenate([self.logit_.intercept_, self.logit_.coef_.ravel()])
        X_design = np.column_stack([np.ones(len(X_scaled)), X_scaled])
        p = self.logit_.predict_proba(X_scaled)[:, 1]
        w = np.clip(p * (1.0 - p), 1e-12, None)
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            xtwx = (X_design.T * w) @ X_design
            cov = np.linalg.pinv(xtwx)
            se = np.sqrt(np.clip(np.diag(cov), 0, None))
        feature_beta = beta[1:]
        feature_se = se[1:]
        return pd.DataFrame(
            {
                "feature": self.logit_features_,
                "coefficient": feature_beta,
                "odds_ratio": np.exp(feature_beta),
                "ci_low": np.exp(feature_beta - 1.96 * feature_se),
                "ci_high": np.exp(feature_beta + 1.96 * feature_se),
                "std_error": feature_se,
            }
        ).sort_values("odds_ratio", ascending=False)

    def _make_xgb(self, **params: Any) -> ClassifierMixin:
        if self._xgboost_usable():
            from xgboost import XGBClassifier

            defaults = dict(
                n_estimators=120,
                max_depth=4,
                learning_rate=0.08,
                subsample=0.85,
                colsample_bytree=0.85,
                objective="binary:logistic",
                eval_metric="logloss",
                random_state=self.random_state,
                n_jobs=1,
            )
            defaults.update(params)
            self.production_name_ = "xgboost"
            return XGBClassifier(**defaults)

        LOGGER.warning("XGBoost native library unavailable; falling back to RandomForestClassifier")
        from sklearn.ensemble import RandomForestClassifier

        rf_params = {
            k: v
            for k, v in params.items()
            if k in {"n_estimators", "max_depth", "min_samples_leaf", "max_features"}
        }
        rf_params.setdefault("n_estimators", 200)
        rf_params.setdefault("max_depth", 8)
        rf_params.setdefault("random_state", self.random_state)
        rf_params.setdefault("class_weight", "balanced")
        rf_params.setdefault("n_jobs", 1)
        self.production_name_ = "random_forest"
        return RandomForestClassifier(**rf_params)

    @staticmethod
    def _xgboost_usable() -> bool:
        try:
            from xgboost import XGBClassifier

            XGBClassifier(n_estimators=1, max_depth=1, n_jobs=1)
            return True
        except Exception:
            return False

    def _param_grid(self) -> dict[str, list[Any]]:
        if self.production_name_ == "random_forest":
            return {
                "n_estimators": [120, 200],
                "max_depth": [6, 10],
                "min_samples_leaf": [2, 5],
            }
        return {
            "n_estimators": [80, 160],
            "max_depth": [3, 5],
            "learning_rate": [0.05, 0.1],
            "subsample": [0.85],
            "colsample_bytree": [0.85],
        }

    def _fit_production(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_test: pd.DataFrame,
        y_test: pd.Series,
        tune: bool,
    ) -> ModelReport:
        pos = float(y_train.mean())
        scale_pos_weight = (1.0 - pos) / max(pos, 1e-6)
        estimator = self._make_xgb(scale_pos_weight=scale_pos_weight)

        if tune:
            grid = GridSearchCV(
                estimator,
                param_grid=self._param_grid(),
                scoring={"roc_auc": "roc_auc", "pr_auc": "average_precision"},
                refit="pr_auc",
                cv=StratifiedKFold(n_splits=3, shuffle=True, random_state=self.random_state),
                n_jobs=1,
                verbose=0,
            )
            grid.fit(X_train, y_train)
            model = grid.best_estimator_
            self.grid_best_params_ = dict(grid.best_params_)
            LOGGER.info("Best production params (refit PR-AUC): %s", self.grid_best_params_)
        else:
            model = estimator
            model.fit(X_train, y_train)
            self.grid_best_params_ = {}

        self.production_model_ = model
        importances = getattr(model, "feature_importances_", None)
        if importances is not None:
            self.feature_importances_ = (
                pd.DataFrame({"feature": X_train.columns, "importance": importances})
                .sort_values("importance", ascending=False)
                .reset_index(drop=True)
            )
        return self._evaluate(self.production_name_, model, X_test, y_test)

    def _evaluate(
        self,
        name: str,
        model: ClassifierMixin,
        X_test: pd.DataFrame | np.ndarray,
        y_test: pd.Series,
    ) -> ModelReport:
        proba = model.predict_proba(X_test)[:, 1]
        pred = (proba >= 0.5).astype(int)
        report_txt = classification_report(y_test, pred, digits=4, zero_division=0)
        report_dict = classification_report(y_test, pred, output_dict=True, zero_division=0)
        cm = confusion_matrix(y_test, pred)
        roc = roc_auc_score(y_test, proba)
        pr = average_precision_score(y_test, proba)
        brier = brier_score_loss(y_test, proba)
        pos = report_dict.get("1", report_dict.get("1.0", {}))
        model_report = ModelReport(
            name=name,
            accuracy=float(report_dict["accuracy"]),
            precision=float(pos.get("precision", 0.0)),
            recall=float(pos.get("recall", 0.0)),
            f1=float(pos.get("f1-score", 0.0)),
            roc_auc=float(roc),
            pr_auc=float(pr),
            brier=float(brier),
            confusion=cm,
            classification_report=report_txt,
            extras={
                "roc_curve": roc_curve(y_test, proba),
                "pr_curve": precision_recall_curve(y_test, proba),
                "y_proba": proba,
            },
        )
        self.reports_[name] = model_report
        return model_report

    def fit(self, df: pd.DataFrame, tune: bool = True) -> "ChurnScoringEngine":
        X, y = self._model_frame(df)
        if y is None:
            raise ValueError("Training data must include a 'churned' column")
        self.feature_columns_ = list(X.columns)
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.20, stratify=y, random_state=self.random_state
        )
        self.X_test_ = X_test
        self.y_test_ = y_test

        LOGGER.info("Training logistic baseline on %s rows / %s features", len(X_train), X_train.shape[1])
        logit_report = self._fit_logistic(X_train, y_train, X_test, y_test)
        X_train_s = self.scaler_.transform(X_train[self.logit_features_])
        self.odds_ratios_ = self._wald_odds_ratios(X_train_s, y_train.to_numpy())

        LOGGER.info("Training production model (%s)", self.production_name_)
        prod_report = self._fit_production(X_train, y_train, X_test, y_test, tune=tune)
        self.fitted_ = True
        self._print_reports([logit_report, prod_report])
        return self

    def _print_reports(self, reports: Iterable[ModelReport]) -> None:
        print("\n" + "=" * 78)
        print("CHURN MODEL EVALUATION")
        print("=" * 78)
        if self.vif_ is not None:
            print("\nVariance Inflation Factors (logistic design after pruning):")
            print(self.vif_.to_string(index=False))
        if self.odds_ratios_ is not None:
            print("\nLogistic Odds Ratios (per 1 SD, 95% Wald CI):")
            print(self.odds_ratios_.round(4).to_string(index=False))
        for report in reports:
            print(f"\n--- {report.name} ---")
            print(report.classification_report)
            print("Confusion matrix [[TN FP] [FN TP]]:")
            print(report.confusion)
            print(
                f"ROC-AUC={report.roc_auc:.4f}  PR-AUC={report.pr_auc:.4f}  "
                f"Brier={report.brier:.4f}"
            )
        if self.feature_importances_ is not None:
            print("\nProduction feature importance (top 10):")
            print(self.feature_importances_.head(10).to_string(index=False))
        print("=" * 78 + "\n")

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        if not self.fitted_ or self.production_model_ is None:
            raise RuntimeError("Engine is not fitted. Call fit() first.")
        X, _ = self._model_frame(df)
        X = X.reindex(columns=self.feature_columns_, fill_value=0.0)
        return self.production_model_.predict_proba(X)[:, 1]

    def score_active_customers(
        self,
        df: pd.DataFrame,
        threshold: float = DEFAULT_THRESHOLD,
        output_path: Any = None,
        persist: bool = True,
    ) -> pd.DataFrame:
        """Flag currently active customers whose predicted churn risk >= threshold."""
        if not self.fitted_:
            raise RuntimeError("Engine is not fitted. Call fit() first.")
        active = df.loc[df[TARGET] == 0].copy() if TARGET in df.columns else df.copy()
        active["churn_risk_score"] = self.predict_proba(active)
        alerts = active.loc[active["churn_risk_score"] >= threshold].copy()
        alerts.sort_values("churn_risk_score", ascending=False, inplace=True)
        alerts["risk_tier"] = pd.cut(
            alerts["churn_risk_score"],
            bins=[threshold, 0.80, 0.90, 1.01],
            labels=["Watch", "High", "Critical"],
            include_lowest=True,
            right=False,
        )
        alerts["recommended_intervention"] = alerts.apply(_recommend_intervention, axis=1)
        keep = [
            ID_COL,
            "churn_risk_score",
            "risk_tier",
            "recommended_intervention",
            "days_since_last_login",
            "engagement_index",
            "feature_adoption_score",
            "support_tickets_raised",
            "ltv_est",
            "ltv_cac_ratio",
            "cac_usd",
            "monthly_recurring_revenue",
        ]
        if "acquisition_channel" in alerts.columns:
            keep.insert(1, "acquisition_channel")
        if "contract_type" in alerts.columns:
            keep.insert(2, "contract_type")
        table = alerts.loc[:, [c for c in keep if c in alerts.columns]].reset_index(drop=True)

        if persist:
            PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
            dest = output_path or CHURN_ALERTS_PATH
            table.to_csv(dest, index=False)
            LOGGER.info("Wrote %s churn-risk alerts -> %s", len(table), dest)
        return table


def _recommend_intervention(row: pd.Series) -> str:
    days = float(row.get("days_since_last_login", 0) or 0)
    engagement = float(row.get("engagement_index", 0) or 0)
    tickets = float(row.get("support_tickets_raised", 0) or 0)
    adoption = float(row.get("feature_adoption_score", 0) or 0)
    ltv_cac = float(row.get("ltv_cac_ratio", 0) or 0)
    contract = str(row.get("contract_type", ""))

    if days > 21:
        return "CSM re-engagement sprint within 48h + executive sponsor ping"
    if adoption < 4.0:
        return "Guided onboarding reboot: core-feature activation workshop"
    if tickets >= 4:
        return "Technical account review; escalate open tickets to solutions eng"
    if engagement < 4.5:
        return "In-product adoption campaign + weekly success checklist"
    if contract == "Monthly" and ltv_cac >= 3:
        return "Annual conversion incentive (discount locked to 12-month term)"
    if ltv_cac < 3:
        return "Value review: usage vs. contracted seats; avoid over-discounting"
    return "Health-score watchlist; nurture with case studies and QBR"


def _load_features() -> pd.DataFrame:
    if FULL_FUNNEL_FEATURES_PATH.exists():
        return pd.read_csv(FULL_FUNNEL_FEATURES_PATH)
    return run_pipeline()


def score_active_customers(
    df: pd.DataFrame | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    engine: ChurnScoringEngine | None = None,
    tune: bool = True,
) -> pd.DataFrame:
    """Module-level inference helper used by CLI, notebook, and tests."""
    data = df if df is not None else _load_features()
    if engine is None:
        engine = ChurnScoringEngine()
        engine.fit(data, tune=tune)
    return engine.score_active_customers(data, threshold=threshold)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    df = _load_features()
    engine = ChurnScoringEngine()
    engine.fit(df, tune=True)
    alerts = engine.score_active_customers(df, threshold=DEFAULT_THRESHOLD)
    print(f"Active customers above {DEFAULT_THRESHOLD:.0%} churn risk: {len(alerts):,}")
    if not alerts.empty:
        print(alerts.head(8).to_string(index=False))


if __name__ == "__main__":
    main()
