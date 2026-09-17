"""SaaS Lifecycle & Churn Prediction Engine package entrypoint."""

from src.generate_data import generate_datasets
from src.data_pipeline import run_pipeline
from src.stats_engine import print_statistical_summary
from src.churn_model import ChurnScoringEngine


def main() -> None:
    generate_datasets()
    df = run_pipeline()
    print_statistical_summary()
    engine = ChurnScoringEngine()
    engine.fit(df, tune=True)
    alerts = engine.score_active_customers(df, threshold=0.65)
    print(f"Alert table rows: {len(alerts):,}")


if __name__ == "__main__":
    main()
