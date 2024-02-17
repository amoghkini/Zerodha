from __future__ import annotations
from asyncio.exceptions import CancelledError
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    NoReturn,
    Tuple,
)
import os, sys, six, struct, contextlib, json, queue, asyncio, logging, websockets, websockets.client, signal, urllib.parse
from time import sleep
from pathlib import Path
from threading import Thread
from datetime import datetime
from websockets.typing import Data
from websockets.exceptions import (
    WebSocketException,
    ConnectionClosed,
    ConnectionClosedOK,
    ConnectionClosedError,
)
from websockets.client import WebSocketClientProtocol
from concurrent.futures import (
    ProcessPoolExecutor,
    ThreadPoolExecutor,
)
from multiprocessing import cpu_count
from logging.handlers import RotatingFileHandler
from datetime import datetime as dtdt


@staticmethod
def is_windows() -> bool:
    return (
        os.name == "nt" and sys.platform == "win32" and platform.system() == "Windows"
    )


if not is_windows() and sys.version_info >= (3, 8):
    try:
        import uvloop
    except (ImportError, ModuleNotFoundError):
        os.system(f"{sys.executable} -m pip install uvloop")
        import uvloop

    from signal import SIGABRT, SIGINT, SIGTERM, SIGHUP

    SIGNALS = (SIGABRT, SIGINT, SIGTERM, SIGHUP)
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
else:
    from signal import SIGABRT, SIGINT, SIGTERM

    SIGNALS = (SIGABRT, SIGINT, SIGTERM)
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from kiteconnect.__version__ import __version__, __title__

__all__ = ["KiteExtTicker"]

LOGGING_FORMAT: str = "[%(levelname)s]|[%(asctime)s]|[%(name)s::%(module)s::%(funcName)s::%(lineno)d]|=> %(message)s"


class AttrDict(dict):
    __slots__ = ()

    def __getattr__(self, attr):
        try:
            return self[attr]
        except KeyError:
            raise AttributeError(attr) from None

    def __setattr__(self, attr, value):
        self[attr] = value

    def __delattr__(self, attr):
        try:
            del self[attr]
        except KeyError:
            raise AttributeError(attr) from None

    def __dir__(self):
        return list(self) + dir(type(self))


class Callback:
    def __init__(
        self,
        callback: Callable,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        pool: Optional[Literal["thread", "process"]] = None,
        max_workers: int = cpu_count(),
    ) -> None:
        self.callback = callback
        self.is_async = asyncio.iscoroutinefunction(callback)
        self.loop = loop if loop is not None else asyncio.get_event_loop()
        self.pool = (
            ThreadPoolExecutor(max_workers=max_workers)
            if isinstance(pool, str) and pool == "thread"
            else ProcessPoolExecutor(max_workers=max_workers)
            if isinstance(pool, str) and pool == "process"
            else None
        )

    def __shutdown_pool(self) -> None:
        if self.pool is not None:
            self.pool.shutdown(wait=False, cancel_futures=True)

    def __del__(self) -> None:
        self.__shutdown_pool()

    def __delete__(self) -> None:
        self.__shutdown_pool()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.__shutdown_pool()

    def __aexit__(self) -> None:
        self.__shutdown_pool()

    async def __call__(self, obj):
        if self.callback is None:
            return
        elif self.is_async:
            await self.callback(obj)
        else:
            await self.loop.run_in_executor(self.pool, self.callback, obj)


class LoggerAdapter(logging.LoggerAdapter):
    """Add connection ID and client IP address to websockets logs."""

    def process(self, msg, kwargs):
        try:
            websocket = kwargs["extra"]["websocket"]
        except KeyError:
            return msg, kwargs
            kwargs["extra"]["event_data"] = {
                "connection_id": str(websocket.id),
                "remote_addr": websocket.request_headers.get("X-Forwarded-For"),
            }
        return msg, kwargs


@staticmethod
def get_logger(name, filename, level=logging.WARNING) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)

    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter(LOGGING_FORMAT))
    logger.addHandler(stream)

    fh = RotatingFileHandler(filename, maxBytes=100 * 1024 * 1024, backupCount=25)
    fh.setFormatter(logging.Formatter(LOGGING_FORMAT))
    logger.addHandler(fh)
    logger.propagate = False
    return logger


@staticmethod
def get_now_date_time_with_microseconds_string() -> str:
    return dtdt.now().strftime("%d_%b_%Y_%H_%M_%S_%f")


log = get_logger(
    __name__,
    Path(os.getcwd()).joinpath(
        f"logs/kiteext_ticker_{get_now_date_time_with_microseconds_string()}.log"
    ),
)


class KiteExtTicker:
    PING_INTERVAL: float = 2.5
    KEEPALIVE_INTERVAL: int = 5
    MAXDELAY: int = 5
    MAXRETRIES: int = 10
    # Default connection timeout
    CONNECT_TIMEOUT: int = 30
    # Default Reconnect max delay.
    RECONNECT_MAX_DELAY: int = 60
    # Default reconnect attempts
    RECONNECT_MAX_TRIES: int = 50
    CLOSE_TIMEOUT: int = 30
    # Default root API endpoint. It's possible to
    # override this by passing the `root` parameter during initialisation.
    KITE_API_WS_ROOT_URI: str = "wss://ws.kite.trade"
    KITE_WEB_WS_ROOT_URI: str = "wss://ws.zerodha.com"
    KITE_WEB_WS20_ROOT_URI: str = "wss://ws20.zerodha.com"
    KITE_MOBILE_WS_ROOT_URI: str = "wss://ws-mobile.zerodha.com"
    KITE_WEB_API_KEY: str = "kitefront"
    KITE_ANDROID_API_KEY: str = "kiteandroid"
    KITE_IOS_API_KEY: str = "kiteios"

    # Available streaming modes.
    MODE_FULL: str = "full"
    MODE_QUOTE: str = "quote"
    MODE_LTP: str = "ltp"

    SUBSCRIBE: str = "SUBSCRIBE"
    UNSUBSCRIBE: str = "UNSUBSCRIBE"
    SETMODE: str = "SETMODE"

    TICK_UPDATE: str = "TICK_UPDATE"
    ORDER_UPDATE: str = "ORDER_UPDATE"
    ERROR_UPDATE: str = "ERROR_UPDATE"
    MESSAGE_UPDATE: str = "MESSAGE_UPDATE"
    ACCESS_TOKEN_UPDATE: str = "ACCESS_TOKEN_UPDATE"
    SYNTH_FUT_ATM_UPDATE: str = "SYNTH_FUT_ATM_UPDATE"
    EXIT_CALL: str = "EXIT_CALL"
    ASK_SUBSCRIPTION: str = "ASK_SUBSCRIPTION"

    # Flag to set if its first connect
    IS_FIRST_CONNECT = True

    # Available actions.
    MESSAGE_CODE: int = 11
    MESSAGE_SUBSCRIBE: str = "subscribe"
    MESSAGE_UNSUBSCRIBE: str = "unsubscribe"
    MESSAGE_SETMODE: str = "mode"

    # Minimum delay which should be set between retries. User can't set less than this
    MINIMUM_RECONNECT_MAX_DELAY: int = 5
    # Maximum number or retries user can set
    MAXIMUM_RECONNECT_MAX_TRIES: int = 300

    CUSTOM_HEADERS: Dict[str, str] = {
        "X-Kite-Version": "3",  # For version 3
    }
    PING_PAYLOAD: str = ""
    ON_TICKS: str = "ON_TICKS"
    ON_EXTENDED_TICKS: str = "ON_EXTENDED_TICKS"
    ON_ORDER_UPDATES: str = "ON_ORDER_UPDATES"
    EXCHANGE_MAP: Dict[str, int] = {
        "nse": 1,
        "nfo": 2,
        "cds": 3,
        "bse": 4,
        "bfo": 5,
        "bcd": 6,
        "mcx": 7,
        "mcxsx": 8,
        "indices": 9,
        # bsecds is replaced with it's official segment name bcd
        # so,bsecds key will be depreciated in next version
        "bsecds": 6,
    }
    BROWSER_USER_AGENT: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.2210.91"

    @staticmethod
    def _user_agent() -> str:
        return (__title__ + "-python/").capitalize() + __version__

    @staticmethod
    def default_callbacks() -> Dict[str, Optional[Callable]]:
        return dict.fromkeys(
            (
                KiteExtTicker.ON_TICKS.lower(),
                KiteExtTicker.ON_ORDER_UPDATES.lower(),
            )
        )

    @staticmethod
    def parse_text_message(
        payload: Data,
    ) -> None | Tuple[str, str] | Tuple[str, Dict[str, Any]]:
        """Parse text message."""
        # Decode unicode data
        if not six.PY2 and type(payload) == bytes:
            payload = payload.decode("utf-8")
        try:
            data = json.loads(payload)
        except ValueError:
            return

        # Order update callback
        if data.get("type") is not None and data["type"] == "order":
            if data.get("data") is not None:
                return KiteExtTicker.ORDER_UPDATE, data["data"]
            return KiteExtTicker.ORDER_UPDATE, data

        # Custom error with websocket error code 0
        if data.get("type") is not None and data["type"] == "error":
            if data.get("data") is not None:
                return KiteExtTicker.ERROR_UPDATE, data["data"]
            return KiteExtTicker.ERROR_UPDATE, data

        if data.get("type") is not None and data["type"] == "message":
            if data.get("data") is not None:
                return KiteExtTicker.MESSAGE_UPDATE, data["data"]
            return KiteExtTicker.MESSAGE_UPDATE, data

        if data.get("type") is not None and data["type"] == "instruments_meta":
            if data.get("data") is not None:
                return KiteExtTicker.MESSAGE_UPDATE, data["data"]
            return KiteExtTicker.MESSAGE_UPDATE, data

    @staticmethod
    def unpack_int(bin: bytes, start: int, end: int, byte_format: str = "I") -> int:
        """Unpack binary data as unsgined interger."""
        return struct.unpack(">" + byte_format, bin[start:end])[0]

    @staticmethod
    def split_packets(bin: bytes) -> None | List[bytes]:
        """Split the data to individual packets of ticks."""
        # Ignore heartbeat data. (len(bin) < 2)
        if len(bin) >= 2:
            j = 2
            packets = []
            number_of_packets = KiteExtTicker.unpack_int(bin, 0, 2, byte_format="H")
            for i in range(number_of_packets):
                packet_length = KiteExtTicker.unpack_int(bin, j, j + 2, byte_format="H")
                packets.append(bin[j + 2 : j + 2 + packet_length])
                j = j + 2 + packet_length
            return packets

    @staticmethod
    def parse_binary_packet(packet: bytes) -> Dict[str, Any]:
        """Parse binary data ticks structure."""
        tick_data = {}
        instrument_token = KiteExtTicker.unpack_int(packet, 0, 4)
        # Retrive segment constant from instrument_token
        segment = instrument_token & 0xFF
        # Add price divisor based on segment
        if segment == KiteExtTicker.EXCHANGE_MAP["cds"]:
            divisor = 10000000.0
        elif segment == KiteExtTicker.EXCHANGE_MAP["bcd"]:
            divisor = 10000.0
        else:
            divisor = 100.0

        # All indices are not tradable
        tradable = not (segment == KiteExtTicker.EXCHANGE_MAP["indices"])
        # LTP packets
        if len(packet) == 8:
            tick_data.update(
                {
                    "tradable": tradable,
                    "mode": KiteExtTicker.MODE_LTP,
                    "instrument_token": instrument_token,
                    "last_price": KiteExtTicker.unpack_int(packet, 4, 8) / divisor,
                }
            )

        # Indices quote and full mode
        if len(packet) == 28 or len(packet) == 32:
            mode = (
                KiteExtTicker.MODE_QUOTE
                if len(packet) == 28
                else KiteExtTicker.MODE_FULL
            )
            tick_data.update(
                {
                    "tradable": tradable,
                    "mode": mode,
                    "instrument_token": instrument_token,
                    "last_price": KiteExtTicker.unpack_int(packet, 4, 8) / divisor,
                    "ohlc": {
                        "high": KiteExtTicker.unpack_int(packet, 8, 12) / divisor,
                        "low": KiteExtTicker.unpack_int(packet, 12, 16) / divisor,
                        "open": KiteExtTicker.unpack_int(packet, 16, 20) / divisor,
                        "close": KiteExtTicker.unpack_int(packet, 20, 24) / divisor,
                    },
                }
            )
            # Compute the change price using close price and last price
            tick_data["change"] = 0
            if tick_data["ohlc"]["close"] != 0:
                tick_data["change"] = (
                    (tick_data["last_price"] - tick_data["ohlc"]["close"])
                    * 100
                    / tick_data["ohlc"]["close"]
                )

            # Full mode with timestamp
            if len(packet) == 32:
                try:
                    timestamp = datetime.fromtimestamp(
                        KiteExtTicker.unpack_int(packet, 28, 32)
                    )
                except Exception:
                    timestamp = None

                tick_data["exchange_timestamp"] = timestamp

        # Quote and full mode
        if len(packet) == 44 or len(packet) == 184:
            mode = (
                KiteExtTicker.MODE_QUOTE
                if len(packet) == 44
                else KiteExtTicker.MODE_FULL
            )
            tick_data.update(
                {
                    "tradable": tradable,
                    "mode": mode,
                    "instrument_token": instrument_token,
                    "last_price": KiteExtTicker.unpack_int(packet, 4, 8) / divisor,
                    "last_traded_quantity": KiteExtTicker.unpack_int(packet, 8, 12),
                    "average_traded_price": KiteExtTicker.unpack_int(packet, 12, 16)
                    / divisor,
                    "volume_traded": KiteExtTicker.unpack_int(packet, 16, 20),
                    "total_buy_quantity": KiteExtTicker.unpack_int(packet, 20, 24),
                    "total_sell_quantity": KiteExtTicker.unpack_int(packet, 24, 28),
                    "ohlc": {
                        "open": KiteExtTicker.unpack_int(packet, 28, 32) / divisor,
                        "high": KiteExtTicker.unpack_int(packet, 32, 36) / divisor,
                        "low": KiteExtTicker.unpack_int(packet, 36, 40) / divisor,
                        "close": KiteExtTicker.unpack_int(packet, 40, 44) / divisor,
                    },
                }
            )

            # Compute the change price using close price and last price
            tick_data["change"] = 0
            if tick_data["ohlc"]["close"] != 0:
                tick_data["change"] = (
                    (tick_data["last_price"] - tick_data["ohlc"]["close"])
                    * 100
                    / tick_data["ohlc"]["close"]
                )

            # Parse full mode
            if len(packet) == 184:
                try:
                    last_trade_time = datetime.fromtimestamp(
                        KiteExtTicker.unpack_int(packet, 44, 48)
                    )
                except Exception:
                    last_trade_time = None

                try:
                    timestamp = datetime.fromtimestamp(
                        KiteExtTicker.unpack_int(packet, 60, 64)
                    )
                except Exception:
                    timestamp = None

                tick_data["last_trade_time"] = last_trade_time
                tick_data["oi"] = KiteExtTicker.unpack_int(packet, 48, 52)
                tick_data["oi_day_high"] = KiteExtTicker.unpack_int(packet, 52, 56)
                tick_data["oi_day_low"] = KiteExtTicker.unpack_int(packet, 56, 60)
                tick_data["exchange_timestamp"] = timestamp

                # Market depth entries.
                depth = {"buy": [], "sell": []}

                # Compile the market depth lists.
                for i, p in enumerate(range(64, len(packet), 12)):
                    depth["sell" if i >= 5 else "buy"].append(
                        {
                            "quantity": KiteExtTicker.unpack_int(packet, p, p + 4),
                            "price": KiteExtTicker.unpack_int(packet, p + 4, p + 8)
                            / divisor,
                            "orders": KiteExtTicker.unpack_int(
                                packet, p + 8, p + 10, byte_format="H"
                            ),
                        }
                    )

                tick_data["depth"] = depth
        # Parse full mode with extended (20-depth) ticker
        if len(packet) == 492:
            tick_data.update(
                {
                    "tradable": tradable,
                    "mode": KiteExtTicker.MODE_FULL,
                    "instrument_token": instrument_token,
                    "last_price": KiteExtTicker.unpack_int(packet, 4, 8) / divisor,
                }
            )
            try:
                timestamp = datetime.fromtimestamp(
                    KiteExtTicker.unpack_int(packet, 8, 12)
                )
            except Exception:
                timestamp = None

            tick_data["exchange_timestamp"] = timestamp

            # Market 20 depth entries.
            extended_depth = {"buy": [], "sell": []}

            # Compile the market depth lists.
            for i, p in enumerate(range(12, len(packet), 12)):
                extended_depth["sell" if i >= 20 else "buy"].append(
                    {
                        "quantity": KiteExtTicker.unpack_int(packet, p, p + 4),
                        "price": KiteExtTicker.unpack_int(packet, p + 4, p + 8)
                        / divisor,
                        "orders": KiteExtTicker.unpack_int(packet, p + 8, p + 12),
                    }
                )
            tick_data["20_depth"] = extended_depth
        return tick_data

    @staticmethod
    def parse_binary(bin: bytes) -> None | List[Dict[str, Any]]:
        """Parse binary data to a (list of) ticks structure."""
        # Ignore heartbeat data.
        if len(bin) < 2:
            return
        else:
            # split data to individual ticks packet
            packets = KiteExtTicker.split_packets(bin)
            if packets is not None:
                return [KiteExtTicker.parse_binary_packet(packet) for packet in packets]

    @staticmethod
    def _start_background_loop(loop: asyncio.AbstractEventLoop) -> Optional[NoReturn]:
        asyncio.set_event_loop(loop)
        try:
            loop.run_forever()
        except (KeyboardInterrupt, SystemExit):
            loop.run_until_complete(loop.shutdown_asyncgens())
            if loop.is_running():
                loop.stop()
            if not loop.is_closed():
                loop.close()

    def __aenter__(self) -> "KiteExtTicker":
        return self

    def __enter__(self) -> "KiteExtTicker":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._graceful_exit()

    def __del__(self) -> None:
        self._graceful_exit()

    def __delete__(self) -> None:
        self._graceful_exit()

    def create_ticker_feed_url(self) -> None:
        self.ticker_feed_url = (
            (
                "{root}?api_key={api_key}"
                "&user_id={user_id}"
                "&access_token={access_token}".format(
                    root=self._root,
                    api_key=self.api_key,
                    user_id=self.user_id,
                    access_token=urllib.parse.quote(self.access_token),
                )
            )
            if self.api_key
            in [
                self.KITE_WEB_API_KEY,
                self.KITE_ANDROID_API_KEY,
                self.KITE_IOS_API_KEY,
            ]
            else (
                "{root}?api_key={api_key}" "&access_token={access_token}".format(
                    root=self._root,
                    api_key=self.api_key,
                    access_token=self.access_token,
                )
            )
        )
        log.info("Feed Url Created as: %s", self.ticker_feed_url)

    def refresh_token_from_file(self) -> None:
        token_data = None
        with open(self.token_path, "r") as file:
            token_data = json.load(file)
        if token_data is not None:
            self.access_token = token_data["access_token"]
            self.create_ticker_feed_url()

    def __init__(
        self,
        user_id: str = "",
        api_key: str = KITE_WEB_API_KEY,
        access_token: str = "",
        root: Optional[str] = None,
        debug: bool = False,
        debug_verbose: bool = False,
        log_tick_updates: bool = False,
        log_order_updates: bool = True,
        callbacks: Optional[Dict[str, Callback | Callable]] = None,
        reconnect: bool = True,
        reconnect_max_tries: int = RECONNECT_MAX_TRIES,
        reconnect_max_delay: int = RECONNECT_MAX_DELAY,
        connect_timeout: int = CONNECT_TIMEOUT,
        with_extended_ticker: bool = False,
        token_path: Path = Path(os.getcwd()).joinpath("access_token.json"),
    ) -> None:
        if user_id == "":
            raise ValueError(
                "`user_id` value can not be empty, Please Try again with correct value of `user_id`."
            )
        self.user_id = user_id
        self.api_key = api_key
        if isinstance(access_token, str) and access_token == "":
            token_data = None
            with open(token_path, "r") as file:
                token_data = json.load(file)
            if token_data is not None:
                self.access_token = token_data["access_token"]
                if self.access_token is not None and isinstance(self.access_token, str):
                    pass
                else:
                    raise ValueError(
                        "`access_token` value can not be empty, Please Try again with correct value of `access_token` or ensure the token file specified in the token path conatins valid `access_token`."
                    )
        else:
            self.access_token = access_token
        self.debug = debug
        self.debug_verbose = debug_verbose
        self.log_tick_updates = log_tick_updates
        self.log_order_updates = log_order_updates
        self.token_path = token_path
        self.log_level = (
            logging.INFO
            if self.debug
            else logging.DEBUG
            if self.debug_verbose
            else logging.WARNING
        )
        log.setLevel(self.log_level)
        logging.getLogger("websockets").addHandler(logging.NullHandler())
        if self.debug:
            logging.basicConfig(format=LOGGING_FORMAT, level=self.log_level)
            logging.getLogger("websockets").propagate = False
            logging.getLogger("websockets").setLevel(logging.WARNING)
        if self.debug_verbose:
            log.propagate = self.debug_verbose
            logging.basicConfig(format=LOGGING_FORMAT, level=self.log_level)
            logging.getLogger("websockets").propagate = self.debug_verbose
            logging.getLogger("websockets").setLevel(self.log_level)
        self.callbacks = callbacks
        self.on_tick = None
        self.on_extended_tick = None
        self.on_order_update = None
        self.ticker_feed = None
        self.ticker_feed_running = False
        self.called_for_exit = False
        self.should_run = True
        self._root = root or self.KITE_API_WS_ROOT_URI
        self.create_ticker_feed_url()
        self.tickdata = {}
        self.subscribed_tokens = {}
        self.order_updates_data = {}
        self._ticker_feed_first_run = True
        self._fresh_access_token_event = False
        self.stop_stream_queue = queue.Queue()
        self.__initialize_loop()
        if self.callbacks is not None and isinstance(self.callbacks, dict):
            for k, v in self.callbacks.items():
                if k.lower() == KiteExtTicker.ON_TICKS.lower():
                    self.on_tick = (
                        Callback(v, loop=self._loop) if isinstance(v, Callable) else v
                    )
                if k.lower() == KiteExtTicker.ON_ORDER_UPDATES.lower():
                    self.on_order_update = (
                        Callback(v, loop=self._loop) if isinstance(v, Callable) else v
                    )
        self.run()

    def _graceful_exit(self) -> None:
        with contextlib.suppress(RuntimeError, RuntimeWarning):
            self.stop()
            asyncio.run_coroutine_threadsafe(
                self._loop.shutdown_asyncgens(), self._loop
            ).result(15.0)
            if self._loop.is_running():
                self._loop.stop()
            if not self._loop.is_closed():
                self._loop.close()

    def _handle_stop_signals(self, *args, **kwargs):
        try:
            self._graceful_exit()
        except Exception as err:
            log.error(str(err))
        else:
            exit()

    def __initialize_loop(self) -> None:
        if is_windows():
            self._loop = asyncio.new_event_loop()
            with contextlib.suppress(ValueError):
                for sig in SIGNALS:
                    signal.signal(sig, self._handle_stop_signals)
        else:
            self._loop = uvloop.new_event_loop()
            with contextlib.suppress(ValueError):
                for sig in SIGNALS:
                    self._loop.add_signal_handler(sig, self._handle_stop_signals)  # noqa E501
        self._event_thread = Thread(
            target=self._start_background_loop,
            args=(self._loop,),
            name=f"{self.__class__.__name__}_event_thread",
            daemon=True,
        )
        self._event_thread.start()
        log.info("KiteExtTicker Event Loop has been initialized.")

    async def get_ws_client(self) -> WebSocketClientProtocol:
        uri = self.ticker_feed_url
        return await websockets.client.connect(
            uri,
            logger=LoggerAdapter(
                get_logger("websockets.client", "websocket.log", level=self.log_level),
                None,
            )
            if self.debug or self.debug_verbose
            else None,
            user_agent_header=self._user_agent(),
            extra_headers=self.CUSTOM_HEADERS,
            open_timeout=self.CONNECT_TIMEOUT,
            ping_interval=self.PING_INTERVAL,
            ping_timeout=self.KEEPALIVE_INTERVAL,
            close_timeout=self.CLOSE_TIMEOUT,
        )

    async def __on_open(self) -> None:
        if self.ticker_feed is not None and self.ticker_feed_running:
            instruments = {
                "NIFTY 50": 256265,
                "NIFTY BANK": 260105,
                "NIFTY FIN SERVICE": 257801,
                "NIFTY MID SELECT": 288009,
                "BANKEX": 274441,
                "SENSEX": 265,
            }
            log.info("Sending On Open Packet To Price Feed.")
            await self.__subscribe(instruments)
            log.info("On Open Packet Sent To Price Feed Successfully")

    def run(self) -> None:
        try:
            if self._loop.is_running():
                self.run_future = asyncio.run_coroutine_threadsafe(
                    self.run_forever(), self._loop
                )
        except KeyboardInterrupt:
            log.info("Keyboard Interrupt Detected, Bye...")

    def stop(self) -> None:
        if self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self.stop_ws(),
                self._loop,
            ).result(7.5)
            if self.run_future.running():
                self.run_future.cancel()
                while not self.run_future.done():
                    sleep(0.025)

    async def stop_ws(self) -> None:
        self.should_run = False
        if self.stop_stream_queue.empty():
            self.stop_stream_queue.put_nowait({"should_stop": True})
        await asyncio.sleep(1.0)
        await self.close()

    async def close(self, with_exceptions: bool = False) -> None:
        log_msg = "Facing An Error, " if with_exceptions else ""
        log_msg += "Ticker Feed Websocket Disconnected With Endpoint: %s"
        if self.ticker_feed is not None:
            log.info("Initiating Websocket Closing Procedures.")
            if self.ticker_feed_running and self.ticker_feed.open:
                unsubscribe_frame = json.dumps(
                    {
                        "a": KiteExtTicker.MESSAGE_UNSUBSCRIBE,
                        "v": list(self.subscribed_tokens.keys()),
                    }
                )
                await self.ticker_feed.send(unsubscribe_frame)
                await asyncio.sleep(0.25)
            if not self.ticker_feed.closed:
                log.info("Calling websocket.close() task.")
                try:
                    async with asyncio.timeout(2.5):
                        await self.ticker_feed.close()
                        # await self.ticker_feed.wait_closed()
                except TimeoutError:
                    log.critical(
                        "Websocket Closing Task Timed Out After Waiting For 2.5 Seconds"
                    )
                else:
                    log.info("Calling websocket.close() completed succesfully.")
            self.ticker_feed_running, self.ticker_feed = False, None
            log.info(log_msg, self.ticker_feed_url)

    async def connect(self) -> None:
        log.info("Connecting To Websocket Endpoint: %s", self.ticker_feed_url)
        self.ticker_feed = await self.get_ws_client()
        await asyncio.sleep(0.01)
        if self.ticker_feed is not None and isinstance(
            self.ticker_feed, WebSocketClientProtocol
        ):
            self.ticker_feed_running = True
            log.info(
                "Websocket Endpoint: %s Connected Successfully.", self.ticker_feed_url
            )
            await asyncio.sleep(0.1)
            if self._ticker_feed_first_run:
                self._ticker_feed_first_run = False
                await self.__on_open()
            else:
                if len(self.subscribed_tokens) > 0:
                    await self.__resubscribe()
            await asyncio.sleep(0.25)

    async def run_forever(self) -> None:
        await asyncio.sleep(0.1)
        reconnect_sleep_time: float = 0.5
        while True:
            if (
                not self.stop_stream_queue.empty()
                or not self.should_run
                or self.called_for_exit
            ):
                # self.stop_stream_queue.get(timeout=1.0)
                log.info("Ticker Feeds Stopped Successfully.")
                return
            if not self.ticker_feed_running:
                log.info("Initializing Or Restarting Ticker Feed Websocket Stream.")
                await self.connect()
            try:
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(self.is_ticker_feed_runnig())
                    tg.create_task(self.consume())
                    tg.create_task(self.ping())
            except* WebSocketException as wse:
                log.warning(
                    "Ticker Feeds Websocket Has Met With An Exception, During Its Communication With Zerodha Kite Servers, Exception Was: %s",
                    wse,
                )
                log.exception(wse, exc_info=True)
                await self.close(with_exceptions=True)
                await asyncio.sleep(reconnect_sleep_time)
                reconnect_sleep_time += 0.05
                if not self._fresh_access_token_event:
                    self.refresh_token_from_file()
                else:
                    self._fresh_access_token_event = False
            except* Exception as exc:
                log.critical(
                    "An Exception Has Occured While Consuming The Stream From Ticker Feed. Please Check For Correction In The Callbacks If Any., Exception Was: %s",
                    exc,
                )
                log.exception(exc, exc_info=True)
            await asyncio.sleep(0.001)

    async def is_ticker_feed_runnig(self) -> None:
        await asyncio.sleep(0.1)
        while True:
            if (
                not self.stop_stream_queue.empty()
                or not self.should_run
                or self.called_for_exit
            ):
                # self.stop_stream_queue.get(timeout=1.0)
                log.info(
                    "Ticker Feeds Process `is_ticker_feed_runnig` Task Stopped Successfully."
                )
                return
            try:
                if self.ticker_feed is not None:
                    await self.ticker_feed.ensure_open()
            except ConnectionClosed:
                self.ticker_feed_running = False
                raise WebSocketException(
                    "Websocket Connection Was Found To Be Closed, hence Can't Ping Pong."
                )
            await asyncio.sleep(0.25)

    async def ping(self) -> None:
        await asyncio.sleep(0.3)
        while True:
            if (
                not self.stop_stream_queue.empty()
                or not self.should_run
                or self.called_for_exit
            ):
                # self.stop_stream_queue.get(timeout=1.0)
                log.info("Ticker Feeds PINPONG Task Stopped Successfully.")
                return
            if not self.ticker_feed_running:
                log.info(
                    "Ticker Feeds Not Running, As A Result, PINPONG Task Stopped Successfully."
                )
                return
            try:
                async with asyncio.timeout(KiteExtTicker.PING_INTERVAL):
                    await asyncio.sleep(KiteExtTicker.PING_INTERVAL + 1.0)
            except TimeoutError:
                if self.ticker_feed is not None and self.ticker_feed.open:
                    try:
                        async with asyncio.timeout(KiteExtTicker.KEEPALIVE_INTERVAL):
                            try:
                                # await self.ticker_feed.ping(data=self.PING_PAYLOAD)
                                pong_waiter = await self.ticker_feed.ping(
                                    data=self.PING_PAYLOAD
                                )
                                log.info("Ticker Feed Pinged.")
                                # A future that will be completed when the corresponding pong is received. You can ignore it if you don’t intend to wait. The result of the future is the latency of the connection in seconds.
                                # Here we do not want to wait for the corresponding pong packet so commenting the below line.
                                latency = await pong_waiter
                                log.info(
                                    "Ticker Feed Pong Received With Latency: %s",
                                    str(latency),
                                )
                            except ConnectionClosed:
                                self.ticker_feed_running = False
                                raise WebSocketException(
                                    "Websocket Connection Was Found To Be Closed, hence Can't Ping Pong."
                                )
                            except (RuntimeError, RuntimeWarning):
                                continue
                            except CancelledError:
                                continue
                            except Exception:
                                raise WebSocketException(
                                    "While Sending PING and waiting for PONG from `ping` function Exception Occured"
                                )
                    except TimeoutError:
                        self.ticker_feed_running = False
                        raise WebSocketException(
                            "Ticker Feed Websocket Connection Stale As Pong is not received within the keep alive time interval of 5 seconds."
                        )
                    except (RuntimeError, RuntimeWarning):
                        continue
                    except CancelledError:
                        continue
                    except Exception as err:
                        log.exception(
                            "While Sending PING and waiting for PONG from `ping` function, Exception Occured: %s",
                            err,
                        )
                        raise WebSocketException(
                            "While Sending PING and waiting for PONG from `ping` function Exception Occured"
                        )
            await asyncio.sleep(0.001)

    async def consume(self) -> None:
        await asyncio.sleep(0.2)
        while True:
            data = None
            if (
                not self.stop_stream_queue.empty()
                or not self.should_run
                or self.called_for_exit
            ):
                # self.stop_stream_queue.get(timeout=1.0)
                log.info("Ticker Feeds Consume Task Stopped Successfully.")
                return
            if not self.ticker_feed_running:
                log.info(
                    "Ticker Feeds Not Running, As A Result, Consume Task Stopped Successfully."
                )
                return
            try:
                async with asyncio.timeout(1.0):
                    if self.ticker_feed is not None:
                        try:
                            data = await self.ticker_feed.recv()
                        except (
                            ConnectionClosed,
                            ConnectionClosedOK,
                            ConnectionClosedError,
                        ):
                            self.ticker_feed_running = False
                            raise WebSocketException(
                                "Websocket Connection Was Found To Be Closed, hence Can't Receive And Consume Any Data."
                            )
                        except (RuntimeError, RuntimeWarning):
                            continue
            except TimeoutError:
                continue
            except (RuntimeError, RuntimeWarning):
                continue
            else:
                try:
                    if data is not None:
                        await self.dispatch(data)
                except TimeoutError:
                    continue
                except Exception as exc:
                    log.critical(
                        "While Dispatching from `consume` function, Exception Occured: %s",
                    )
                    log.exception(exc, exc_info=True)
            await asyncio.sleep(0.001)

    async def cast(
        self,
        to: str,
        data: Data | Dict[str, Any] | List[AttrDict] | List[Dict[str, Any]],
    ):
        match to.upper():
            case KiteExtTicker.ON_TICKS:
                log.info("Casting on `on_ticks` callback.")
                if self.on_tick is not None and isinstance(self.on_tick, Callback):
                    try:
                        await self.on_tick(data)
                    except Exception as exc:
                        log.critical(
                            "While Executing `on_ticks` Callback Function, Exception Occured: %s",
                        )
                        log.exception(exc, exc_info=True)
            case KiteExtTicker.ON_ORDER_UPDATES:
                log.info("Casting on `on_order_updates` callback.")
                if self.on_order_update is not None and isinstance(
                    self.on_order_update, Callback
                ):
                    try:
                        await self.on_order_update(data)
                    except Exception as exc:
                        log.critical(
                            "While Executing `on_order_updates` Callback Function, Exception Occured: %s",
                        )
                        log.exception(exc, exc_info=True)

    async def dispatch(
        self,
        data: Data,
    ) -> None:
        if isinstance(data, bytes) and len(data) > 4:
            try:
                await self.process_price_frame(data)
            except Exception as exc:
                log.critical("An Exception Has Occured While Dispatching Tick Datas.")
                log.exception(exc, exc_info=True)
        else:
            try:
                await self.process_order_or_error_frame(data)
            except Exception as exc:
                log.critical(
                    "An Exception Has Occured While Dispatching Order Datas / Error Datas / Custom Message Datas."
                )
                log.exception(exc, exc_info=True)

    async def process_order_or_error_frame(self, frame: Data) -> None:
        _parsed_text_data = self.parse_text_message(frame)
        if _parsed_text_data is not None and isinstance(_parsed_text_data, tuple):
            _data_type, data = _parsed_text_data
            if _data_type == KiteExtTicker.ORDER_UPDATE and isinstance(data, dict):
                if self.on_order_update is None:
                    try:
                        await self.cast(KiteExtTicker.ON_ORDER_UPDATES, data)
                    except Exception as exc:
                        log.critical(
                            "An Exception Has Occured While Casting Order Updates Data."
                        )
                        log.exception(exc, exc_info=True)
                if self.log_order_updates:
                    log.info("Order Update: %s", data)

            if _data_type in {KiteExtTicker.ERROR_UPDATE, KiteExtTicker.MESSAGE_UPDATE}:
                log.info(
                    "Error Message Receieved On Ticker Feed: %s"
                    if _data_type == KiteExtTicker.ERROR_UPDATE
                    else "Custom Message Receieved On Ticker Feed: %s",
                    data,
                )

    async def process_price_frame(self, frame: Data) -> None:
        tick_datas = None
        if isinstance(frame, bytes):
            tick_datas = self.parse_binary(frame)
        if tick_datas is not None:
            if self.on_tick is not None:
                await self.cast(KiteExtTicker.ON_TICKS, tick_datas)
            if self.log_tick_updates:
                log.info("Tick Update: %s", tick_datas)

    async def __setmode(
        self,
        instruments: Dict[str, int],
        mode: str = MODE_FULL,
    ) -> None:
        log.info(
            "Instruments: %s, Modes To Be Set As: %s", list(instruments.keys()), mode
        )
        subscription_mode_frame = json.dumps(
            {"a": self.MESSAGE_SETMODE, "v": [mode, list(instruments.values())]}
        )
        await self._setmode(subscription_mode_frame)
        [
            self.subscribed_tokens[token].__setitem__("mode", mode)
            for token in instruments.values()
        ]
        [
            self.tickdata[tradingsymbol].__setitem__("mode", mode)
            for tradingsymbol, token in instruments.items()
            if self.tickdata[tradingsymbol]["instrument_token"] == token
        ]
        log.info("Instruments Mode Has Been Set To: %s Successfully.", mode)

    async def _setmode(
        self,
        subscription_mode_frame: str | bytes,
    ) -> None:
        for i in range(1, 6):
            if not self.ticker_feed_running:
                sleep_duration = i + i * 0.1
                await asyncio.sleep(sleep_duration)
                log.error(
                    "Ticker Feed Websocket Is Not Running, Sleeping For %s Second, Then Will Retry Setting Modes Of Subscribing Instruments. Retry No.: %s",
                    str(sleep_duration),
                    str(i),
                )
        if self.ticker_feed is not None and self.ticker_feed_running:
            await self.ticker_feed.send(subscription_mode_frame)
            log.info("Instruments Subscription Mode Set Packets Sent Successfully.")
        else:
            log.error(
                "Ticker Feed Websocket Is Not Running, Hence Can't Set Mode Of Subscribed Instruments."
            )

    def setmode(
        self,
        instruments: Dict[str, int],
        mode: str = MODE_FULL,
    ) -> None:
        log.info(
            "Instruments: %s, Modes To Be Set As: %s", list(instruments.keys()), mode
        )
        subscription_mode_frame = json.dumps(
            {"a": self.MESSAGE_SETMODE, "v": [mode, list(instruments.values())]}
        )
        future = asyncio.run_coroutine_threadsafe(
            self._setmode(subscription_mode_frame), self._loop
        )
        try:
            future.result(timeout=5.0)
        except TimeoutError:
            log.error(
                "The instruments setmode tasks took too long, cancelling the task..."
            )
            future.cancel()
        except Exception as exc:
            log.exception(
                "The instruments setmode tasks has raised an exception: %s",
                exc,
            )
        else:
            [
                self.subscribed_tokens[token].__setitem__("mode", mode)
                for token in instruments.values()
            ]
            [
                self.tickdata[tradingsymbol].__setitem__("mode", mode)
                for tradingsymbol, token in instruments.items()
                if self.tickdata[tradingsymbol]["instrument_token"] == token
            ]
            log.info("Instruments Mode Has Been Set To: %s Successfully.", mode)

    async def __subscribe(
        self,
        instruments: Dict[str, int],
        mode: str = MODE_FULL,
    ) -> None:
        log.info(
            "Instruments To Be Subscribed: %s, With Mode: %s",
            list(instruments.keys()),
            mode,
        )
        subscription_frame = json.dumps(
            {"a": KiteExtTicker.MESSAGE_SUBSCRIBE, "v": list(instruments.values())}
        )
        subscription_mode_frame = json.dumps(
            {
                "a": KiteExtTicker.MESSAGE_SETMODE,
                "v": [mode, list(instruments.values())],
            }
        )
        await self._subscribe(subscription_frame, subscription_mode_frame)
        self.subscribed_tokens.update(
            {
                token: {
                    "tradingsymbol": tradingsymbol,
                    "mode": mode,
                    "tick_data": None,
                    "extended_tick_data": None,
                }
                for tradingsymbol, token in instruments.items()
            }
        )
        self.tickdata.update(
            {
                tradingsymbol: {
                    "tradingsymbol": tradingsymbol,
                    "mode": mode,
                    "instrument_token": token,
                }
                for tradingsymbol, token in instruments.items()
            }
        )
        log.info("Instruments Subscribed Successfully In %s Mode.", mode)

    async def _subscribe(
        self,
        subscription_frame: str | bytes,
        subscription_mode_frame: str | bytes,
    ) -> None:
        for i in range(1, 6):
            if not self.ticker_feed_running:
                sleep_duration = i + i * 0.1
                await asyncio.sleep(sleep_duration)
                log.error(
                    "Ticker Feed Websocket Is Not Running, Sleeping For %s Second, Then Will Retry Subscribing Instruments. Retry No.: %s",
                    str(sleep_duration),
                    str(i),
                )
        if self.ticker_feed is not None and self.ticker_feed_running:
            await self.ticker_feed.send(subscription_frame)
            await self.ticker_feed.send(subscription_mode_frame)
            log.info("Instruments Subscription Packets Sent Successfully.")
        else:
            log.error("Ticker Feed Websocket Is Not Running, Hence Can't Subscribe.")

    def subscribe(
        self,
        instruments: Dict[str, int],
        mode: str = MODE_FULL,
    ) -> None:
        log.info(
            "Instruments To Be Subscribed: %s, With Mode: %s",
            list(instruments.keys()),
            mode,
        )
        subscription_frame = json.dumps(
            {"a": KiteExtTicker.MESSAGE_SUBSCRIBE, "v": list(instruments.values())}
        )
        subscription_mode_frame = json.dumps(
            {
                "a": KiteExtTicker.MESSAGE_SETMODE,
                "v": [mode, list(instruments.values())],
            }
        )
        future = asyncio.run_coroutine_threadsafe(
            self._subscribe(subscription_frame, subscription_mode_frame), self._loop
        )
        try:
            future.result(timeout=5.0)
        except TimeoutError:
            log.error(
                "The instruments subscription tasks took too long, cancelling the task..."
            )
            future.cancel()
        except Exception as exc:
            log.exception(
                "The instruments subscription tasks has raised an exception: %s",
                exc,
            )
        else:
            self.subscribed_tokens.update(
                {
                    token: {
                        "tradingsymbol": tradingsymbol,
                        "mode": mode,
                        "tick_data": None,
                        "extended_tick_data": None,
                    }
                    for tradingsymbol, token in instruments.items()
                }
            )
            self.tickdata.update(
                {
                    tradingsymbol: {
                        "tradingsymbol": tradingsymbol,
                        "mode": mode,
                        "instrument_token": token,
                    }
                    for tradingsymbol, token in instruments.items()
                }
            )
            log.info("Instruments Subscribed Successfully In %s Mode.", mode)

    async def __unsubscribe(self, instruments: Dict[str, int]) -> None:
        log.info("Instruments To Be Unsubscribed: %s", list(instruments.keys()))
        unsubscription_frame = json.dumps(
            {
                "a": KiteExtTicker.MESSAGE_UNSUBSCRIBE,
                "v": list(instruments.values()),
            }
        )
        await self._unsubscribe(unsubscription_frame)
        [self.subscribed_tokens.pop(token) for token in instruments.values()]
        [
            self.tickdata.pop(tradingsymbol)
            for tradingsymbol, token in instruments.items()
            if self.tickdata[tradingsymbol]["instrument_token"] == token
        ]
        log.info("Instruments Unsubscribed Successfully.")

    async def _unsubscribe(
        self,
        unsubscription_frame: str | bytes,
    ) -> None:
        for i in range(1, 6):
            if not self.ticker_feed_running:
                sleep_duration = i + i * 0.1
                await asyncio.sleep(sleep_duration)
                log.error(
                    "Ticker Feed Websocket Is Not Running, Sleeping For %s Second, Then Will Retry Unsubscribing Instruments. Retry No.: %s",
                    str(sleep_duration),
                    str(i),
                )
        if self.ticker_feed is not None and self.ticker_feed_running:
            await self.ticker_feed.send(unsubscription_frame)
            log.info("Instruments Unsubscription Packets Sent Successfully.")
            return
        else:
            log.error("Ticker Feed Websocket Is Not Running, Hence Can't Unsubscribe.")

    def unsubscribe(
        self,
        instruments: Dict[str, int],
    ) -> None:
        log.info("Instruments To Be Unsubscribed: %s", list(instruments.keys()))
        unsubscription_frame = json.dumps(
            {
                "a": KiteExtTicker.MESSAGE_UNSUBSCRIBE,
                "v": list(instruments.values()),
            }
        )
        future = asyncio.run_coroutine_threadsafe(
            self._unsubscribe(unsubscription_frame), self._loop
        )
        try:
            future.result(timeout=5.0)
        except TimeoutError:
            log.error(
                "The instruments unsubscription tasks took too long, cancelling the task..."
            )
            future.cancel()
        except Exception as exc:
            log.exception(
                "The instruments unsubscription tasks has raised an exception: %s",
                exc,
            )
        else:
            [self.subscribed_tokens.pop(token) for token in instruments.values()]
            [
                self.tickdata.pop(tradingsymbol)
                for tradingsymbol, token in instruments.items()
                if self.tickdata[tradingsymbol]["instrument_token"] == token
            ]
            log.info("Instruments Unsubscribed Successfully.")

    async def __resubscribe(self) -> None:
        log.info(
            "Instruments To Be Resubscribed: %s",
            [data["tradingsymbol"] for data in self.subscribed_tokens.values()],
        )
        await self._resubscribe()
        log.info("Instruments Resubscribed Successfully.")

    async def _resubscribe(self) -> None:
        _ltp_mode_tokens = [
            token
            for token, data in self.subscribed_tokens.items()
            if data["mode"] == KiteExtTicker.MODE_LTP
        ]
        _quote_mode_tokens = [
            token
            for token, data in self.subscribed_tokens.items()
            if data["mode"] == KiteExtTicker.MODE_QUOTE
        ]
        _full_mode_tokens = [
            token
            for token, data in self.subscribed_tokens.items()
            if data["mode"] == KiteExtTicker.MODE_FULL
        ]
        for i in range(1, 6):
            if not self.ticker_feed_running:
                sleep_duration = i + i * 0.1
                await asyncio.sleep(sleep_duration)
                log.error(
                    "Ticker Feed Websocket Is Not Running, Sleeping For %s Second, Then Will Retry Resubscribing Instruments. Retry No.: %s",
                    str(sleep_duration),
                    str(i),
                )
        if self.ticker_feed is not None and self.ticker_feed_running:
            if len(_ltp_mode_tokens) > 0:
                await self.ticker_feed.send(
                    json.dumps(
                        {
                            "a": KiteExtTicker.MESSAGE_SUBSCRIBE,
                            "v": _ltp_mode_tokens,
                        }
                    )
                )
                await self.ticker_feed.send(
                    json.dumps(
                        {
                            "a": KiteExtTicker.MESSAGE_SETMODE,
                            "v": [KiteExtTicker.MODE_LTP, _ltp_mode_tokens],
                        }
                    )
                )
                log.info(
                    "Instruments: %s, Resubscription Packets With Mode: %s Sent Successfully.",
                    [
                        self.subscribed_tokens[token]["tradingsymbol"]
                        for token in _ltp_mode_tokens
                    ],
                    KiteExtTicker.MODE_LTP,
                )
            if len(_quote_mode_tokens) > 0:
                await self.ticker_feed.send(
                    json.dumps(
                        {
                            "a": KiteExtTicker.MESSAGE_SUBSCRIBE,
                            "v": _quote_mode_tokens,
                        }
                    )
                )
                await self.ticker_feed.send(
                    json.dumps(
                        {
                            "a": KiteExtTicker.MESSAGE_SETMODE,
                            "v": [KiteExtTicker.MODE_QUOTE, _quote_mode_tokens],
                        }
                    )
                )
                log.info(
                    "Instruments: %s, Resubscription Packets With Mode: %s Sent Successfully.",
                    [
                        self.subscribed_tokens[token]["tradingsymbol"]
                        for token in _quote_mode_tokens
                    ],
                    KiteExtTicker.MODE_QUOTE,
                )
            if len(_full_mode_tokens) > 0:
                await self.ticker_feed.send(
                    json.dumps(
                        {
                            "a": KiteExtTicker.MESSAGE_SUBSCRIBE,
                            "v": _full_mode_tokens,
                        }
                    )
                )
                await self.ticker_feed.send(
                    json.dumps(
                        {
                            "a": KiteExtTicker.MESSAGE_SETMODE,
                            "v": [KiteExtTicker.MODE_FULL, _full_mode_tokens],
                        }
                    )
                )
                log.info(
                    "Instruments: %s, Resubscription Packets With Mode: %s Sent Successfully.",
                    [
                        self.subscribed_tokens[token]["tradingsymbol"]
                        for token in _full_mode_tokens
                    ],
                    KiteExtTicker.MODE_FULL,
                )
            return
        else:
            log.error("Ticker Feed Websocket Is Not Running, Hence Can't Resubscribe.")

    def resubscribe(self) -> None:
        log.info(
            "Instruments To Be Resubscribed: %s",
            [data["tradingsymbol"] for data in self.subscribed_tokens.values()],
        )
        future = asyncio.run_coroutine_threadsafe(self._resubscribe(), self._loop)
        try:
            future.result(timeout=15.0)
        except TimeoutError:
            log.error(
                "The instruments resubscription tasks took too long, cancelling the task..."
            )
            future.cancel()
        except Exception as exc:
            log.exception(
                "The instruments resubscription tasks has raised an exception: %s",
                exc,
            )
        else:
            log.info("Instruments Resubscribed Successfully.")
