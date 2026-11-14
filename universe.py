"""Stocks the market board scans.

Default ("all"): the S&P 500 + the Nasdaq-100 + major ETFs, so there's something setting up every day.
Choose with BOARD_UNIVERSE: all | sp500 | ndx | etf, or a comma list like AAPL,MSFT,...
Nasdaq-100 per Nasdaq's Aug 2026 list (Oct 9, 2026: MRNA replaced WBD); S&P 500 as of Oct 28, 2026.
The indexes change a few times a year, so refresh these lists now and then."""
import os

NASDAQ_100 = """
ADBE AMD ABNB ALNY GOOGL GOOG AMZN AEP AMGN ADI AAPL AMAT APP ARM ASML ALAB ADSK ADP AXON BKR
BKNG AVGO CDNS CTAS CSCO CCEP CMCSA CEG CPRT CRWV COST CRWD CSX DDOG DXCM FANG DASH EA EXC FAST
FER FTNT GEHC GILD HONA HON IDXX INTC INTU ISRG KDP KLAC KHC LRCX LIN LITE MAR MRVL MELI META
MCHP MU MSFT MDLZ MPWR MNST NBIS NFLX NVDA NXPI ODFL ORLY PCAR PLTR PANW PAYX PYPL PDD PEP QCOM
REGN RKLB ROP ROST SNDK STX SHOP SPCX SBUX MSTR SNPS TTWO TER TSLA TXN TRI TMUS VRTX WMT WDC
WDAY MRNA XEL
""".split()


SP_500 = """
A AAPL ABBV ABNB ABT ACGL ACN ADBE ADI ADM ADP ADSK AEE AEP AES AFL AIG AIZ AJG AKAM ALB ALGN ALL ALLE AMAT
AMCR AMD AME AMGN AMP AMT AMZN ANET AON AOS APA APD APH APO APP APTV ARE ARES ATO AVGO AVY AWK AXON AXP AZO BA
BAC BALL BAX BBY BDX BE BEN BF.B BG BIIB BKNG BKR BLK BMY BNY BR BRK.B BRO BSX BX BXP C CAH CARR CASY CAT CB
CBOE CBRE CCI CCL CDNS CDW CEG CF CFG CHD CHRW CHTR CI CIEN CINF CL CLX CMCSA CME CMG CMI CMS CNC CNP COF COHR
COIN COO COP COR COST CPAY CPRT CPT CRH CRL CRM CRWD CSCO CSGP CSX CTAS CTSH CVNA CVS CVX D DAL DASH DD DDOG
DE DECK DELL DG DGX DHI DHR DIS DLR DLTR DOC DOV DOW DPZ DRI DTE DUK DVA DVN DXCM EBAY ECHO ECL ED EFX EG EIX
EL ELV EME EMR EOG EQIX EQT ERIE ES ESS ETN ETR EVRG EW EXC EXE EXPD EXPE EXR F FANG FAST FCX FDS FDX FDXF FE
FERG FFIV FICO FIS FISV FITB FIX FLEX FOX FOXA FRT FSLR FTNT FTV GD GDDY GE GEHC GEN GEV GILD GIS GL GLW GM
GNRC GOOG GOOGL GPC GPN GRMN GS GWW HAL HAS HBAN HCA HD HIG HII HLT HON HONA HOOD HPE HPQ HRL HSIC HST HSY
HUBB HUM HWM IBKR IBM ICE IDXX IEX IFF ILMN INCY INTC INTU INVH IP IQV IR IRM ISRG IT ITW IVZ J JBHT JBL JCI
JKHY JNJ JPM KDP KEY KEYS KHC KIM KKR KLAC KMB KMI KO KR KVUE L LDOS LEN LH LHX LII LIN LITE LLY LMT LNT LOW
LRCX LULU LUV LVS LYB LYV MA MAA MAR MAS MCD MCHP MCK MCO MDLZ MDT MET META MGM MKC MLM MMM MNST MO MOS MPC
MPWR MRK MRNA MRSH MRVL MS MSCI MSFT MSI MTB MTD MU NCLH NDAQ NDSN NEE NEM NFLX NI NKE NOC NOW NRG NSC NTAP
NTRS NUE NVDA NVR NWS NWSA NXPI O ODFL OKE OMC ON ORCL ORLY OTIS OXY P PANW PAYX PCAR PCG PEG PEP PFE PFG PG
PGR PH PHM PKG PLD PLTR PM PNC PNR PNW PODD PPG PPL PRU PSA PSX PTC PWR PYPL Q QCOM RCL RDDT REG REGN RF RJF
RL RMD ROK ROL ROP ROST RSG RTX RVTY SBAC SBUX SCHW SHW SJM SKYD SLB SMCI SNA SNDK SNPS SO SOLV SPG SPGI SRE
STE STLD STT STX STZ SW SWK SWKS SYF SYK SYY T TDG TDY TECH TEL TER TFC TGT TJX TKO TMO TMUS TPL TPR TRGP TRMB
TROW TRV TSCO TSLA TSN TT TTWO TWLO TXN TXT TYL UAL UBER UDR UHS ULTA UNH UNP UPS URI USB V VEEV VICI VLO VLTO
VMC VMRK VRSK VRSN VRT VRTX VST VTR VTRS VYLR VZ WAB WAT WDAY WDC WEC WELL WFC WM WMB WMT WRB WSM WST WTW WY
WYNN XEL XOM XYL XYZ YUM ZBH ZBRA ZTS
""".split()

# broad market, sectors and a few popular macro ETFs
ETFS = """
SPY QQQ IWM DIA XLK XLF XLE XLV XLY XLP XLI XLB XLU XLRE XLC SMH SOXX XBI KRE ARKK TLT GLD SLV USO
""".split()

UNIVERSES = {"ndx": NASDAQ_100, "sp500": SP_500, "etf": ETFS}


def board_universe() -> list[str]:
    choice = os.getenv("BOARD_UNIVERSE", "all").strip()
    if choice.lower() in UNIVERSES:
        return sorted(set(UNIVERSES[choice.lower()]))
    if choice and choice.lower() != "all":
        return sorted({s.strip().upper() for s in choice.split(",") if s.strip()})
    return sorted(set(NASDAQ_100) | set(SP_500) | set(ETFS))


def describe() -> str:
    """Plain-English name of what the board covers."""
    choice = os.getenv("BOARD_UNIVERSE", "").strip().lower() or "all"
    return {"all": "S&P 500 and Nasdaq-100 stocks plus major ETFs", "ndx": "Nasdaq-100 stocks", "sp500": "S&P 500 stocks",
            "etf": "major ETFs"}.get(choice, "stocks on the board")


import json as _json
import os as _os


def _load_caps() -> dict:
    try:
        with open(_os.path.join(_os.path.dirname(__file__), "market_caps.json")) as f:
            return {k.upper(): float(v) for k, v in _json.load(f)["caps"].items()}
    except Exception:
        return {}          # no snapshot: fail open, the cap filter never blocks


MARKET_CAPS = _load_caps()


def market_cap(symbol: str) -> float | None:
    """Market cap in dollars, or None when unknown (ETFs, or a name missing from the
    snapshot): None means fail open, never block on missing data."""
    if symbol.upper() in ETFS:
        return None        # ETFs have no market cap; the volume filter still applies
    return MARKET_CAPS.get(symbol.upper())


def _load_sectors() -> dict:
    try:
        with open(_os.path.join(_os.path.dirname(__file__), "sectors.json")) as f:
            return {k.upper(): v for k, v in _json.load(f)["sectors"].items()}
    except Exception:
        return {}


SECTORS = _load_sectors()


def sector(symbol: str) -> str:
    """GICS sector for the market heat map; ETFs are their own group; unknown names go to 'Other'."""
    s = symbol.upper()
    if s in ETFS:
        return "ETFs"
    return SECTORS.get(s, "Other")
