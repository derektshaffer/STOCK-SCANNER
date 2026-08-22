Run python stock_scanner.py
Momentum scan v2 started: 2026-08-22 12:00:54 EDT
Mode: OFF-HOURS / TEST
Alpaca returned 50 gainers.

TOP WATCHLIST / NEAR-MISS CANDIDATES
-----------------------------------------------------------------------------------------------------------------------------
SYM      SCORE  FAILS    PRICE     DAY%     5M%    15M%  VOLPACE   SPREAD%   FROMHI%   VWAP  FAILED FILTERS
-----------------------------------------------------------------------------------------------------------------------------
USDE      53.2      1     8.63   116.29       -       -        -     1.413      1.26    YES  dollar volume < $5M
CRMU      48.0      2     2.34    40.96       -       -        -         -      0.00    YES  dollar volume < $5M, spread > 2% or unavailable
USAX      48.0      2    13.60    26.58       -       -        -     7.568      0.00    YES  dollar volume < $5M, spread > 2% or unavailable
CRMX      48.0      2     5.48    24.83       -       -        -         -      0.00    YES  dollar volume < $5M, spread > 2% or unavailable
VOGX      48.0      2    28.68    21.63       -       -        -         -      0.00    YES  dollar volume < $5M, spread > 2% or unavailable
NCTY      47.6      2     5.66    30.11       -       -        -    31.671      0.53    YES  dollar volume < $5M, spread > 2% or unavailable
INFH      47.5      2     9.97    24.31       -       -        -    13.372      0.60    YES  dollar volume < $5M, spread > 2% or unavailable
PSIG      47.4      2     2.88    46.19       -       -        -         -      0.69    YES  dollar volume < $5M, spread > 2% or unavailable
UECG      47.2      2     5.39    29.26       -       -        -     9.237      0.37    YES  dollar volume < $5M, spread > 2% or unavailable
USGG      47.0      2     7.96    24.76       -       -        -     2.753      2.21    YES  dollar volume < $5M, spread > 2% or unavailable

FULL BASE-FILTER PASSES
No stocks passed every base filter on this scan.

HISTORICAL CONTINUATION - TOP CANDIDATES
Historical intraday continuation is skipped outside regular market hours; it will activate automatically during Monday's session.

SCAN SUMMARY
Analyzed common-stock candidates: 29
Passed every base filter: 0
Likely warrants/rights/units excluded: 21
Excluded symbols: RFAIU, USDEW, TMCWW, SAIHW, PDYNW, ENGNW, RFAIR, ARQQW, GRABW, CRACW, SPEGR, OXBRW, CSHRW, SATLW, MSAIW, KWMWW, HQWWW, BAERW, GENVR, NTRBW, SDAWW
Most common rejection reasons:
  - dollar volume < $5M: 29
  - spread > 2% or unavailable: 27
  - > 8% below high: 7
  - below VWAP: 6
  - price < $1: 5
  - day gain < 3%: 2
  - price > $50: 1
  - range < 3%: 1

JSON RESULTS - TOP WATCHLIST
[
  {
    "symbol": "USDE",
    "price": 8.63,
    "prev_close": 3.99,
    "day_pct": 116.29,
    "volume": 291829,
    "dollar_volume": 2518484.27,
    "spread_pct": 1.413,
    "intraday_range_pct": 92.72,
    "distance_from_high_pct": 1.26,
    "vwap": 6.9848,
    "distance_from_vwap_pct": 23.55,
    "above_vwap": true,
    "failed_filters": [
      "dollar volume < $5M"
    ],
    "failed_count": 1,
    "passed_base_filters": false,
    "base_score": 53.2,
    "live_bonus": 0.0,
    "score": 53.2,
    "live_data_status": "skipped_off_hours",
    "historical": {
      "status": "skipped_off_hours",
      "message": "Historical intraday comparison activates during regular market hours."
    }
  },
  {
    "symbol": "CRMU",
    "price": 2.34,
    "prev_close": 1.66,
    "day_pct": 40.96,
    "volume": 10731,
    "dollar_volume": 25110.54,
    "spread_pct": null,
    "intraday_range_pct": 18.18,
    "distance_from_high_pct": 0.0,
    "vwap": 2.1926,
    "distance_from_vwap_pct": 6.72,
    "above_vwap": true,
    "failed_filters": [
      "dollar volume < $5M",
      "spread > 2% or unavailable"
    ],
    "failed_count": 2,
    "passed_base_filters": false,
    "base_score": 48.0,
    "live_bonus": 0.0,
    "score": 48.0,
    "live_data_status": "skipped_off_hours"
  },
  {
    "symbol": "USAX",
    "price": 13.595,
    "prev_close": 10.74,
    "day_pct": 26.58,
    "volume": 1151,
    "dollar_volume": 15647.85,
    "spread_pct": 7.568,
    "intraday_range_pct": 19.46,
    "distance_from_high_pct": 0.0,
    "vwap": 12.31,
    "distance_from_vwap_pct": 10.44,
    "above_vwap": true,
    "failed_filters": [
      "dollar volume < $5M",
      "spread > 2% or unavailable"
    ],
    "failed_count": 2,
    "passed_base_filters": false,
    "base_score": 48.0,
    "live_bonus": 0.0,
    "score": 48.0,
    "live_data_status": "skipped_off_hours"
  },
  {
    "symbol": "CRMX",
    "price": 5.48,
    "prev_close": 4.39,
    "day_pct": 24.83,
    "volume": 861,
    "dollar_volume": 4718.28,
    "spread_pct": null,
    "intraday_range_pct": 23.15,
    "distance_from_high_pct": 0.0,
    "vwap": 5.15,
    "distance_from_vwap_pct": 6.41,
    "above_vwap": true,
    "failed_filters": [
      "dollar volume < $5M",
      "spread > 2% or unavailable"
    ],
    "failed_count": 2,
    "passed_base_filters": false,
    "base_score": 48.0,
    "live_bonus": 0.0,
    "score": 48.0,
    "live_data_status": "skipped_off_hours"
  },
  {
    "symbol": "VOGX",
    "price": 28.675,
    "prev_close": 23.575,
    "day_pct": 21.63,
    "volume": 4069,
    "dollar_volume": 116678.57,
    "spread_pct": null,
    "intraday_range_pct": 20.08,
    "distance_from_high_pct": 0.0,
    "vwap": 26.6141,
    "distance_from_vwap_pct": 7.74,
    "above_vwap": true,
    "failed_filters": [
      "dollar volume < $5M",
      "spread > 2% or unavailable"
    ],
    "failed_count": 2,
    "passed_base_filters": false,
    "base_score": 48.0,
    "live_bonus": 0.0,
    "score": 48.0,
    "live_data_status": "skipped_off_hours"
  },
  {
    "symbol": "NCTY",
    "price": 5.66,
    "prev_close": 4.35,
    "day_pct": 30.11,
    "volume": 9851,
    "dollar_volume": 55756.66,
    "spread_pct": 31.671,
    "intraday_range_pct": 25.88,
    "distance_from_high_pct": 0.53,
    "vwap": 5.1242,
    "distance_from_vwap_pct": 10.46,
    "above_vwap": true,
    "failed_filters": [
      "dollar volume < $5M",
      "spread > 2% or unavailable"
    ],
    "failed_count": 2,
    "passed_base_filters": false,
    "base_score": 47.6,
    "live_bonus": 0.0,
    "score": 47.6,
    "live_data_status": "skipped_off_hours"
  },
  {
    "symbol": "INFH",
    "price": 9.97,
    "prev_close": 8.02,
    "day_pct": 24.31,
    "volume": 2500,
    "dollar_volume": 24925.0,
    "spread_pct": 13.372,
    "intraday_range_pct": 19.4,
    "distance_from_high_pct": 0.6,
    "vwap": 9.6442,
    "distance_from_vwap_pct": 3.38,
    "above_vwap": true,
    "failed_filters": [
      "dollar volume < $5M",
      "spread > 2% or unavailable"
    ],
    "failed_count": 2,
    "passed_base_filters": false,
    "base_score": 47.5,
    "live_bonus": 0.0,
    "score": 47.5,
    "live_data_status": "skipped_off_hours"
  },
  {
    "symbol": "PSIG",
    "price": 2.88,
    "prev_close": 1.97,
    "day_pct": 46.19,
    "volume": 6448,
    "dollar_volume": 18570.24,
    "spread_pct": null,
    "intraday_range_pct": 26.91,
    "distance_from_high_pct": 0.69,
    "vwap": 2.611,
    "distance_from_vwap_pct": 10.3,
    "above_vwap": true,
    "failed_filters": [
      "dollar volume < $5M",
      "spread > 2% or unavailable"
    ],
    "failed_count": 2,
    "passed_base_filters": false,
    "base_score": 47.4,
    "live_bonus": 0.0,
    "score": 47.4,
    "live_data_status": "skipped_off_hours"
  },
  {
    "symbol": "UECG",
    "price": 5.39,
    "prev_close": 4.17,
    "day_pct": 29.26,
    "volume": 22595,
    "dollar_volume": 121787.05,
    "spread_pct": 9.237,
    "intraday_range_pct": 11.32,
    "distance_from_high_pct": 0.37,
    "vwap": 5.1155,
    "distance_from_vwap_pct": 5.37,
    "above_vwap": true,
    "failed_filters": [
      "dollar volume < $5M",
      "spread > 2% or unavailable"
    ],
    "failed_count": 2,
    "passed_base_filters": false,
    "base_score": 47.2,
    "live_bonus": 0.0,
    "score": 47.2
  },
  {
    "symbol": "USGG",
    "price": 7.96,
    "prev_close": 6.38,
    "day_pct": 24.76,
    "volume": 3698,
    "dollar_volume": 29436.08,
    "spread_pct": 2.753,
    "intraday_range_pct": 20.5,
    "distance_from_high_pct": 2.21,
    "vwap": 7.4037,
    "distance_from_vwap_pct": 7.51,
    "above_vwap": true,
    "failed_filters": [
      "dollar volume < $5M",
      "spread > 2% or unavailable"
    ],
    "failed_count": 2,
    "passed_base_filters": false,
    "base_score": 47.0,
    "live_bonus": 0.0,
    "score": 47.0
  }
]
