"""Stocks the Hub Board scans. Default: the Nasdaq-100 (per Nasdaq's Aug 2026 component list,
updated for the Oct 9, 2026 change: MRNA replaced WBD). Override with BOARD_UNIVERSE=AAPL,MSFT,...
The index changes a few times a year, so check this list now and then."""
import os

NASDAQ_100 = """
ADBE AMD ABNB ALNY GOOGL GOOG AMZN AEP AMGN ADI AAPL AMAT APP ARM ASML ALAB ADSK ADP AXON BKR
BKNG AVGO CDNS CTAS CSCO CCEP CMCSA CEG CPRT CRWV COST CRWD CSX DDOG DXCM FANG DASH EA EXC FAST
FER FTNT GEHC GILD HONA HON IDXX INTC INTU ISRG KDP KLAC KHC LRCX LIN LITE MAR MRVL MELI META
MCHP MU MSFT MDLZ MPWR MNST NBIS NFLX NVDA NXPI ODFL ORLY PCAR PLTR PANW PAYX PYPL PDD PEP QCOM
REGN RKLB ROP ROST SNDK STX SHOP SPCX SBUX MSTR SNPS TTWO TER TSLA TXN TRI TMUS VRTX WMT WDC
WDAY MRNA XEL
""".split()


def board_universe() -> list[str]:
    custom = os.getenv("BOARD_UNIVERSE", "").strip()
    if custom:
        return sorted({s.strip().upper() for s in custom.split(",") if s.strip()})
    return sorted(set(NASDAQ_100))
