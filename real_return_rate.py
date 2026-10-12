#====================================================================================
# Project: FDL-002                                                                  ||
# Title: Real Return Analysis of Iranian Fixed-Income Securities                    ||
# Author: Khashayar Jamshidi                                                        ||
# Role: Financial Data Analyst                                                      ||
#====================================================================================
import re
import requests                 # for receiving a web page
import urllib3
import pandas as pd             # for data manipulation
import numpy as np
import jdatetime
from io import StringIO         # for reading the data into a pandas dataframe
from datetime import datetime
from bs4 import BeautifulSoup   # for parsing the inflation box
import matplotlib.pyplot as plt
from openpyxl.drawing.image import Image

# Use the operating system certificate store (fixes "unable to get local issuer certificate").
# Optional: install with  python -m pip install truststore
try:
    import truststore
    truststore.inject_into_ssl()
    print("truststore enabled: using the Windows certificate store.")
except ImportError:
    print("truststore is not installed (optional). Install it with: python -m pip install truststore")

# ------------------------------- Settings ----------------------------------------
URL            = "https://www.ifb.ir/ytm.aspx"
INFLATION_URL  = "https://amar.org.ir/"
INFLATION_ID   = "statistics1"       # id of the <div> that holds the CPI inflation card
INFLATION_MANUAL = None              # fallback in % (e.g. 73.6). Used only if scraping fails
ALLOW_INSECURE_FALLBACK = True       # if SSL verification fails for the statistics site, retry WITHOUT verification
                                     # (used only for reading the inflation number; set False to disable)
INCLUDE_WORDS  = ["اخزا", "گام"]     # instruments to keep (use ["اخزا"] only for a stricter benchmark)
EXCLUDE_WORD   = "سررسید شده"        # matured instruments
MAX_STALE_DAYS = 5                   # max days since last trade (older prices are dropped)
MIN_DAYS       = 7                   # drop instruments that are too close to maturity
MIN_YTM        = 20                  # outlier filter (e.g. the 5.89% row)
RATE_COL       = "YTM"               # "YTM" or "Simple Yield" (use the same one for all tenors)
TARGETS        = {"Short (90d)": 90, "Medium (1y)": 365, "Medium (2y)": 730}
LAMBDA_GRID    = np.arange(0.05, 10.0, 0.01)   # Nelson-Siegel decay parameter search range (years)
EXTRAPOLATE    = False               # if False, rates outside the data range are NaN
OUTPUT_FILE    = "Output_Real_Return.xlsx"
CHART_FILE     = "Real_Yield_Curve.png"

headers = {"User-Agent": "Mozilla/5.0"}

# ------------------------------- Helpers -----------------------------------------
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

def to_num(s):
    """'40/05%' -> 40.05"""
    return pd.to_numeric(
        s.astype(str).str.replace("%", "", regex=False)
                     .str.replace("/", ".", regex=False).str.strip(),
        errors="coerce")

def parse_percent(text):
    """'%73/6' or '٪۷۳/۶' -> 73.6"""
    t = str(text).translate(_DIGITS).replace("/", ".").replace("٫", ".")
    m = re.search(r"\d+(?:\.\d+)?", t)
    if not m:
        raise ValueError(f"Cannot parse a number from: {text!r}")
    return float(m.group())

def jalali_to_date(s):
    """'1405-07-18' -> gregorian date; invalid day is moved back to the last valid day of the month."""
    try:
        y, m, d = map(int, str(s).strip().split("-"))
    except ValueError:
        return pd.NaT
    while d > 0:
        try:
            return jdatetime.date(y, m, d).togregorian()
        except ValueError:
            d -= 1
    return pd.NaT

def get_statistics_page(url):
    """GET the statistics site. On an SSL error, optionally retry without certificate verification."""
    try:
        return requests.get(url, headers=headers, timeout=30)
    except requests.exceptions.SSLError as e:
        if not ALLOW_INSECURE_FALLBACK:
            raise
        print(f"SSL verification failed ({type(e).__name__}). "
              f"Retrying WITHOUT certificate verification (inflation page only).")
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        return requests.get(url, headers=headers, timeout=30, verify=False)

def fetch_inflation():
    """Read CPI inflation (%) from the card with id=INFLATION_ID on amar.org.ir."""
    r = get_statistics_page(INFLATION_URL)
    r.raise_for_status()
    r.encoding = r.apparent_encoding
    soup = BeautifulSoup(r.text, "lxml")
    box = soup.find(id=INFLATION_ID)
    if box is None:
        raise ValueError(f"Element with id='{INFLATION_ID}' not found (page layout may have changed "
                         f"or the card is loaded by JavaScript).")
    title = box.find("p", class_="card-title").get_text(strip=True)
    value = box.find("p", class_="card-text").get_text(strip=True)
    period = box.find("p", class_="card-desc").get_text(strip=True)
    if "تورم" not in title:
        raise ValueError(f"The card title is not an inflation title: {title!r}")
    number = parse_percent(value)
    if not (0 <= number <= 500):
        raise ValueError(f"Inflation value looks wrong: {number}")
    return {"title": title, "raw": value, "value": number, "period": period}

def real_rate(nominal_pct, inflation_pct):
    """r_real = (1 + r_nominal) / (1 + pi) - 1, inputs and output in %."""
    return ((1 + np.asarray(nominal_pct, dtype=float) / 100) / (1 + inflation_pct / 100) - 1) * 100

def ns_design(t, lam):
    """Nelson-Siegel design matrix. t in years, lam in years."""
    x = t / lam
    f1 = (1 - np.exp(-x)) / x
    f2 = f1 - np.exp(-x)
    return np.column_stack([np.ones_like(t), f1, f2])

def fit_nelson_siegel(t, y, lam_grid):
    """For each lambda solve beta0, beta1, beta2 by least squares; keep the best lambda."""
    best = None
    for lam in lam_grid:
        X = ns_design(t, lam)
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        sse = float(((y - X @ beta) ** 2).sum())
        if best is None or sse < best["sse"]:
            best = {"beta0": beta[0], "beta1": beta[1], "beta2": beta[2], "lambda": lam, "sse": sse}
    return best

def ns_rate(days, p):
    """Nelson-Siegel rate (%) for days to maturity."""
    t = np.atleast_1d(np.asarray(days, dtype=float)) / 365.0
    X = ns_design(t, p["lambda"])
    return X @ np.array([p["beta0"], p["beta1"], p["beta2"]])

def save_excel(path, sheets, image_path=None):
    """Save sheets; if the file is open/locked, save with a timestamped name instead."""
    try:
        writer = pd.ExcelWriter(path, engine="openpyxl")
    except PermissionError:
        new_path = f"Output_Real_Return_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
        print(f"{path} is open or locked. Saving as {new_path} instead.")
        path = new_path
        writer = pd.ExcelWriter(path, engine="openpyxl")
    with writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)
        if image_path:
            ws = writer.book.create_sheet("Chart")
            ws.add_image(Image(image_path), "A1")
    print(f"{path} saved.")

# ------------------------------- Inflation ---------------------------------------
try:
    infl = fetch_inflation()
    INFLATION = infl["value"]
    print(f"CPI inflation: {infl['raw']} -> {INFLATION}% | period: {infl['period']}")
except Exception as e:
    print(f"Could not read inflation from {INFLATION_URL}: {e}")
    if INFLATION_MANUAL is None:
        raise SystemExit("Set INFLATION_MANUAL in the settings (e.g. 73.6) and run again.")
    INFLATION = float(INFLATION_MANUAL)
    infl = {"title": "Manual input", "raw": str(INFLATION), "value": INFLATION, "period": "manual"}
    print(f"Using manual inflation value: {INFLATION}%")

# ------------------------------- Download ----------------------------------------
response = requests.get(URL, headers=headers, timeout=30)   # send a GET request to the URL
response.raise_for_status()
response.encoding = response.apparent_encoding               # fix Persian text encoding

tables = pd.read_html(StringIO(response.text))               # list of dataframes
print(f"Number of tables found: {len(tables)}")
df = tables[1]

# ------------------------------- Filter rows -------------------------------------
col  = df.iloc[:, 1].astype(str)     # name column
col2 = df.iloc[:, 5].astype(str)     # status column
pattern = "|".join(INCLUDE_WORDS)
filtered = df[col.str.contains(pattern) & ~col2.str.contains(EXCLUDE_WORD)].copy()

# ------------------------------- Rename headers ----------------------------------
filtered.columns = [
    str(c).strip()
    .replace("ردیف", "index")
    .replace("نماد", "Name")
    .replace("قیمت معامله شده هر ورقه", "Last Price")
    .replace("تاریخ آخرین روز معاملاتی", "Date")
    .replace("تاریخ سررسید", "Maturity")
    .replace("بازده ساده", "Simple Yield")
    for c in filtered.columns
]
print(list(filtered.columns))        # check the final header names

required = ["Name", "Date", "Maturity", "YTM", "Simple Yield"]
missing = [c for c in required if c not in filtered.columns]
if missing:
    raise KeyError(f"Columns not found: {missing}. Rename them in the header step above.")

# ------------------------------- Clean data --------------------------------------
for c in ["YTM", "Simple Yield", "Last Price"]:
    if c in filtered.columns:
        filtered[c] = to_num(filtered[c])

filtered["trade_date"]    = pd.to_datetime(filtered["Date"].apply(jalali_to_date))
filtered["maturity_date"] = pd.to_datetime(filtered["Maturity"].apply(jalali_to_date))
filtered = filtered.dropna(subset=["trade_date", "maturity_date", RATE_COL])

base = filtered["trade_date"].max()                          # latest trading day in the data
filtered["days_to_maturity"] = (filtered["maturity_date"] - base).dt.days
filtered["days_since_trade"] = (base - filtered["trade_date"]).dt.days

clean = filtered[
    (filtered["days_since_trade"] <= MAX_STALE_DAYS) &
    (filtered["days_to_maturity"] >= MIN_DAYS) &
    (filtered[RATE_COL] > MIN_YTM)
].copy()

print(f"Base date: {base.date()} | rows before: {len(filtered)} | rows after cleaning: {len(clean)}")
if len(clean) < 4:
    raise ValueError("Not enough clean data points to fit Nelson-Siegel (need at least 4).")

# ------------------------------- Linear curve (for comparison) -------------------
curve = (clean.groupby("days_to_maturity", as_index=False)[RATE_COL].mean()
              .sort_values("days_to_maturity"))

d_min = int(clean["days_to_maturity"].min())
d_max = int(clean["days_to_maturity"].max())

def linear_rate_at(target_days):
    if target_days < d_min or target_days > d_max:
        return np.nan
    return float(np.interp(target_days, curve["days_to_maturity"], curve[RATE_COL]))

# ------------------------------- Nelson-Siegel fit -------------------------------
t_obs = clean["days_to_maturity"].to_numpy(dtype=float) / 365.0
y_obs = clean[RATE_COL].to_numpy(dtype=float)

params = fit_nelson_siegel(t_obs, y_obs, LAMBDA_GRID)
y_fit = ns_rate(clean["days_to_maturity"].to_numpy(), params)
sst   = float(((y_obs - y_obs.mean()) ** 2).sum())
rmse  = float(np.sqrt(params["sse"] / len(y_obs)))
r2    = 1 - params["sse"] / sst if sst > 0 else np.nan

clean["NS Fitted"]   = np.round(y_fit, 4)
clean["NS Residual"] = np.round(clean[RATE_COL] - clean["NS Fitted"], 4)

ns_params = pd.DataFrame({
    "Parameter": ["beta0 (long-term level)", "beta1 (slope)", "beta2 (curvature)",
                  "lambda (years)", "RMSE (% points)", "R-squared", "N points"],
    "Value": [params["beta0"], params["beta1"], params["beta2"],
              params["lambda"], rmse, r2, len(y_obs)],
})
print(ns_params.to_string(index=False))

print(f"Implied short-end rate (tau->0): {params['beta0'] + params['beta1']:.2f}% | "
      f"long-end rate (tau->inf): {params['beta0']:.2f}%")

# ------------------------------- Real returns: instruments -----------------------
real_col = f"Real {RATE_COL} %"
clean["Inflation %"] = INFLATION
clean[real_col] = np.round(real_rate(clean[RATE_COL], INFLATION), 4)
clean["Purchasing Power Preserved"] = np.where(clean[real_col] >= 0, "Yes", "No")

n_pos = int((clean[real_col] >= 0).sum())
print(f"\nInstruments with non-negative real return: {n_pos} of {len(clean)}")
print(f"Real {RATE_COL} range: {clean[real_col].min():.2f}% to {clean[real_col].max():.2f}% | "
      f"mean: {clean[real_col].mean():.2f}%")

# ------------------------------- Real returns: tenors ----------------------------
targets = dict(TARGETS)
targets["Long (longest available)"] = d_max

def ns_rate_at(target_days):
    if not EXTRAPOLATE and (target_days < d_min or target_days > d_max):
        return np.nan
    return float(ns_rate(target_days, params)[0])

nom_ns  = np.array([ns_rate_at(d) for d in targets.values()])
nom_lin = np.array([linear_rate_at(d) for d in targets.values()])
real_ns = real_rate(nom_ns, INFLATION)

rates = pd.DataFrame({
    "Tenor": list(targets.keys()),
    "Days": list(targets.values()),
    f"Nominal NS ({RATE_COL}) %":     np.round(nom_ns, 2),
    f"Nominal Linear ({RATE_COL}) %": np.round(nom_lin, 2),
    "Inflation (CPI) %":              INFLATION,
    "Real Rate NS %":                 np.round(real_ns, 2),
    "Real Rate Linear %":             np.round(real_rate(nom_lin, INFLATION), 2),
    "Nominal - Inflation (pp)":       np.round(nom_ns - INFLATION, 2),
})
rates["Purchasing Power Preserved"] = np.where(
    rates["Real Rate NS %"].isna(), "n/a",
    np.where(rates["Real Rate NS %"] >= 0, "Yes", "No"))
print("\n", rates.to_string(index=False))

inflation_info = pd.DataFrame({
    "Item": ["Series", "Raw text", "Value (%)", "Period", "Source", "Fetched at"],
    "Value": [infl["title"], infl["raw"], INFLATION, infl["period"], INFLATION_URL,
              f"{datetime.now():%Y-%m-%d %H:%M}"],
})

# fitted curves on a daily grid (for Excel and plot)
grid_days = np.arange(max(d_min, 1), d_max + 1)
ns_nominal = ns_rate(grid_days, params)
ns_curve = pd.DataFrame({
    "days_to_maturity": grid_days,
    f"NS Nominal {RATE_COL} %": np.round(ns_nominal, 4),
    "Inflation %": INFLATION,
    "NS Real %": np.round(real_rate(ns_nominal, INFLATION), 4),
})

# ------------------------------- Plot --------------------------------------------
fig, ax = plt.subplots(figsize=(11, 6.5))

# 1) cleaned instruments: nominal (filled) and real (hollow)
for word, color, marker, label in [("اخزا", "tab:blue", "o", "Akhza"),
                                   ("گام", "tab:orange", "s", "Gam")]:
    sub = clean[clean["Name"].astype(str).str.contains(word)]
    if not sub.empty:
        ax.scatter(sub["days_to_maturity"], sub[RATE_COL],
                   color=color, marker=marker, alpha=0.6, s=40, label=f"{label} nominal")
        ax.scatter(sub["days_to_maturity"], sub[real_col],
                   facecolors="none", edgecolors=color, marker=marker, alpha=0.8, s=40,
                   label=f"{label} real")

# 2) Nelson-Siegel curves
ax.plot(ns_curve["days_to_maturity"], ns_curve[f"NS Nominal {RATE_COL} %"],
        color="black", linewidth=2.2, label="Nominal NS curve")
ax.plot(ns_curve["days_to_maturity"], ns_curve["NS Real %"],
        color="red", linewidth=2.2, label="Real NS curve")

# 3) inflation and zero lines
ax.axhline(INFLATION, color="green", linestyle="--", linewidth=1.4,
           label=f"CPI inflation ({INFLATION:.1f}%)")
ax.axhline(0, color="gray", linestyle="-", linewidth=1.0)
ax.axhspan(ax.get_ylim()[0], 0, color="red", alpha=0.05)

# 4) real rate at target tenors
valid = rates.dropna(subset=["Real Rate NS %"])
ax.scatter(valid["Days"], valid["Real Rate NS %"], color="red", zorder=5, s=90,
           edgecolors="black", label="Target tenors (real)")
for _, r in valid.iterrows():
    ax.annotate(f"{r['Real Rate NS %']:.2f}%", (r["Days"], r["Real Rate NS %"]),
                textcoords="offset points", xytext=(0, 10),
                ha="center", fontsize=9, color="red")

ax.set_title(f"Nominal vs Real Yield Curve - Iran Government Securities ({RATE_COL}) - {base.date()}\n"
             f"CPI inflation = {INFLATION:.1f}% | lambda={params['lambda']:.2f}y | "
             f"RMSE={rmse:.2f} | R²={r2:.3f}")
ax.set_xlabel("Days to Maturity")
ax.set_ylabel("Rate (%)")
ax.grid(True, linestyle="--", alpha=0.5)
ax.legend(fontsize=8, ncol=2)
plt.tight_layout()
plt.savefig(CHART_FILE, dpi=150)

# ------------------------------- Save --------------------------------------------
save_excel(
    OUTPUT_FILE,
    {
        "Clean Data": clean,
        "Linear Curve": curve,
        "NS Parameters": ns_params,
        "NS Curve": ns_curve,
        "Real Returns": rates,
        "Inflation": inflation_info,
    },
    image_path=CHART_FILE,
)

plt.show()