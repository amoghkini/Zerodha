import signal
from time import sleep
from async_kiteext_ticker import KiteExtTicker

# you can pass your `api_key` in KiteExtTicker if you have one.
# In that case `user_id is not needed.
user_id = "Paste_Your_User_Id_Here"
access_token = "Paste_Your_Access_token_Or_Enctoken_Here"

instruments = {
    "BANKNIFTY2422144400PE": 11719682,
    "BANKNIFTY2422144500CE": 11719938,
    "BANKNIFTY2422144500PE": 11722242,
    "BANKNIFTY2422144600CE": 11722498,
    "BANKNIFTY2422144600PE": 11724290,
    "BANKNIFTY2422144700CE": 11724546,
    "BANKNIFTY2422144700PE": 11726338,
    "BANKNIFTY2422144800CE": 11726594,
    "BANKNIFTY2422144800PE": 11726850,
    "BANKNIFTY2422144900CE": 11727106,
    "BANKNIFTY2422144900PE": 11727362,
    "BANKNIFTY2422145000CE": 11727618,
    "BANKNIFTY2422145000PE": 11727874,
}

def on_ticks(data):
    print("Ticks Data => ", data)


def on_order_updates(data):
    print("Order Update Data => ", data)


ticker = KiteExtTicker(
    user_id=user_id,
    access_token=access_token,
    callbacks={
        "on_ticks": on_ticks,
        "on_order_updates": on_order_updates,
    },
    # log_tick_updates=True,
    # log_order_updates=True,
    # debug=True,  # Uncomment This For Debugging
    # debug_verbose=True,  # Uncomment This For Verbose Debugging
)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, ticker._handle_stop_signals)
    signal.signal(signal.SIGTERM, ticker._handle_stop_signals)
    sleep(0.1)
    ticker.subscribe(instruments=instruments, mode=ticker.MODE_FULL)
    while True:
        try:
            print("Dr. June Moone Says Hi! From Main Thread.")
        except KeyboardInterrupt:
            break
        else:
            sleep(1)
