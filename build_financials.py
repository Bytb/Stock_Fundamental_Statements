#!/usr/bin/env python3
"""
Portable 10-year condensed financial workbook from SEC EDGAR (as-reported XBRL).

Spec:
- Input: US ticker (CIK-mapped)
- Source: edgar (edgartools) Python library (10-K, prefer 10-K/A)
- Up to 10 annual years (newest -> oldest)
- Sheets: Income Statement, Balance Sheet, Cash Flow
- Rows: condensed line items (+ derived: EBIT, EBITDA, Free Cash Flow, Gross Profit if needed)
- Columns: fiscal year-end dates (YYYY-MM-DD), newest -> oldest
- USD only; display "USD in millions", 2 decimals
- Negatives with parentheses; missing as em dash (—)
- Source traceability per column: filing date + accession (SEC link)
- Abort if <2 years usable
"""

from __future__ import annotations
import sys
import warnings
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
from decimal import Decimal

import pandas as pd
from dateutil.parser import parse as parse_date

# edgartools
from edgar import Company, MultiFinancials, set_identity

# Identify to SEC (please customize this)
set_identity("Your Name your.email@domain.com")

# ----------------------------- Condensed line sets -----------------------------

CONDENSED_INCOME = [
    "Revenue",
    "Cost of Goods Sold",
    "Gross Profit",
    "Research and Development",
    "Selling, General and Administrative",
    "Operating Income",
    "Non-Operating Income (Expense)",
    "Income Before Tax",
    "Income Tax Expense",
    "Net Income",
    "EBIT",
    "EBITDA",
    "Basic Shares Outstanding",
    "Diluted Shares Outstanding",
    "Basic EPS",
    "Diluted EPS",
]

CONDENSED_BALANCE = [
    "Cash and Cash Equivalents",
    "Short-term Investments",
    "Cash and Short-term Investments",
    "Accounts Receivable",
    "Inventory",
    "Total Current Assets",
    "Property, Plant & Equipment (Net)",
    "Goodwill and Intangibles",
    "Total Assets",
    "Accounts Payable",
    "Short-term Debt",
    "Total Current Liabilities",
    "Long-term Debt",
    "Total Liabilities",
    "Total Equity",
    "Total Liabilities and Equity",
]

CONDENSED_CASHFLOW = [
    "Net Cash from Operating Activities",
    "Capital Expenditures",
    "Free Cash Flow",
    "Net Cash from Investing Activities",
    "Net Cash from Financing Activities",
    "Net Change in Cash",
]

# ----------------------------- Concept groups (portable) -----------------------------
# Map common US-GAAP concepts to our canonical condensed rows.
CONCEPT_GROUPS: Dict[str, List[str]] = {
    # Income
    "Revenue": [
        "us-gaap:Revenues",
        "us-gaap:SalesRevenueNet",
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
    ],
    "Cost of Goods Sold": [
        "us-gaap:CostOfRevenue",
        "us-gaap:CostOfGoodsSold",
        "us-gaap:CostOfServices",
    ],
    "Gross Profit": [
        "us-gaap:GrossProfit",
    ],
    "Research and Development": [
        "us-gaap:ResearchAndDevelopmentExpense",
    ],
    "Selling, General and Administrative": [
        "us-gaap:SellingGeneralAndAdministrativeExpense",
    ],
    "Operating Income": [
        "us-gaap:OperatingIncomeLoss",
    ],
    "Non-Operating Income (Expense)": [
        "us-gaap:NonoperatingIncomeExpense",
        "us-gaap:OtherNonoperatingIncomeExpense",
        "us-gaap:OtherIncomeExpenseNet",
    ],
    "Income Before Tax": [
        "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "us-gaap:IncomeBeforeEquityMethodInvestmentsIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxes",
    ],
    "Income Tax Expense": [
        "us-gaap:IncomeTaxExpenseBenefit",
    ],
    "Net Income": [
        "us-gaap:NetIncomeLoss",
        "us-gaap:ProfitLoss",
    ],
    "Basic Shares Outstanding": [
        "us-gaap:WeightedAverageNumberOfSharesOutstandingBasic",
    ],
    "Diluted Shares Outstanding": [
        "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",
    ],
    "Basic EPS": [
        "us-gaap:EarningsPerShareBasic",
    ],
    "Diluted EPS": [
        "us-gaap:EarningsPerShareDiluted",
    ],

    # Balance
    "Cash and Cash Equivalents": [
        "us-gaap:CashAndCashEquivalentsAtCarryingValue",
        "us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "Short-term Investments": [
        "us-gaap:MarketableSecuritiesCurrent",
        "us-gaap:AvailableForSaleSecuritiesCurrent",
    ],
    "Accounts Receivable": [
        "us-gaap:AccountsReceivableNetCurrent",
    ],
    "Inventory": [
        "us-gaap:InventoryNet",
    ],
    "Total Current Assets": [
        "us-gaap:AssetsCurrent",
    ],
    "Property, Plant & Equipment (Net)": [
        "us-gaap:PropertyPlantAndEquipmentNet",
    ],
    "Goodwill and Intangibles": [
        "us-gaap:Goodwill",
        "us-gaap:IntangibleAssetsNetExcludingGoodwill",
        "us-gaap:IntangibleAssetsNet",
        "us-gaap:FiniteLivedIntangibleAssetsNet",
    ],
    "Total Assets": [
        "us-gaap:Assets",
    ],
    "Accounts Payable": [
        "us-gaap:AccountsPayableCurrent",
    ],
    "Short-term Debt": [
        "us-gaap:ShortTermBorrowings",
        "us-gaap:LongTermDebtCurrent",
        "us-gaap:DebtCurrent",
    ],
    "Total Current Liabilities": [
        "us-gaap:LiabilitiesCurrent",
    ],
    "Long-term Debt": [
        "us-gaap:LongTermDebtNoncurrent",
    ],
    "Total Liabilities": [
        "us-gaap:Liabilities",
    ],
    "Total Equity": [
        "us-gaap:StockholdersEquity",
        "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "Total Liabilities and Equity": [
        "us-gaap:LiabilitiesAndStockholdersEquity",
        "us-gaap:LiabilitiesAndStockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],

    # Cash Flow
    "Net Cash from Operating Activities": [
        "us-gaap:NetCashProvidedByUsedInOperatingActivities",
    ],
    "Capital Expenditures": [
        "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
    ],
    "Net Cash from Investing Activities": [
        "us-gaap:NetCashProvidedByUsedInInvestingActivities",
    ],
    "Net Cash from Financing Activities": [
        "us-gaap:NetCashProvidedByUsedInFinancingActivities",
    ],
    "Net Change in Cash": [
        "us-gaap:CashAndCashEquivalentsPeriodIncreaseDecrease",
    ],
    # For EBITDA calc
    "Depreciation and Amortization": [
        "us-gaap:DepreciationDepletionAndAmortization",
    ],
}

# ----------------------------- Label aliases (fallback) -----------------------------
ALIASES = {
    # Income (common variants)
    "Revenues": "Revenue",
    "Revenue": "Revenue",
    "Revenue from contracts with customers": "Revenue",
    "Revenue from contracts with customers, excluding assessed taxes": "Revenue",
    "Revenues before reimbursements (“Net revenues”)": "Revenue",

    "Cost of Revenue": "Cost of Goods Sold",
    "Cost of Goods and Services Sold": "Cost of Goods Sold",
    "Cost of services": "Cost of Goods Sold",
    "Cost of services before reimbursable expenses": "Cost of Goods Sold",
    "Total Cost of Revenue": "Cost of Goods Sold",

    "Gross Profit": "Gross Profit",

    "Research and Development Expense": "Research and Development",
    "Research and development": "Research and Development",

    "Selling, general and administrative": "Selling, General and Administrative",
    "Selling General and Administrative": "Selling, General and Administrative",
    "Selling Expense": "Selling Expense",
    "General and Administrative Expense": "General and Administrative Expense",

    "Operating Income (Loss)": "Operating Income",
    "Operating Profit (Loss)": "Operating Income",
    "Operating income": "Operating Income",

    "Other income (expense), net": "Non-Operating Income (Expense)",
    "Other (expense) income, net": "Non-Operating Income (Expense)",

    "Income (Loss) Before Income Taxes": "Income Before Tax",
    "INCOME BEFORE INCOME TAXES": "Income Before Tax",
    "Income before income taxes": "Income Before Tax",
    "Income Before Tax from Continuing Operations": "Income Before Tax",

    "Provision for Income Taxes": "Income Tax Expense",
    "Income Tax Expense": "Income Tax Expense",

    "Net Income (Loss)": "Net Income",
    "Net income": "Net Income",
    "Net Income from Continuing Operations": "Net Income",

    "Weighted Average Shares, Basic": "Basic Shares Outstanding",
    "Weighted Average Shares Outstanding, Basic": "Basic Shares Outstanding",
    "Shares Outstanding (Basic)": "Basic Shares Outstanding",

    "Weighted Average Shares, Diluted": "Diluted Shares Outstanding",
    "Weighted Average Shares Outstanding, Diluted": "Diluted Shares Outstanding",
    "Shares Outstanding (Diluted)": "Diluted Shares Outstanding",

    "Earnings per Share, Basic": "Basic EPS",
    "Basic EPS": "Basic EPS",
    "Earnings Per Share (Basic)": "Basic EPS",

    "Earnings per Share, Diluted": "Diluted EPS",
    "Diluted EPS": "Diluted EPS",
    "Earnings Per Share (Diluted)": "Diluted EPS",

    # Balance
    "Cash and Cash Equivalents": "Cash and Cash Equivalents",
    "Short-term investments": "Short-term Investments",
    "Short-term Investments": "Short-term Investments",
    "Cash, Cash Equivalents, and Short-term Investments": "Cash and Short-term Investments",

    "Accounts Receivable, Net": "Accounts Receivable",
    "Accounts Receivable": "Accounts Receivable",
    "Inventories": "Inventory",
    "Inventory": "Inventory",

    "Total Current Assets": "Total Current Assets",
    "Total Assets": "Total Assets",

    "Property, Plant and Equipment, Net": "Property, Plant & Equipment (Net)",
    "Property, Plant and Equipment": "Property, Plant & Equipment (Net)",

    "Goodwill": "Goodwill",
    "Intangible Assets": "Intangible Assets",
    "Goodwill and Intangible Assets": "Goodwill and Intangibles",

    "Accounts Payable": "Accounts Payable",
    "Short-term Debt": "Short-term Debt",
    "Short Term Debt": "Short-term Debt",

    "Total Current Liabilities": "Total Current Liabilities",
    "Long-term Debt": "Long-term Debt",
    "Long Term Debt": "Long-term Debt",
    "Total Liabilities": "Total Liabilities",

    "Total Shareholders’ Equity": "Total Equity",
    "Total Stockholders’ Equity": "Total Equity",
    "Total Liabilities and Shareholders’ Equity": "Total Liabilities and Equity",
    "Total Liabilities and Stockholders' Equity": "Total Liabilities and Equity",

    # Cash Flow
    "Net Cash Provided by Operating Activities": "Net Cash from Operating Activities",
    "Net Cash Used in Operating Activities": "Net Cash from Operating Activities",
    "Net cash provided by operating activities": "Net Cash from Operating Activities",

    "Payments for Property, Plant, and Equipment": "Capital Expenditures",
    "Payments for Property, Plant and Equipment": "Capital Expenditures",
    "Purchases of Property and Equipment": "Capital Expenditures",

    "Net Cash Provided by Investing Activities": "Net Cash from Investing Activities",
    "Net Cash Used in Investing Activities": "Net Cash from Investing Activities",
    "Net Cash Provided by Financing Activities": "Net Cash from Financing Activities",
    "Net Cash Used in Financing Activities": "Net Cash from Financing Activities",
    "Net Increase (Decrease) in Cash and Cash Equivalents": "Net Change in Cash",
    "Net change in cash and cash equivalents": "Net Change in Cash",

    "Depreciation, amortization and other": "Depreciation and Amortization",
    "Depreciation, amortization and asset impairments": "Depreciation and Amortization",
}

# Items that are per-share or counts (do not scale)
PER_SHARE_OR_COUNT = {
    "Basic EPS", "Diluted EPS", "Basic Shares Outstanding", "Diluted Shares Outstanding"
}

# -------------------------------------------------------------------------------------------

@dataclass
class FilingMeta:
    period_end: str
    filing_date: str
    accession_number: str
    url: str

def _to_number(x):
    """Convert EDGAR-like strings into floats; return pd.NA if not parseable."""
    import pandas as pd
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return pd.NA
    if isinstance(x, (int, float)):
        return x
    if isinstance(x, Decimal):
        return float(x)
    if isinstance(x, str):
        s = x.strip()
        if s == "" or s.lower() in {"na", "n/a", "--", "—", "-"}:
            return pd.NA
        neg = s.startswith("(") and s.endswith(")")
        s = s.strip("()").replace(",", "").replace("$", "")
        try:
            v = float(s)
            return -v if neg else v
        except Exception:
            return pd.NA
    return pd.NA

def _format_sec_link(accession: str, cik: str) -> str:
    acc_nodashes = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodashes}/{accession}-index.html"

def _date_columns_newest_to_oldest(columns: List[str]) -> List[str]:
    date_cols = []
    for c in columns:
        s = str(c).strip()
        try:
            parse_date(s)
            date_cols.append(s)
        except Exception:
            pass
    if not date_cols:
        raise RuntimeError(f"No fiscal period columns detected. Got columns: {list(columns)[:10]}...")
    return sorted(date_cols, key=lambda s: parse_date(s), reverse=True)

# ---------- Build canonical frames from the raw EDGAR df (using concept → alias → keyword) ----------

def _build_canonical_from_raw(df_raw: pd.DataFrame, condensed_order: List[str]) -> pd.DataFrame:
    """
    From an edgartools statement df (with 'label','concept' + date cols), build a
    canonical condensed frame by:
      1) mapping us-gaap 'concept' to our target names
      2) falling back to label aliases
      3) falling back to keyword heuristics
    If multiple rows map to the same target, take first non-null per column.
    """
    # Identify date columns
    date_cols = [c for c in df_raw.columns if _is_date_col(c)]
    # Make a working copy with numeric values only in date columns
    work = df_raw.copy()
    for c in date_cols:
        work[c] = work[c].map(_to_number)

    # Helper: merge series by taking first non-null per column
    def merge_series(a: Optional[pd.Series], b: pd.Series) -> pd.Series:
        if a is None:
            return b
        return pd.Series([next((x for x in (a[i], b[i]) if pd.notna(x)), pd.NA) for i in a.index], index=a.index)

    # 1) Concept mapping
    pick: Dict[str, pd.Series] = {}
    for _, row in work.iterrows():
        label = str(row.get("label", "")).strip()
        concept = str(row.get("concept", "")).strip()
        series = row[date_cols]
        target = None
        # concept → target
        for name, concepts in CONCEPT_GROUPS.items():
            if concept in concepts:
                target = name
                break
        # alias → target
        if target is None and label in ALIASES:
            target = ALIASES[label]
        # keyword fallback
        if target is None:
            low = label.lower()
            if "revenue" in low:
                target = "Revenue"
            elif "cost" in low and ("revenue" in low or "services" in low or "goods" in low):
                target = "Cost of Goods Sold"
            elif "gross profit" in low:
                target = "Gross Profit"
            elif "research" in low and "development" in low:
                target = "Research and Development"
            elif "selling" in low and "administr" in low:
                target = "Selling, General and Administrative"
            elif "operating" in low and "income" in low:
                target = "Operating Income"
            elif "other" in low and "income" in low:
                target = "Non-Operating Income (Expense)"
            elif "before" in low and "income" in low and "tax" in low:
                target = "Income Before Tax"
            elif "income tax" in low or "provision for income taxes" in low:
                target = "Income Tax Expense"
            elif low.startswith("net income"):
                target = "Net Income"
            elif "earnings per share" in low and "basic" in low:
                target = "Basic EPS"
            elif "earnings per share" in low and "diluted" in low:
                target = "Diluted EPS"
            elif "shares" in low and "basic" in low:
                target = "Basic Shares Outstanding"
            elif "shares" in low and "diluted" in low:
                target = "Diluted Shares Outstanding"
            elif "property" in low and "plant" in low:
                target = "Property, Plant & Equipment (Net)"
            elif "accounts receivable" in low:
                target = "Accounts Receivable"
            elif "inventory" in low:
                target = "Inventory"
            elif "accounts payable" in low:
                target = "Accounts Payable"
            elif "short term debt" in low or "short-term debt" in low:
                target = "Short-term Debt"
            elif "liabilities and stockholders' equity" in low or "liabilities and shareholders’ equity" in low:
                target = "Total Liabilities and Equity"
            elif "liabilities current" in low:
                target = "Total Current Liabilities"
            elif "long-term debt" in low:
                target = "Long-term Debt"
            elif low == "total liabilities":
                target = "Total Liabilities"
            elif "stockholders’ equity" in low or "stockholders' equity" in low:
                target = "Total Equity"
            elif "goodwill" in low or "intangible" in low:
                target = "Goodwill and Intangibles"
            elif "cash and cash equivalents" in low:
                target = "Cash and Cash Equivalents"
            elif "marketable securities" in low or "short-term investments" in low:
                target = "Short-term Investments"
            elif "assets current" in low:
                target = "Total Current Assets"
            elif low == "assets":
                target = "Total Assets"
            elif "net cash" in low and "operating" in low:
                target = "Net Cash from Operating Activities"
            elif "net cash" in low and "investing" in low:
                target = "Net Cash from Investing Activities"
            elif "net cash" in low and "financing" in low:
                target = "Net Cash from Financing Activities"
            elif "net change" in low and "cash" in low:
                target = "Net Change in Cash"
            elif "depreciation" in low and "amort" in low:
                target = "Depreciation and Amortization"

        if target:
            pick[target] = merge_series(pick.get(target), series)

    # Build df from picks
    canon = pd.DataFrame(pick).T
    canon.index.name = "Line Item"

    # Synthesize combined rows
    canon = _synthesize_combined_rows(canon)
    canon = _ensure_gross_profit(canon)

    return canon[date_cols] if not canon.empty else pd.DataFrame(columns=date_cols)

def _is_date_col(c) -> bool:
    try:
        parse_date(str(c).strip())
        return True
    except Exception:
        return False

def _synthesize_combined_rows(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    # SG&A = Selling + G&A (if split)
    if "Selling, General and Administrative" not in out.index:
        s = out.loc["Selling Expense"] if "Selling Expense" in out.index else 0
        g = out.loc["General and Administrative Expense"] if "General and Administrative Expense" in out.index else 0
        if isinstance(s, pd.Series) or isinstance(g, pd.Series):
            out.loc["Selling, General and Administrative"] = (s if isinstance(s, pd.Series) else 0) + (g if isinstance(g, pd.Series) else 0)
    # Cash+STI
    if "Cash and Short-term Investments" not in out.index:
        c = out.loc["Cash and Cash Equivalents"] if "Cash and Cash Equivalents" in out.index else 0
        sti = out.loc["Short-term Investments"] if "Short-term Investments" in out.index else 0
        if isinstance(c, pd.Series) or isinstance(sti, pd.Series):
            out.loc["Cash and Short-term Investments"] = (c if isinstance(c, pd.Series) else 0) + (sti if isinstance(sti, pd.Series) else 0)
    # Goodwill + Intangibles
    if "Goodwill and Intangibles" not in out.index:
        g = out.loc["Goodwill"] if "Goodwill" in out.index else 0
        ia = out.loc["Intangible Assets"] if "Intangible Assets" in out.index else 0
        if isinstance(g, pd.Series) or isinstance(ia, pd.Series):
            out.loc["Goodwill and Intangibles"] = (g if isinstance(g, pd.Series) else 0) + (ia if isinstance(ia, pd.Series) else 0)
    return out

def _ensure_gross_profit(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "Gross Profit" not in out.index:
        if "Revenue" in out.index and "Cost of Goods Sold" in out.index:
            out.loc["Gross Profit"] = out.loc["Revenue"] - out.loc["Cost of Goods Sold"]
    return out

def _derive_income_extras(df: pd.DataFrame, cf_df: Optional[pd.DataFrame]) -> pd.DataFrame:
    out = df.copy()
    if "EBIT" not in out.index:
        if "Operating Income" in out.index:
            out.loc["EBIT"] = out.loc["Operating Income"]
        elif "Income Before Tax" in out.index and "Non-Operating Income (Expense)" in out.index:
            out.loc["EBIT"] = out.loc["Income Before Tax"] - out.loc["Non-Operating Income (Expense)"]
    if "EBITDA" not in out.index:
        da = None
        cand = "Depreciation and Amortization"
        if cand in out.index:
            da = out.loc[cand]
        elif cf_df is not None and cand in cf_df.index:
            da = cf_df.loc[cand]
        if da is not None and "EBIT" in out.index:
            out.loc["EBITDA"] = out.loc["EBIT"] + da
    return out

def _derive_cashflow_extras(cf: pd.DataFrame) -> pd.DataFrame:
    out = cf.copy()
    if "Net Cash from Operating Activities" in out.index and "Capital Expenditures" in out.index:
        out.loc["Free Cash Flow"] = out.loc["Net Cash from Operating Activities"] - out.loc["Capital Expenditures"]
    return out

def _synthesize_balance_totals(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    # Ensure TL&E equals Assets if missing
    if "Total Liabilities and Equity" not in out.index and "Total Assets" in out.index:
        out.loc["Total Liabilities and Equity"] = out.loc["Total Assets"]
    # Total Liabilities
    if "Total Liabilities" not in out.index:
        if "Total Current Liabilities" in out.index and "Long-term Debt" in out.index:
            # heuristic (not exact liabilities noncurrent, but helps some filers)
            pass
        if "Total Liabilities and Equity" in out.index and "Total Equity" in out.index:
            out.loc["Total Liabilities"] = out.loc["Total Liabilities and Equity"] - out.loc["Total Equity"]
    # Total Equity
    if "Total Equity" not in out.index:
        if "Total Liabilities and Equity" in out.index and "Total Liabilities" in out.index:
            out.loc["Total Equity"] = out.loc["Total Liabilities and Equity"] - out.loc["Total Liabilities"]
    return out

# ---------- Pull & construct canonical statements ----------

def _extract_canonical_statements(mf: MultiFinancials) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    # Grab raw dfs with label+concept
    inc_raw = mf.income_statement().to_dataframe()
    bal_raw = mf.balance_sheet().to_dataframe()
    cf_raw  = mf.cashflow_statement().to_dataframe()

    # Build canonical condensed frames from raw
    inc = _build_canonical_from_raw(inc_raw, CONDENSED_INCOME)
    bal = _build_canonical_from_raw(bal_raw, CONDENSED_BALANCE)
    cf  = _build_canonical_from_raw(cf_raw,  CONDENSED_CASHFLOW)

    # Extra synth/derivations
    inc = _derive_income_extras(inc, cf)
    bal = _synthesize_balance_totals(bal)
    cf  = _derive_cashflow_extras(cf)

    return inc, bal, cf

# ---------- Formatting & writing ----------

def _format_sheet(
    df_full: pd.DataFrame,
    condensed_order: List[str],
    per_share_or_count: set,
    decimals: int = 2
) -> Tuple[pd.DataFrame, str, float]:
    # Sort columns by date desc and select target rows (preserve missing with NaN)
    cols = _date_columns_newest_to_oldest(df_full.columns)
    df = df_full.reindex(index=condensed_order, columns=cols)

    # Force millions
    scale_label, factor = ("USD in millions", 1e6)

    out = pd.DataFrame(index=df.index, columns=df.columns, dtype=object)
    for r in df.index:
        for c in df.columns:
            val = df.at[r, c]
            if pd.isna(val):
                out.at[r, c] = "—"
                continue
            if r in per_share_or_count:
                try:
                    out.at[r, c] = f"{float(val):,.2f}"
                except Exception:
                    out.at[r, c] = "—"
            else:
                try:
                    v = float(val) / factor
                    out.at[r, c] = f"({abs(v):,.{decimals}f})" if v < 0 else f"{v:,.{decimals}f}"
                except Exception:
                    out.at[r, c] = "—"
    out.index.name = "Line Item"
    return out, scale_label, factor

def _gather_filing_meta(mf: MultiFinancials, cik: str) -> Dict[str, FilingMeta]:
    meta_by_period: Dict[str, FilingMeta] = {}
    for f in getattr(mf, "filings", []):
        period_end = str(getattr(f, "period", "")) or str(getattr(f, "period_end", ""))
        filed = str(getattr(f, "filed", "")) or str(getattr(f, "filing_date", ""))
        acc = str(getattr(f, "accession_number", "")) or str(getattr(f, "accession_no", ""))
        form = str(getattr(f, "form", ""))
        if not period_end:
            continue
        keep = True
        if period_end in meta_by_period:
            # prefer 10-K/A if both exist
            cur_is_amend = (form.upper() == "10-K/A")
            keep = cur_is_amend or not keep
        url = _format_sec_link(acc, cik) if acc and cik else ""
        if keep:
            meta_by_period[period_end] = FilingMeta(period_end=period_end, filing_date=filed, accession_number=acc, url=url)
    return meta_by_period

def _filter_usd_or_abort(mf: MultiFinancials):
    cur = getattr(mf, "currency", None) or getattr(mf, "currencies", None)
    if isinstance(cur, str) and cur.upper() != "USD":
        raise RuntimeError(f"Only USD filers supported, detected currency: {cur}")

# ----------------------------- Main builder -----------------------------

def build_workbook_for_ticker(ticker: str) -> str:
    ticker = ticker.strip().upper()
    company = Company(ticker)
    cik = str(getattr(company, "cik", "") or getattr(company, "cik_str", "") or "").lstrip("0")

    filings = company.get_filings(form="10-K").head(14)
    mf = MultiFinancials.extract(filings)

    _filter_usd_or_abort(mf)

    inc_raw, bal_raw, cf_raw = _extract_canonical_statements(mf)

    # Determine shared year columns (based on income stmt)
    cols_order = _date_columns_newest_to_oldest(inc_raw.columns)[:10]
    inc_raw = inc_raw.reindex(columns=cols_order)
    bal_raw = bal_raw.reindex(columns=cols_order)
    cf_raw  = cf_raw.reindex(columns=cols_order)

    if len(cols_order) < 2:
        raise RuntimeError(f"Not enough annual data for {ticker}. Need at least 2 years.")

    inc_fmt, inc_scale, _ = _format_sheet(inc_raw, CONDENSED_INCOME, PER_SHARE_OR_COUNT)
    bal_fmt, bal_scale, _ = _format_sheet(bal_raw, CONDENSED_BALANCE, PER_SHARE_OR_COUNT)
    cf_fmt,  cf_scale,  _ = _format_sheet(cf_raw,  CONDENSED_CASHFLOW, PER_SHARE_OR_COUNT)

    newest_year = parse_date(cols_order[0]).year
    oldest_year = parse_date(cols_order[-1]).year
    out_name = f"{ticker}_{newest_year}-{oldest_year}.xlsx"

    meta_by_period = _gather_filing_meta(mf, cik=cik)

    with pd.ExcelWriter(out_name, engine="openpyxl") as xw:
        for title, df_fmt, scale_label in [
            ("Income Statement", inc_fmt, inc_scale),
            ("Balance Sheet",   bal_fmt, bal_scale),
            ("Cash Flow",       cf_fmt,  cf_scale),
        ]:
            # Write table at row 2 (note at row 1)
            df_fmt.to_excel(xw, sheet_name=title, startrow=1, index=True)
            ws = xw.book[title]
            note = f"{scale_label} (rounded to 2 decimals). Per-share and share counts are unscaled."
            ws["A1"] = note
            last_col = 1 + len(df_fmt.columns)
            ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
            ws.freeze_panes = "B3"

            # Append traceability
            start_row = 2 + len(df_fmt.index) + 2
            filing_dates, accessions = [], []
            for col in df_fmt.columns:
                meta = meta_by_period.get(col)
                if meta:
                    filing_dates.append(meta.filing_date or "")
                    accessions.append(meta.url or meta.accession_number or "")
                else:
                    filing_dates.append("")
                    accessions.append("")
            trace_df = pd.DataFrame({"Filing Date": filing_dates,
                                     "Accession (SEC link)": accessions},
                                    index=df_fmt.columns).T
            trace_df.to_excel(xw, sheet_name=title, startrow=start_row, index=True)

    return out_name

# ----------------------------- CLI -----------------------------

def main():
    if len(sys.argv) != 2:
        print("Usage: python build_financials.py TICKER", file=sys.stderr)
        sys.exit(2)
    ticker = sys.argv[1]
    try:
        out = build_workbook_for_ticker(ticker)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Saved: {out}")

if __name__ == "__main__":
    warnings.simplefilter("ignore", category=FutureWarning)
    main()
