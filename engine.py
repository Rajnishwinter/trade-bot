"""
AlgoTrader Pro — Trading Bot Engine
Supports: MetaTrader 5 (Forex/Commodities), Binance/CCXT (Crypto)
Strategy: EMA (5/20/57/111) + CPR Breakout with Trailing Stop
"""

import asyncio
import json
import logging
import math
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional
import os

# pip install MetaTrader5 ccxt websockets pandas numpy python-dotenv
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

try:
    import ccxt.async_support as ccxt
    CCXT_AVAILABLE = True
except ImportError:
    CCXT_AVAILABLE = False

try:
    import pandas as pd
    import numpy as np
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

try:
    import websockets
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('trading_bot.log'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('AlgoTrader')


# ══════════════════════════════════════════════
# DATA STRUCTURES
# ══════════════════════════════════════════════

@dataclass
class BotConfig:
    symbol: str = 'XAUUSD'
    timeframe: int = 5           # minutes
    lot_size: float = 0.01
    max_sl_pips: int = 50
    rr_ratio: float = 4.0        # 1:4 risk reward
    max_drawdown_pct: float = 10.0
    london_session: bool = True
    ny_session: bool = True
    trailing_enabled: bool = True
    telegram_token: str = ''
    telegram_chat_id: str = ''
    broker: str = 'MT5'          # MT5 | Binance | Bybit
    api_key: str = ''
    api_secret: str = ''
    mt5_server: str = ''
    mt5_account: int = 0
    mt5_password: str = ''


@dataclass
class Candle:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0


@dataclass
class EMAs:
    ema5: float = 0
    ema20: float = 0
    ema57: float = 0
    ema111: float = 0


@dataclass
class CPR:
    pivot: float = 0
    bc: float = 0      # Bottom Central
    tc: float = 0      # Top Central
    width: float = 0


@dataclass
class Signal:
    symbol: str
    side: str          # BUY | SELL
    entry: float
    sl: float
    tp: float
    timeframe: int
    timestamp: datetime = field(default_factory=datetime.utcnow)
    reason: str = ''


@dataclass
class Position:
    ticket: int
    symbol: str
    side: str
    entry: float
    sl: float
    tp: float
    lots: float
    open_time: datetime = field(default_factory=datetime.utcnow)
    trailing_sl: Optional[float] = None
    trailing_activated: bool = False
    pnl: float = 0.0


@dataclass
class TradeRecord:
    ticket: int
    symbol: str
    side: str
    entry: float
    exit: float
    lots: float
    sl: float
    tp: float
    pnl: float
    open_time: datetime
    close_time: datetime
    exit_reason: str = ''


@dataclass
class DashboardMetrics:
    total_pnl: float = 0
    capital: float = 50000
    principal: float = 50000
    max_drawdown: float = 0
    max_profit: float = 0
    overall_accuracy: float = 0
    daily_accuracy: float = 0
    weekly_accuracy: float = 0
    monthly_accuracy: float = 0
    max_accuracy: float = 0
    open_positions: list = field(default_factory=list)
    recent_signals: list = field(default_factory=list)
    trade_history: list = field(default_factory=list)
    bot_running: bool = True
    timestamp: str = ''


# ══════════════════════════════════════════════
# INDICATOR ENGINE
# ══════════════════════════════════════════════

class IndicatorEngine:
    """Computes EMA, CPR and detects breakout conditions."""

    @staticmethod
    def ema(prices: list, period: int) -> list:
        """Exponential Moving Average."""
        if len(prices) < period:
            return [None] * len(prices)
        k = 2 / (period + 1)
        result = [None] * (period - 1)
        result.append(sum(prices[:period]) / period)
        for p in prices[period:]:
            result.append(p * k + result[-1] * (1 - k))
        return result

    @staticmethod
    def compute_emas(candles: list[Candle]) -> EMAs:
        closes = [c.close for c in candles]
        e5  = IndicatorEngine.ema(closes, 5)
        e20 = IndicatorEngine.ema(closes, 20)
        e57 = IndicatorEngine.ema(closes, 57)
        e111= IndicatorEngine.ema(closes, 111)
        return EMAs(
            ema5   = e5[-1]   if e5[-1]   is not None else 0,
            ema20  = e20[-1]  if e20[-1]  is not None else 0,
            ema57  = e57[-1]  if e57[-1]  is not None else 0,
            ema111 = e111[-1] if e111[-1] is not None else 0,
        )

    @staticmethod
    def compute_cpr(prev_candle: Candle) -> CPR:
        """Central Pivot Range from previous candle's HLC."""
        pivot = (prev_candle.high + prev_candle.low + prev_candle.close) / 3
        bc    = (prev_candle.high + prev_candle.low) / 2
        tc    = (pivot - bc) + pivot
        if tc < bc:
            bc, tc = tc, bc
        return CPR(pivot=pivot, bc=bc, tc=tc, width=tc - bc)

    @staticmethod
    def ema_cluster_bullish(emas: EMAs, price: float) -> bool:
        """Price above all EMAs and EMAs ordered (5>20>57>111)."""
        return (price > emas.ema5 > emas.ema20 > emas.ema57 > emas.ema111
                and emas.ema5 > 0 and emas.ema111 > 0)

    @staticmethod
    def ema_cluster_bearish(emas: EMAs, price: float) -> bool:
        """Price below all EMAs and EMAs ordered (5<20<57<111)."""
        return (price < emas.ema5 < emas.ema20 < emas.ema57 < emas.ema111
                and emas.ema5 > 0 and emas.ema111 > 0)

    @staticmethod
    def cpr_breakout_buy(cpr: CPR, candle: Candle) -> bool:
        """Candle closes above CPR top."""
        return candle.close > cpr.tc and candle.low < cpr.tc

    @staticmethod
    def cpr_breakout_sell(cpr: CPR, candle: Candle) -> bool:
        """Candle closes below CPR bottom."""
        return candle.close < cpr.bc and candle.high > cpr.bc


# ══════════════════════════════════════════════
# STRATEGY ENGINE
# ══════════════════════════════════════════════

class StrategyEngine:
    def __init__(self, config: BotConfig):
        self.config = config
        self.ind = IndicatorEngine()

    def pip_size(self, symbol: str) -> float:
        """Returns pip size for the symbol."""
        symbol_upper = symbol.upper()
        if 'JPY' in symbol_upper: return 0.01
        if 'XAU' in symbol_upper or 'GOLD' in symbol_upper: return 0.10
        if 'BTC' in symbol_upper: return 1.0
        if 'ETH' in symbol_upper: return 0.10
        return 0.0001

    def compute_sl_tp(self, side: str, entry: float, candle: Candle) -> tuple[float, float]:
        """Compute SL/TP. SL = min(50 pips, candle low/high), TP = SL * RR."""
        pip = self.pip_size(self.config.symbol)
        max_sl_distance = self.config.max_sl_pips * pip

        if side == 'BUY':
            candle_sl = entry - (entry - candle.low) * 1.001
            sl_distance = min(max_sl_distance, entry - candle_sl)
            sl = entry - sl_distance
            tp = entry + sl_distance * self.config.rr_ratio
        else:  # SELL
            candle_sl = entry + (candle.high - entry) * 1.001
            sl_distance = min(max_sl_distance, candle_sl - entry)
            sl = entry + sl_distance
            tp = entry - sl_distance * self.config.rr_ratio

        return round(sl, 5), round(tp, 5)

    def generate_signal(self, candles: list[Candle]) -> Optional[Signal]:
        """Main signal generation logic. Requires min 120 candles."""
        if len(candles) < 120:
            return None

        current  = candles[-1]
        prev     = candles[-2]
        emas     = self.ind.compute_emas(candles)
        cpr      = self.ind.compute_cpr(candles[-2])  # Use prev candle for CPR

        # ── BUY Signal ────────────────────────────────────────────
        buy_ema   = self.ind.ema_cluster_bullish(emas, current.close)
        buy_cpr   = self.ind.cpr_breakout_buy(cpr, current)
        prev_below= prev.close < cpr.tc  # Previously below CPR (breakout confirmation)

        if buy_ema and buy_cpr and prev_below:
            sl, tp = self.compute_sl_tp('BUY', current.close, current)
            return Signal(
                symbol=self.config.symbol,
                side='BUY',
                entry=current.close,
                sl=sl, tp=tp,
                timeframe=self.config.timeframe,
                reason=f'EMA bullish cluster + CPR breakout above {cpr.tc:.5f}'
            )

        # ── SELL Signal ───────────────────────────────────────────
        sell_ema  = self.ind.ema_cluster_bearish(emas, current.close)
        sell_cpr  = self.ind.cpr_breakout_sell(cpr, current)
        prev_above= prev.close > cpr.bc

        if sell_ema and sell_cpr and prev_above:
            sl, tp = self.compute_sl_tp('SELL', current.close, current)
            return Signal(
                symbol=self.config.symbol,
                side='SELL',
                entry=current.close,
                sl=sl, tp=tp,
                timeframe=self.config.timeframe,
                reason=f'EMA bearish cluster + CPR breakdown below {cpr.bc:.5f}'
            )

        return None

    def update_trailing_stop(self, pos: Position, current_price: float) -> Optional[float]:
        """
        Trailing logic:
        - When price reaches 1:4 TP, move SL to 1:3
        - Continue trailing dynamically thereafter
        Returns new SL if should be updated, else None.
        """
        if not self.config.trailing_enabled:
            return None

        pip = self.pip_size(pos.symbol)
        if pos.side == 'BUY':
            risk      = pos.entry - pos.sl
            target_4r = pos.entry + risk * self.config.rr_ratio
            target_3r = pos.entry + risk * (self.config.rr_ratio - 1)

            if current_price >= target_4r and not pos.trailing_activated:
                pos.trailing_activated = True
                new_sl = pos.entry + risk * (self.config.rr_ratio - 1)
                log.info(f'[TRAIL] Activated on {pos.symbol} BUY — SL moved to {new_sl:.5f} (1:3 lock)')
                return new_sl

            if pos.trailing_activated:
                trail_sl = current_price - risk * 0.5  # Trail 50% of risk distance
                if trail_sl > (pos.trailing_sl or pos.sl):
                    log.info(f'[TRAIL] Adjusting {pos.symbol} BUY SL → {trail_sl:.5f}')
                    return trail_sl

        else:  # SELL
            risk      = pos.sl - pos.entry
            target_4r = pos.entry - risk * self.config.rr_ratio
            target_3r = pos.entry - risk * (self.config.rr_ratio - 1)

            if current_price <= target_4r and not pos.trailing_activated:
                pos.trailing_activated = True
                new_sl = pos.entry - risk * (self.config.rr_ratio - 1)
                log.info(f'[TRAIL] Activated on {pos.symbol} SELL — SL moved to {new_sl:.5f} (1:3 lock)')
                return new_sl

            if pos.trailing_activated:
                trail_sl = current_price + risk * 0.5
                if trail_sl < (pos.trailing_sl or pos.sl):
                    log.info(f'[TRAIL] Adjusting {pos.symbol} SELL SL → {trail_sl:.5f}')
                    return trail_sl

        return None


# ══════════════════════════════════════════════
# MT5 BROKER ADAPTER
# ══════════════════════════════════════════════

class MT5Adapter:
    TIMEFRAME_MAP = {
        1: mt5.TIMEFRAME_M1 if MT5_AVAILABLE else 1,
        3: mt5.TIMEFRAME_M3 if MT5_AVAILABLE else 3,
        5: mt5.TIMEFRAME_M5 if MT5_AVAILABLE else 5,
        15: mt5.TIMEFRAME_M15 if MT5_AVAILABLE else 15,
        30: mt5.TIMEFRAME_M30 if MT5_AVAILABLE else 30,
        60: mt5.TIMEFRAME_H1 if MT5_AVAILABLE else 60,
    }

    def __init__(self, config: BotConfig):
        self.config = config
        self.connected = False

    def connect(self) -> bool:
        if not MT5_AVAILABLE:
            log.warning('MetaTrader5 library not installed. Install: pip install MetaTrader5')
            return False
        if not mt5.initialize(
            path=None,
            server=self.config.mt5_server,
            login=self.config.mt5_account,
            password=self.config.mt5_password
        ):
            log.error(f'MT5 init failed: {mt5.last_error()}')
            return False
        self.connected = True
        log.info(f'MT5 connected — account {mt5.account_info().login}')
        return True

    def disconnect(self):
        if MT5_AVAILABLE and self.connected:
            mt5.shutdown()

    def get_candles(self, symbol: str, timeframe: int, count: int = 200) -> list[Candle]:
        if not MT5_AVAILABLE or not self.connected:
            return []
        tf = self.TIMEFRAME_MAP.get(timeframe, mt5.TIMEFRAME_M5)
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None:
            return []
        return [Candle(
            time=datetime.fromtimestamp(r['time']),
            open=r['open'], high=r['high'], low=r['low'], close=r['close'],
            volume=r['tick_volume']
        ) for r in rates]

    def get_price(self, symbol: str) -> float:
        if not MT5_AVAILABLE or not self.connected:
            return 0
        tick = mt5.symbol_info_tick(symbol)
        return (tick.bid + tick.ask) / 2 if tick else 0

    def place_order(self, signal: Signal, lots: float) -> Optional[int]:
        if not MT5_AVAILABLE or not self.connected:
            return None
        order_type = mt5.ORDER_TYPE_BUY if signal.side == 'BUY' else mt5.ORDER_TYPE_SELL
        price = mt5.symbol_info_tick(signal.symbol).ask if signal.side == 'BUY' else mt5.symbol_info_tick(signal.symbol).bid
        request = {
            'action': mt5.TRADE_ACTION_DEAL,
            'symbol': signal.symbol,
            'volume': lots,
            'type': order_type,
            'price': price,
            'sl': signal.sl,
            'tp': signal.tp,
            'deviation': 20,
            'magic': 202501,
            'comment': f'AlgoTrader {signal.side}',
            'type_time': mt5.ORDER_TIME_GTC,
            'type_filling': mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            log.error(f'Order failed: {result.comment}')
            return None
        log.info(f'Order placed: ticket={result.order} {signal.side} {signal.symbol} @ {price}')
        return result.order

    def modify_sl(self, ticket: int, new_sl: float) -> bool:
        if not MT5_AVAILABLE or not self.connected:
            return False
        request = {
            'action': mt5.TRADE_ACTION_SLTP,
            'position': ticket,
            'sl': new_sl,
        }
        result = mt5.order_send(request)
        return result.retcode == mt5.TRADE_RETCODE_DONE

    def close_position(self, pos: Position) -> bool:
        if not MT5_AVAILABLE or not self.connected:
            return False
        order_type = mt5.ORDER_TYPE_SELL if pos.side == 'BUY' else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(pos.symbol)
        price = tick.bid if pos.side == 'BUY' else tick.ask
        request = {
            'action': mt5.TRADE_ACTION_DEAL,
            'symbol': pos.symbol,
            'volume': pos.lots,
            'type': order_type,
            'position': pos.ticket,
            'price': price,
            'deviation': 20,
            'magic': 202501,
            'comment': 'AlgoTrader close',
        }
        result = mt5.order_send(request)
        return result.retcode == mt5.TRADE_RETCODE_DONE

    def get_account_info(self) -> dict:
        if not MT5_AVAILABLE or not self.connected:
            return {}
        info = mt5.account_info()
        return {'balance': info.balance, 'equity': info.equity, 'margin': info.margin} if info else {}


# ══════════════════════════════════════════════
# CCXT (CRYPTO) ADAPTER
# ══════════════════════════════════════════════

class CCXTAdapter:
    def __init__(self, config: BotConfig):
        self.config = config
        self.exchange = None

    async def connect(self) -> bool:
        if not CCXT_AVAILABLE:
            log.warning('ccxt not installed. Install: pip install ccxt')
            return False
        broker = self.config.broker.lower()
        exchange_class = getattr(ccxt, broker, None)
        if not exchange_class:
            log.error(f'Exchange {broker} not found in ccxt')
            return False
        self.exchange = exchange_class({
            'apiKey': self.config.api_key,
            'secret': self.config.api_secret,
            'enableRateLimit': True,
        })
        try:
            await self.exchange.load_markets()
            log.info(f'CCXT connected to {broker}')
            return True
        except Exception as e:
            log.error(f'CCXT connect failed: {e}')
            return False

    async def get_candles(self, symbol: str, timeframe: int, count: int = 200) -> list[Candle]:
        if not self.exchange:
            return []
        tf_map = {1:'1m', 3:'3m', 5:'5m', 15:'15m', 30:'30m', 60:'1h'}
        tf = tf_map.get(timeframe, '5m')
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, tf, limit=count)
            return [Candle(
                time=datetime.fromtimestamp(c[0]/1000),
                open=c[1], high=c[2], low=c[3], close=c[4], volume=c[5]
            ) for c in ohlcv]
        except Exception as e:
            log.error(f'CCXT candles error: {e}')
            return []

    async def place_order(self, signal: Signal, lots: float) -> Optional[str]:
        if not self.exchange:
            return None
        try:
            side = signal.side.lower()
            order = await self.exchange.create_market_order(signal.symbol, side, lots)
            log.info(f'CCXT order: {order["id"]} {signal.side} {signal.symbol}')
            return order['id']
        except Exception as e:
            log.error(f'CCXT order failed: {e}')
            return None

    async def close(self):
        if self.exchange:
            await self.exchange.close()


# ══════════════════════════════════════════════
# RISK MANAGER
# ══════════════════════════════════════════════

class RiskManager:
    def __init__(self, config: BotConfig, principal: float):
        self.config = config
        self.principal = principal
        self.peak_balance = principal
        self.max_dd = 0.0
        self.trading_halted = False

    def check_drawdown(self, current_balance: float) -> bool:
        """Returns True if trading should continue, False if halt."""
        if current_balance > self.peak_balance:
            self.peak_balance = current_balance

        dd = (self.peak_balance - current_balance) / self.peak_balance * 100
        self.max_dd = max(self.max_dd, dd)

        if dd >= self.config.max_drawdown_pct:
            if not self.trading_halted:
                log.warning(f'MAX DRAWDOWN REACHED: {dd:.2f}% — HALTING TRADING')
                self.trading_halted = True
            return False
        return True

    def is_valid_session(self) -> bool:
        """Check if current UTC time is within allowed sessions."""
        now = datetime.utcnow()
        hour = now.hour
        london_open  = self.config.london_session and (7 <= hour < 17)
        ny_open      = self.config.ny_session and (12 <= hour < 21)
        return london_open or ny_open


# ══════════════════════════════════════════════
# TELEGRAM ALERTS
# ══════════════════════════════════════════════

async def send_telegram(token: str, chat_id: str, message: str):
    """Send Telegram notification."""
    if not token or not chat_id:
        return
    try:
        import urllib.request
        url = f'https://api.telegram.org/bot{token}/sendMessage'
        data = json.dumps({'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML'}).encode()
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        log.warning(f'Telegram send failed: {e}')


# ══════════════════════════════════════════════
# METRICS TRACKER
# ══════════════════════════════════════════════

class MetricsTracker:
    def __init__(self, principal: float):
        self.principal = principal
        self.trades: list[TradeRecord] = []

    def record_trade(self, trade: TradeRecord):
        self.trades.append(trade)

    def _filter_trades(self, since: datetime) -> list[TradeRecord]:
        return [t for t in self.trades if t.close_time >= since]

    def _accuracy(self, trades: list[TradeRecord]) -> float:
        if not trades: return 0.0
        wins = sum(1 for t in trades if t.pnl > 0)
        return round(wins / len(trades) * 100, 2)

    def compute(self, current_balance: float) -> DashboardMetrics:
        now = datetime.utcnow()
        daily   = self._filter_trades(now - timedelta(days=1))
        weekly  = self._filter_trades(now - timedelta(weeks=1))
        monthly = self._filter_trades(now - timedelta(days=30))
        all_acc = [self._accuracy(daily), self._accuracy(weekly), self._accuracy(monthly)]

        pnl = current_balance - self.principal
        all_pnls = [t.pnl for t in self.trades]
        max_dd   = min(all_pnls) if all_pnls else 0
        max_prof = max(all_pnls) if all_pnls else 0

        return DashboardMetrics(
            total_pnl=round(pnl, 2),
            capital=round(current_balance, 2),
            principal=self.principal,
            max_drawdown=round(max_dd, 2),
            max_profit=round(max_prof, 2),
            overall_accuracy=self._accuracy(self.trades),
            daily_accuracy=self._accuracy(daily),
            weekly_accuracy=self._accuracy(weekly),
            monthly_accuracy=self._accuracy(monthly),
            max_accuracy=round(max(all_acc) if all_acc else 0, 2),
            trade_history=[asdict(t) if hasattr(t, '__dataclass_fields__') else vars(t)
                          for t in self.trades[-20:]],
            bot_running=True,
            timestamp=now.isoformat()
        )


# ══════════════════════════════════════════════
# WEBSOCKET SERVER (Dashboard feed)
# ══════════════════════════════════════════════

class DashboardServer:
    def __init__(self, host='0.0.0.0', port=8765):
        self.host = host
        self.port = port
        self.clients: set = set()
        self.latest: dict = {}

    async def handler(self, ws):
        self.clients.add(ws)
        log.info(f'Dashboard client connected: {ws.remote_address}')
        try:
            if self.latest:
                await ws.send(json.dumps(self.latest))
            async for _ in ws:
                pass
        except Exception:
            pass
        finally:
            self.clients.discard(ws)

    async def broadcast(self, data: dict):
        self.latest = data
        if not self.clients:
            return
        msg = json.dumps(data, default=str)
        await asyncio.gather(*[c.send(msg) for c in list(self.clients)], return_exceptions=True)

    async def start(self):
        if not WS_AVAILABLE:
            log.warning('websockets not installed. Dashboard feed disabled.')
            return
        async with websockets.serve(self.handler, self.host, self.port):
            log.info(f'WebSocket server running on ws://{self.host}:{self.port}')
            await asyncio.Future()  # run forever


# ══════════════════════════════════════════════
# MAIN BOT
# ══════════════════════════════════════════════

class TradingBot:
    def __init__(self, config: BotConfig):
        self.config = config
        self.strategy = StrategyEngine(config)
        self.risk     = RiskManager(config, principal=50000.0)
        self.metrics  = MetricsTracker(principal=50000.0)
        self.dashboard= DashboardServer()
        self.open_positions: list[Position] = []
        self.current_balance = 50000.0
        self.running = True

        # Choose broker adapter
        if config.broker == 'MT5':
            self.broker = MT5Adapter(config)
            self.async_broker = False
        else:
            self.broker = CCXTAdapter(config)
            self.async_broker = True

    def connect_broker(self) -> bool:
        if self.async_broker:
            return True  # async connect called in run()
        return self.broker.connect()

    async def get_candles(self) -> list[Candle]:
        if self.async_broker:
            return await self.broker.get_candles(self.config.symbol, self.config.timeframe)
        return self.broker.get_candles(self.config.symbol, self.config.timeframe)

    async def place_order(self, signal: Signal) -> Optional[int]:
        if self.async_broker:
            return await self.broker.place_order(signal, self.config.lot_size)
        return self.broker.place_order(signal, self.config.lot_size)

    async def scan_and_trade(self):
        """Core trading loop — called every N seconds."""
        # Safety checks
        if not self.risk.check_drawdown(self.current_balance):
            log.warning('Trading halted due to drawdown limit.')
            return
        if not self.risk.is_valid_session():
            log.debug('Outside trading session — skipping.')
            return

        candles = await self.get_candles()
        if not candles:
            return

        signal = self.strategy.generate_signal(candles)

        if signal:
            log.info(f'SIGNAL: {signal.side} {signal.symbol} @ {signal.entry} | SL:{signal.sl} TP:{signal.tp}')
            ticket = await self.place_order(signal)
            if ticket:
                pos = Position(
                    ticket=ticket,
                    symbol=signal.symbol,
                    side=signal.side,
                    entry=signal.entry,
                    sl=signal.sl,
                    tp=signal.tp,
                    lots=self.config.lot_size
                )
                self.open_positions.append(pos)
                # Telegram alert
                msg = (f'<b>{signal.side}</b> {signal.symbol}\n'
                       f'Entry: {signal.entry}\nSL: {signal.sl}\nTP: {signal.tp}\n'
                       f'Reason: {signal.reason}')
                await send_telegram(self.config.telegram_token, self.config.telegram_chat_id, msg)

    async def manage_positions(self):
        """Trailing stop updates and position monitoring."""
        current_price = 0
        if not self.async_broker and hasattr(self.broker, 'get_price'):
            current_price = self.broker.get_price(self.config.symbol)

        for pos in list(self.open_positions):
            if current_price == 0:
                continue
            # Trailing stop logic
            new_sl = self.strategy.update_trailing_stop(pos, current_price)
            if new_sl is not None:
                pos.trailing_sl = new_sl
                if not self.async_broker:
                    self.broker.modify_sl(pos.ticket, new_sl)

    async def broadcast_metrics(self):
        """Compute and broadcast metrics to dashboard WebSocket clients."""
        m = self.metrics.compute(self.current_balance)
        m.open_positions = [asdict(p) if hasattr(p, '__dataclass_fields__') else vars(p)
                           for p in self.open_positions]
        await self.dashboard.broadcast({
            'type': 'metrics',
            'data': asdict(m) if hasattr(m, '__dataclass_fields__') else vars(m)
        })

    async def run(self):
        """Main async event loop."""
        log.info('AlgoTrader Pro starting...')

        if self.async_broker:
            await self.broker.connect()
        else:
            if not self.connect_broker():
                log.error('Broker connection failed — exiting.')
                return

        # Start WebSocket server in background
        asyncio.create_task(self.dashboard.start())

        tf_seconds = self.config.timeframe * 60
        last_scan  = 0
        last_bcast = 0

        log.info(f'Scanning {self.config.symbol} on {self.config.timeframe}m...')

        try:
            while self.running:
                now = time.time()

                if now - last_scan >= tf_seconds:
                    await self.scan_and_trade()
                    await self.manage_positions()
                    last_scan = now

                if now - last_bcast >= 5:  # broadcast every 5s
                    await self.broadcast_metrics()
                    last_bcast = now

                await asyncio.sleep(1)

        except KeyboardInterrupt:
            log.info('Bot stopped by user.')
        finally:
            if not self.async_broker:
                self.broker.disconnect()
            if self.async_broker and hasattr(self.broker, 'close'):
                await self.broker.close()
            log.info('Bot shutdown complete.')


# ══════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════

if __name__ == '__main__':
    config = BotConfig(
        symbol       = os.getenv('SYMBOL', 'XAUUSD'),
        timeframe    = int(os.getenv('TIMEFRAME', '5')),
        lot_size     = float(os.getenv('LOT_SIZE', '0.01')),
        max_sl_pips  = int(os.getenv('MAX_SL_PIPS', '50')),
        rr_ratio     = float(os.getenv('RR_RATIO', '4.0')),
        max_drawdown_pct = float(os.getenv('MAX_DRAWDOWN_PCT', '10.0')),
        broker       = os.getenv('BROKER', 'MT5'),
        api_key      = os.getenv('API_KEY', ''),
        api_secret   = os.getenv('API_SECRET', ''),
        mt5_server   = os.getenv('MT5_SERVER', ''),
        mt5_account  = int(os.getenv('MT5_ACCOUNT', '0')),
        mt5_password = os.getenv('MT5_PASSWORD', ''),
        telegram_token   = os.getenv('TELEGRAM_TOKEN', ''),
        telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID', ''),
        london_session   = os.getenv('LONDON_SESSION', 'true').lower() == 'true',
        ny_session       = os.getenv('NY_SESSION', 'true').lower() == 'true',
    )
    bot = TradingBot(config)
    asyncio.run(bot.run())
