from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 데이터 폴더 = 프로젝트 루트 (코드·데이터를 한 폴더에서 관리).
# 위치: C:\Users\ksw96\Desktop\승환 자산 관리\Claude
DATA_DIR = ROOT
SHINHAN_DIR = DATA_DIR / "shinhan"  # 레거시 (현재 미사용)
PORTFOLIO_XLSX = DATA_DIR / "portfolio.xlsx"
HISTORY_DIR = DATA_DIR / "history"

# RAW 파일 폴더 (사용자가 신한·업비트에서 추출하여 떨어뜨리는 폴더)
# 폴더 안의 최신 .xlsx 자동 사용. 파일명에 타임스탬프(`_YYYYMMDD_HHMM`) 권장.
RAW_DIR = Path(r"C:\Users\ksw96\Desktop\승환 자산 관리\RAW DATA")

USD_KRW_TICKER = "KRW=X"
UPBIT_TICKER_URL = "https://api.upbit.com/v1/ticker"
PRICE_CACHE_TTL = 600     # 종목 시세 10분
FX_CACHE_TTL = 60         # 환율 1분 (실시간감)

ASSET_STOCK = "해외주식"
ASSET_COIN = "암호화폐"
ASSET_CASH = "현금"
