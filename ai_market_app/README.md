# Personal AI Stock Signal Dashboard

A private professional market signal dashboard for VEDL and SUZLON with advanced UI.

## What's included

- **Web Dashboard** (Flask) – Professional TradingView-style interface
- **Streamlit Dashboard** (Alternative) – Classic Python-based UI
- Technical indicators: EMA, RSI, VWAP, MACD, ATR
- Option chain scoring with PCR, max pain, put OI
- News headline sentiment analysis
- AI signal with confidence, risk, entry/targets, invalidation levels
- SQLite signal storage

## Quick Start

### Option 1: Professional Web Dashboard (Recommended)

```bash
# Install dependencies
pip install -r requirements.txt

# Run Flask server
python web_app.py
```

Open browser to: `http://localhost:5000`

Features:
- Sticky header with live data status
- Left sidebar for symbol/timeframe selection
- Premium dark theme with glassmorphic design
- Real-time signal updates
- Optional 15-second auto-refresh
- Score breakdown with visual progress bars
- Related-stock option-chain comparison
- News sentiment parsing
- Responsive layout

### Option 2: Streamlit Dashboard

```bash
streamlit run app.py
```

Open browser to: `http://localhost:8501`

## Configuration

1. Copy `.env.example` to `.env`
2. Add your broker API keys (optional):
   - `DHAN_API_KEY` / `DHAN_API_SECRET` – Dhan broker data
   - `UPSTOX_API_KEY` / `UPSTOX_API_SECRET` – Upstox API
3. Leave `USE_SAMPLE_DATA=auto` for live Yahoo Finance NSE quotes/candles with sample fallback, or set `USE_SAMPLE_DATA=true` to force sample data

## Notes

- **Market Data**: App tries Yahoo Finance NSE symbols such as `VEDL.NS` and `SUZLON.NS`, then falls back to synthetic sample data
- **Sample Data Mode**: Set `USE_SAMPLE_DATA=true` to force synthetic quotes and candles
- **Real Broker API**: Replace market data connectors in `data/market_data.py` and `data/option_chain.py` for broker-grade quotes/options
- **Option Chains**: Current option-chain data is modeled locally unless a broker connector is added
- **Personal Use Only**: Trade responsibly; signals are heuristic-based, not financial advice
- **NSE Data**: Dhan or Upstox should still be wired in for broker-grade NSE option chain data
