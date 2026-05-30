SYMBOLS = {
    "NIFTY": {
        "name": "Nifty 50 Index",
        "nse_symbol": "NIFTY",
        "upstox_key": "NSE_INDEX|Nifty 50",
        "sector": "Index",
        "has_options": True,
        "related": ["VEDL", "CDSL"]
    },
    "VEDL": {
        "name": "Vedanta Ltd",
        "nse_symbol": "VEDL",
        "upstox_key": "NSE_EQ|INE205A01025",
        "sector": "Metals",
        "has_options": True,
        "related": ["SUZLON", "CDSL"]
    },
    "SUZLON": {
        "name": "Suzlon Energy Ltd",
        "nse_symbol": "SUZLON",
        "upstox_key": "NSE_EQ|INE040H01021",
        "sector": "Renewable Energy",
        "has_options": True,
        "related": ["INOXWIND", "KPIGREEN"]
    },
    "CDSL": {
        "name": "CDSL (Central Depository)",
        "nse_symbol": "CDSL",
        "upstox_key": "NSE_EQ|INE736A01011",
        "sector": "Financial Services",
        "has_options": True,
        "related": ["VEDL", "SUZLON"]
    },
    "KITEX": {
        "name": "Kitex Garments Ltd",
        "nse_symbol": "KITEX",
        "upstox_key": "NSE_EQ|INE602G01020",
        "sector": "Textiles",
        "has_options": False,
        "related": []
    },
}
