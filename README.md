# FDL-002: Real Return Analysis of Iranian Fixed-Income Securities

A Python project that measures how much of the nominal yield on Iranian government securities (Akhza and Gam) survives inflation. It builds a nominal Nelson-Siegel yield curve from market data, converts it into a **real** yield curve using the consumer price inflation rate, and shows whether the market has preserved the purchasing power of invested capital.

This project extends [FDL-001: Estimation of the Risk-Free Rate from Iranian Fixed-Income Market Data](#related-project).

**Author:** Khashayar Jamshidi, Financial Data Analyst

---

## Overview

A nominal yield alone does not tell an investor whether their money is really growing. This project answers that question in five steps:

1. Downloads the yield table of government securities from the Iran Fara Bourse (IFB) website.
2. Cleans the data and fits a Nelson-Siegel yield curve (nominal curve).
3. Reads the latest consumer price inflation (CPI) rate from the Statistical Center of Iran portal (amar.org.ir).
4. Converts nominal yields into real yields for each instrument and for each tenor (90 days, 1 year, 2 years, longest available).
5. Reports whether each instrument and tenor preserves purchasing power, and exports everything to Excel and a chart.

## Method

Real return is computed with the Fisher relation:

```
r_real = (1 + r_nominal) / (1 + π) - 1
```

- `r_nominal`: nominal yield (YTM by default) from the Nelson-Siegel curve or from each instrument
- `π`: consumer price inflation rate
- `r_real`: real yield. A negative value means the nominal return did not keep up with inflation.

The program also reports the simple difference `r_nominal - π` in percentage points for comparison.

## Features

- Scrapes the IFB yield table (`requests` + `pandas.read_html`)
- Reads the CPI inflation card from amar.org.ir by element id, handling Persian digits, `/` as the decimal separator and the `%` sign
- SSL handling for the statistics site: system certificate store via `truststore`, optional fallback, and a manual inflation input as a last resort
- Sanity check on the inflation value (rejects values outside 0 to 500%)
- Jalali to Gregorian date conversion with `jdatetime`
- Data cleaning: matured instruments, stale prices, near-maturity instruments, outliers
- Nelson-Siegel curve fitting (grid search over lambda, linear least squares for the betas) with RMSE and R-squared
- Linear interpolation curve for comparison
- Real yield per instrument and per tenor, with a "Purchasing Power Preserved" flag
- Excel report with multiple sheets and an embedded chart (nominal vs. real curves)
- Automatic fallback file name if the Excel file is open or locked

## Requirements

- Python 3.9 or newer
- Packages: `requests`, `pandas`, `numpy`, `jdatetime`, `matplotlib`, `openpyxl`, `lxml`, `pillow`, `beautifulsoup4`
- Optional but recommended: `truststore` (fixes `unable to get local issuer certificate` on Windows)

```bash
python -m pip install requests pandas numpy jdatetime matplotlib openpyxl lxml pillow beautifulsoup4 truststore
```

## Usage

```bash
python real_return_rate.py
```

The script prints the inflation value, the cleaning summary, the Nelson-Siegel parameters and the real-rate table. It then saves `Output_Real_Return.xlsx` and `Real_Yield_Curve.png`, and opens the chart.

## Configuration

All settings are at the top of the script:

| Setting | Default | Description |
|---|---|---|
| `URL` | `https://www.ifb.ir/ytm.aspx` | Yield data source |
| `INFLATION_URL` | `https://amar.org.ir/` | Inflation data source |
| `INFLATION_ID` | `"statistics1"` | HTML id of the card that holds the CPI inflation rate |
| `INFLATION_MANUAL` | `None` | Inflation in percent (e.g. `73.6`), used only if reading the site fails |
| `ALLOW_INSECURE_FALLBACK` | `True` | Retry the statistics site without certificate verification on an SSL error (inflation page only). Set `False` if `truststore` works for you |
| `INCLUDE_WORDS` | `["اخزا", "گام"]` | Instrument families to keep |
| `EXCLUDE_WORD` | `"سررسید شده"` | Status text used to exclude matured instruments |
| `MAX_STALE_DAYS` | `5` | Maximum age (days) of the last trade |
| `MIN_DAYS` | `7` | Minimum days to maturity |
| `MIN_YTM` | `20` | Outlier filter (percent) |
| `RATE_COL` | `"YTM"` | `"YTM"` or `"Simple Yield"` |
| `TARGETS` | 90d, 1y, 2y | Tenors (in days) to estimate |
| `LAMBDA_GRID` | `0.05` to `10` | Nelson-Siegel decay parameter search range (years) |
| `EXTRAPOLATE` | `False` | If `False`, tenors outside the data range return `NaN` |
| `OUTPUT_FILE` | `"Output_Real_Return.xlsx"` | Excel output name |
| `CHART_FILE` | `"Real_Yield_Curve.png"` | Chart output name |

## Output

`Output_Real_Return.xlsx` contains:

| Sheet | Content |
|---|---|
| `Clean Data` | Filtered instruments with Nelson-Siegel fit, residuals, real yield and the purchasing-power flag |
| `Linear Curve` | Average yield per maturity (linear reference) |
| `NS Parameters` | `beta0`, `beta1`, `beta2`, `lambda`, RMSE, R-squared, number of points |
| `NS Curve` | Nominal and real Nelson-Siegel curves on a daily grid |
| `Real Returns` | By tenor: nominal rate, inflation, real rate, nominal minus inflation, purchasing-power flag |
| `Inflation` | Series name, raw text, parsed value, reporting period, source and fetch time |
| `Chart` | Nominal vs. real yield curve |

`Real_Yield_Curve.png` is the same chart as a standalone image.


## How to read the results

- A **negative real rate** means the nominal yield is lower than inflation: the investor lost purchasing power even though the nominal return was positive.
- A **positive real rate** means purchasing power was preserved and increased.
- Compare tenors: if the real rate is more negative at short maturities than at long ones (or the reverse), the curve is telling you how the market prices inflation expectations across horizons.

## Limitations

- **Inflation type matters.** The formula assumes an annual inflation rate to match the annualized yield. If the figure on the site is monthly, point-to-point, or an average over a different window, convert it or choose it deliberately. Check the `Inflation` sheet to see exactly what was read.
- **The real rate is backward-looking (ex-post).** It compares a forward-looking yield with realized inflation. For a forward-looking view, use expected inflation through `INFLATION_MANUAL` and test several scenarios.
- **One inflation rate is applied to all tenors.** No inflation term structure is modeled.
- Reading the inflation figure depends on the page layout of amar.org.ir. If the site changes or loads the card with JavaScript, the program falls back to `INFLATION_MANUAL`.
- The IFB page layout may also change; check the table index (`tables[1]`), column positions and header names if the script fails.
- Mixing Akhza and Gam can reduce the fit quality. If `lambda` hits the edge of the grid or R-squared is low, try Akhza only.
- Nelson-Siegel extrapolation outside the observed maturity range is unreliable, so it is off by default.
- Trading volume is not used, so illiquid instruments are not down-weighted.

## Related project

FDL-001 estimates the nominal risk-free rate from the same market data. FDL-002 reuses its pipeline and adds the inflation adjustment.

## Disclaimer

This project is for educational and analytical purposes. It is not investment advice and does not replace official reference rates or official inflation statistics.

## License

Add a license of your choice (for example MIT) as a `LICENSE` file.