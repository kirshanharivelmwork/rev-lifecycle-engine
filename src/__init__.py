"""SaaS Lifecycle & Churn Prediction Engine."""

__all__ = [
    "generate_datasets",
    "run_pipeline",
    "run_hypothesis_tests",
    "print_statistical_summary",
    "ChurnScoringEngine",
    "score_active_customers",
]


def __getattr__(name: str):
    if name == "generate_datasets":
        from src.generate_data import generate_datasets

        return generate_datasets
    if name == "run_pipeline":
        from src.data_pipeline import run_pipeline

        return run_pipeline
    if name in {"run_hypothesis_tests", "print_statistical_summary"}:
        from src import stats_engine

        return getattr(stats_engine, name)
    if name in {"ChurnScoringEngine", "score_active_customers"}:
        from src import churn_model

        return getattr(churn_model, name)
    raise AttributeError(f"module 'src' has no attribute {name!r}")
