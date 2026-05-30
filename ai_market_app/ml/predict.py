import joblib
import pandas as pd


def predict_direction(features: dict) -> dict:
    model = joblib.load("ml/models/xgb_direction_model.pkl")
    X = pd.DataFrame([features])
    probability_up = float(model.predict_proba(X)[0][1])
    return {
        "probability_up": round(probability_up * 100, 2),
        "ml_signal": "BULLISH" if probability_up >= 0.6 else "BEARISH"
    }
