import pandas as pd
import joblib

from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report
from xgboost import XGBClassifier


def train_from_csv(path: str = "training_data.csv") -> None:
    df = pd.read_csv(path)
    features = [
        "rsi",
        "ema_9_above_20",
        "close_above_vwap",
        "macd_positive",
        "atr_percent",
        "option_pcr",
        "max_pain_distance",
        "news_score",
        "market_score"
    ]
    target = "target_next_day_up"
    X = df[features]
    y = df[target]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, shuffle=False
    )

    model = XGBClassifier(
        n_estimators=100,
        max_depth=3,
        learning_rate=0.05,
        eval_metric="logloss"
    )
    model.fit(X_train, y_train)

    preds = model.predict(X_test)
    print("Accuracy:", accuracy_score(y_test, preds))
    print(classification_report(y_test, preds))
    joblib.dump(model, "ml/models/xgb_direction_model.pkl")


if __name__ == "__main__":
    train_from_csv()
