import os
import statistics
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from datetime import datetime, timedelta, timezone

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="eBay Price Checker",
    page_icon="🏷️",
    layout="wide",
)

# ── Constants ─────────────────────────────────────────────────────────────────
FINDING_API_URL = "https://svcs.ebay.com/services/search/FindingService/v1"

CONDITIONS = {
    "Any condition": None,
    "New": "1000",
    "New other (see details)": "1500",
    "New with defects": "1750",
    "Manufacturer refurbished": "2000",
    "Seller refurbished": "2500",
    "Used": "3000",
    "For parts or not working": "7000",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def fetch_sold_items(app_id, keywords, condition_id, exclude_keywords, include_shipping, days, max_pages):
    """Call eBay Finding API and return parsed item list plus a reference image URL."""
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)

    all_items = []
    reference_image_url = None

    for page in range(1, max_pages + 1):
        params = {
            "OPERATION-NAME": "findCompletedItems",
            "SERVICE-VERSION": "1.0.0",
            "SECURITY-APPNAME": app_id,
            "RESPONSE-DATA-FORMAT": "JSON",
            "REST-PAYLOAD": "",
            "keywords": keywords,
            "itemFilter(0).name": "SoldItemsOnly",
            "itemFilter(0).value": "true",
            "itemFilter(1).name": "EndTimeFrom",
            "itemFilter(1).value": start_time.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "itemFilter(2).name": "EndTimeTo",
            "itemFilter(2).value": end_time.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "sortOrder": "EndTimeSoonest",
            "paginationInput.entriesPerPage": 100,
            "paginationInput.pageNumber": page,
        }

        fi = 3
        if condition_id:
            params[f"itemFilter({fi}).name"] = "Condition"
            params[f"itemFilter({fi}).value"] = condition_id
            fi += 1

        response = requests.get(FINDING_API_URL, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        result = data["findCompletedItemsResponse"][0]
        if result["ack"][0] != "Success":
            err = result.get("errorMessage", [{}])[0].get("error", [{}])[0].get("message", ["Unknown"])[0]
            raise ValueError(f"eBay API error: {err}")

        items = result.get("searchResult", [{}])[0].get("item", [])
        if not items:
            break

        for item in items:
            try:
                item_price = float(item["sellingStatus"][0]["currentPrice"][0]["__value__"])
                shipping_price = 0.0
                shipping_type = item.get("shippingInfo", [{}])[0].get("shippingType", ["Unknown"])[0]
                shipping_cost_list = item.get("shippingInfo", [{}])[0].get("shippingServiceCost", [])
                if shipping_cost_list:
                    shipping_price = float(shipping_cost_list[0]["__value__"])

                total_price = item_price + shipping_price if include_shipping else item_price

                title = item["title"][0]
                # Skip if any exclude keyword appears in title
                if exclude_keywords:
                    skip = any(kw.strip().lower() in title.lower() for kw in exclude_keywords.split(",") if kw.strip())
                    if skip:
                        continue

                end_time_str = item["listingInfo"][0]["endTime"][0]
                sold_date = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))
                condition = item.get("condition", [{}])[0].get("conditionDisplayName", ["Unknown"])[0]
                url = item["viewItemURL"][0]

                # Grab the first available gallery image as a reference
                if reference_image_url is None:
                    gallery = item.get("galleryURL", [None])[0]
                    if gallery:
                        # Replace _s-l140 thumbnail suffix with _s-l500 for a larger image
                        reference_image_url = gallery.replace("_s-l140", "_s-l500")

                all_items.append({
                    "Title": title,
                    "Item Price": item_price,
                    "Shipping": shipping_price,
                    "Total Price": total_price,
                    "Condition": condition,
                    "Sold Date": sold_date.date(),
                    "URL": url,
                })
            except (KeyError, IndexError, ValueError):
                continue

        # Stop if fewer than 100 returned (last page)
        if len(items) < 100:
            break

    return pd.DataFrame(all_items), reference_image_url


def detect_outliers(prices: pd.Series):
    """Return boolean mask of outliers using IQR method."""
    q1 = prices.quantile(0.25)
    q3 = prices.quantile(0.75)
    iqr = q3 - q1
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    return (prices < lower) | (prices > upper)


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🏷️ eBay Price Checker")
    st.markdown("Find sold listing prices on eBay for completed listings.")
    st.divider()

    # Read from Streamlit Cloud secrets first, then env var, then let user type it
    default_key = st.secrets.get("EBAY_APP_ID", os.environ.get("EBAY_APP_ID", ""))
    api_key = st.text_input(
        "eBay App ID",
        value=default_key,
        type="password",
        help="Get your free App ID at developer.ebay.com",
    )

    st.subheader("Search Settings")
    condition = st.selectbox("Item Condition", list(CONDITIONS.keys()))
    exclude_kw = st.text_input(
        "Exclude keywords (comma-separated)",
        placeholder="lot, broken, parts",
        help="Listings containing these words will be filtered out",
    )
    include_shipping = st.toggle("Include shipping in price", value=False)
    max_pages = st.slider("Max result pages (100 per page)", min_value=1, max_value=3, value=1)

    TIME_PERIODS = {
        "Last 30 days": 30,
        "Last 60 days": 60,
        "Last 90 days": 90,
    }
    period_label = st.selectbox("Time period", list(TIME_PERIODS.keys()), index=1)
    days = TIME_PERIODS[period_label]

    st.divider()
    st.caption("Free eBay API · 5,000 calls/day limit")

# ── Main area ─────────────────────────────────────────────────────────────────
st.header("eBay Sold Price Checker", divider="gray")

col_search, col_btn = st.columns([5, 1])
with col_search:
    keywords = st.text_input("Search item", placeholder='e.g. "iPhone 15 Pro 256GB"', label_visibility="collapsed")
with col_btn:
    search_clicked = st.button("Search", type="primary", use_container_width=True)

# ── Run search ────────────────────────────────────────────────────────────────
if search_clicked:
    if not api_key:
        st.error("Please enter your eBay App ID in the sidebar. Get one free at developer.ebay.com")
        st.stop()
    if not keywords.strip():
        st.warning("Please enter a search term.")
        st.stop()

    condition_id = CONDITIONS[condition]

    with st.spinner(f"Searching eBay for '{keywords}'..."):
        try:
            df, reference_image_url = fetch_sold_items(api_key, keywords, condition_id, exclude_kw, include_shipping, days, max_pages)
        except Exception as e:
            st.error(f"Error fetching data: {e}")
            st.stop()

    if df.empty:
        st.warning(f"No sold listings found for **{keywords}** in the past {days} days with the selected filters.")
        st.stop()

    price_col = "Total Price" if include_shipping else "Item Price"
    prices = df[price_col]

    # Mark outliers
    df["Outlier"] = detect_outliers(prices)
    clean_prices = prices[~df["Outlier"]]

    # ── Header: image + title ──────────────────────────────────────────────────
    if reference_image_url:
        img_col, title_col = st.columns([1, 6])
        with img_col:
            st.image(reference_image_url, width=120)
        with title_col:
            st.subheader(f"Results: {len(df)} sold listings for \"{keywords}\"")
            st.caption(f"Period: {period_label}" + (" · Prices include shipping" if include_shipping else ""))
    else:
        st.subheader(f"Results: {len(df)} sold listings for \"{keywords}\"")
        st.caption(f"Period: {period_label}" + (" · Prices include shipping" if include_shipping else ""))

    # ── Stats ──────────────────────────────────────────────────────────────────

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Mean", f"${prices.mean():.2f}")
    m2.metric("Median", f"${prices.median():.2f}")
    m3.metric("Min", f"${prices.min():.2f}")
    m4.metric("Max", f"${prices.max():.2f}")
    m5.metric("Std Dev", f"${prices.std():.2f}")
    m6.metric("Outliers", int(df["Outlier"].sum()))

    q1 = prices.quantile(0.25)
    q3 = prices.quantile(0.75)
    iqr_val = q3 - q1
    ci1, ci2, ci3 = st.columns(3)
    ci1.metric("25th Percentile (Q1)", f"${q1:.2f}")
    ci2.metric("75th Percentile (Q3)", f"${q3:.2f}")
    ci3.metric("IQR (typical price range)", f"${iqr_val:.2f}")

    st.divider()

    # ── Charts ─────────────────────────────────────────────────────────────────
    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        st.subheader("Price Distribution")
        fig_hist = px.histogram(
            df, x=price_col, nbins=30,
            color="Outlier",
            color_discrete_map={False: "#2563eb", True: "#dc2626"},
            labels={price_col: "Price ($)", "count": "# Sold"},
            title="Price Histogram (red = outlier)",
        )
        fig_hist.update_layout(showlegend=False, height=350)
        st.plotly_chart(fig_hist, use_container_width=True)

    with chart_col2:
        st.subheader("Price Trend Over Time")
        trend_df = df.groupby("Sold Date")[price_col].median().reset_index()
        trend_df.columns = ["Date", "Median Price"]
        fig_trend = px.line(
            trend_df, x="Date", y="Median Price",
            markers=True,
            labels={"Median Price": "Median Price ($)"},
            title=f"Median Sold Price by Day ({period_label})",
        )
        fig_trend.update_traces(line_color="#2563eb")
        fig_trend.update_layout(height=350)
        st.plotly_chart(fig_trend, use_container_width=True)

    # ── Box plot ───────────────────────────────────────────────────────────────
    st.subheader("Price Spread")
    fig_box = go.Figure()
    fig_box.add_trace(go.Box(
        x=prices,
        name="All sales",
        marker_color="#2563eb",
        boxmean="sd",
    ))
    fig_box.update_layout(height=180, margin=dict(t=20, b=20), xaxis_title="Price ($)")
    st.plotly_chart(fig_box, use_container_width=True)

    st.divider()

    # ── Results table ──────────────────────────────────────────────────────────
    st.subheader("Sold Listings")

    show_outliers = st.toggle("Show outliers in table", value=True)
    display_df = df if show_outliers else df[~df["Outlier"]]

    # Make URL clickable
    display_df = display_df.copy()
    display_df["Link"] = display_df["URL"].apply(lambda u: f'<a href="{u}" target="_blank">View</a>')

    st.dataframe(
        display_df[["Sold Date", price_col, "Condition", "Outlier", "Title"]].rename(columns={price_col: "Price ($)"}),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Price ($)": st.column_config.NumberColumn(format="$%.2f"),
            "Sold Date": st.column_config.DateColumn(),
            "Outlier": st.column_config.CheckboxColumn(),
        },
    )

    # ── CSV Export ─────────────────────────────────────────────────────────────
    csv = display_df[["Sold Date", "Item Price", "Shipping", "Total Price", "Condition", "Outlier", "Title", "URL"]].to_csv(index=False)
    st.download_button(
        label="Download CSV",
        data=csv,
        file_name=f"ebay_{keywords.replace(' ', '_')}_{period_label.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.csv",
        mime="text/csv",
    )

else:
    st.info("Enter a search term above and click **Search** to get started.")
    st.markdown("""
    **Features:**
    - Price statistics: mean, median, min, max, std dev, IQR, percentiles
    - Outlier detection (IQR method)
    - Price trend chart over time
    - Price histogram & box plot
    - Filter by item condition
    - Exclude keywords to clean results
    - Optional shipping cost inclusion
    - Download results as CSV
    """)
