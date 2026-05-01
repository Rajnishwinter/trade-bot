# AlgoTrader Pro

A professional algorithmic trading system with live web dashboard.

## Features
- **Strategy**: EMA (5/20/57/111) + CPR Breakout
- **Trailing Stop**: Dynamic 1:4 → 1:3 → trail mechanism
- **Markets**: Forex, Gold/Silver (MT5), Crypto (Binance/Bybit)
- **Dashboard**: Real-time HTML UI via WebSocket
- **Risk**: Max drawdown protection, session filters
- **Alerts**: Telegram notifications

---

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env with your broker credentials
nano .env
```

### 3. Run the dashboard server
```bash
python server.py
# Open http://localhost:8080
```

### 4. Run the trading bot
```bash
python bot/engine.py
```

---

## File Structure
```
trading_bot/
├── dashboard/
│   └── index.html          ← Live trading dashboard (open in browser)
├── bot/
│   └── engine.py           ← Core trading bot
├── server.py               ← HTTP + REST API server
├── requirements.txt
├── .env.example            ← Configuration template
├── deploy/
│   ├── systemd.service     ← Linux service file
│   └── docker-compose.yml  ← Docker deployment
└── README.md
```

---

## Strategy Logic

### Entry (BUY)
1. EMA cluster aligned bullishly: price > EMA5 > EMA20 > EMA57 > EMA111
2. Candle closes above CPR Top (TC)
3. Previous candle was below TC (breakout confirmation)

### Entry (SELL)
1. EMA cluster aligned bearishly: price < EMA5 < EMA20 < EMA57 < EMA111
2. Candle closes below CPR Bottom (BC)
3. Previous candle was above BC

### Stop Loss
- `min(50 pips, candle high/low * 1.001)`

### Trailing Stop
- Initial TP at 1:4 RR
- When 1:4 reached → SL moves to 1:3 lock
- Continues trailing dynamically until hit

---

## Broker Setup

### MetaTrader 5
1. Install MT5 platform + Python library (`pip install MetaTrader5`)
2. Enable AutoTrading in MT5
3. Set `BROKER=MT5` + credentials in `.env`

### Binance/Bybit (Crypto)
1. Create API keys with trading permissions
2. Set `BROKER=Binance` (or `Bybit`) + keys in `.env`

---

## Deployment (VPS/Server)

### Linux Systemd Service
```bash
sudo cp deploy/systemd.service /etc/systemd/system/algotrader.service
sudo systemctl enable algotrader
sudo systemctl start algotrader
```

### Docker
```bash
docker-compose -f deploy/docker-compose.yml up -d
```

### Recommended VPS
- **DigitalOcean**: $6/mo Droplet (Ubuntu 22.04)
- **AWS**: t3.small EC2 instance
- Keep the VPS in the same region as your broker server

---

## Risk Warning
Trading financial instruments involves substantial risk of loss.
This software is for educational purposes. Always test on a demo account first.

---

## Optional Enhancements
- `TELEGRAM_TOKEN` + `TELEGRAM_CHAT_ID` in `.env` → instant trade alerts
- Export button in dashboard → downloads `trades_export.csv`
- Backtest button → connects to `/api/backtest` endpoint
