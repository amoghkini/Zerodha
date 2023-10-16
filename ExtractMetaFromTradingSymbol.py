from __future__ import annotations
import re, pandas as pd
from typing import Dict
from functools import lru_cache
from datetime import date, datetime as dt

DERIVATIVES_TRADINGSYMBOL_META = re.compile(
    "(?P<instrument>[A-Z]+)(?P<expiry>[A-Z0-9]{5})(?P<instrument_type>[A-Z0-9.]+)"
)
OND_MAP = {"O": "Oct", "N": "Nov", "D": "Dec"}
INSTRUMENTS = pd.read_csv("https://api.kite.trade/instruments")


@lru_cache
def extract_metadata_from_tradingsymbol(
    tradingsymbol: str,
) -> Dict[str, str | float | date]:
    metadata = DERIVATIVES_TRADINGSYMBOL_META.match(tradingsymbol)
    if not metadata:
        raise ValueError(f"Could Not Retrieve Metadata From {tradingsymbol}")
    metadata = metadata.groupdict()
    additional_metadata = INSTRUMENTS.query("tradingsymbol == @tradingsymbol")
    if "CE" in metadata["instrument_type"]:
        metadata["strike"] = float(
            metadata["instrument_type"].replace("CE", "")
        )  # noqa E501
        metadata["instrument_type"] = "CE"
    elif "PE" in metadata["instrument_type"]:
        metadata["strike"] = float(
            metadata["instrument_type"].replace("PE", "")
        )  # noqa E501
        metadata["instrument_type"] = "PE"
    elif "FUT" in metadata["instrument_type"]:
        metadata["instrument_type"] = "FUT"
    else:
        metadata["instrument_type"] = additional_metadata["instrument_type"].values[0]
    if metadata["expiry"][2:].isalpha():
        metadata["expiry_type"] = "Monthly"
    else:
        metadata["expiry_type"] = "Weekly"
        metadata["expiry_dt"] = dt.strptime(
            metadata["expiry"].replace(
                metadata["expiry"][2], OND_MAP[metadata["expiry"][2]]
            ),
            "%y%b%d",
        ).date()
    metadata.update(
        {
            "expiry": additional_metadata["expiry"].values[0],
            "tick_size": additional_metadata["tick_size"].values[0],
            "lot_size": additional_metadata["lot_size"].values[0],
            "segment": additional_metadata["segment"].values[0],
            "exchange": additional_metadata["exchange"].values[0],
        }
    )
    return metadata


if __name__ == "__main__":
    tradingsymbols = [
        "BANKNIFTY23O1844500PE",
        "NIFTY23OCT19500CE",
        "FINNIFTY23OCTFUT",
        "USDINR23O20FUT",
        "USDINR23OCTFUT",
        "USDINR23O2088.25CE",
        "EURINR23DEC83.25PE",
        "RELIANCE23OCTFUT",
        "RELIANCE23OCT2360CE",
    ]
    for tradingsymbol in tradingsymbols:
        print(extract_metadata_from_tradingsymbol(tradingsymbol))
