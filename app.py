import base64
import os
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
OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
MARKETPLACE_INSIGHTS_URL = "https://api.ebay.com/buy/marketplace_insights/v1_beta/item_sales/search"
MARKETPLACE_INSIGHTS_SCOPE = "https://api.ebay.com/oauth/api_scope/buy.marketplace.insights"

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

@st.cache_data(ttl=7000)
def get_oauth_token(client_id, client_secret):
    """Fetch an OAuth application token (cached for ~2 hours)."""
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    response = requests.post(
        OAUTH_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data=f"grant_type=client_credentials&scope={MARKETPLACE_INSIGHTS_SCOPE}",
        timeout=15,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def fetch_sold_items(client_id, client_secret, keywords, condition_id, exclude_keywords, days, max_pages):
    """Call eBay Marketplace Insights API and return parsed sold item list plus a reference image URL."""
    token = get_oauth_token(client_id, client_secret)

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)

    start_str = start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_str = end_time.strftime("%Y-%m-%dT%H:%M:%SZ")

    filter_parts = [f"lastSoldDate:[{start_str}..{end_str}]"]
    if condition_id:
        filter_parts.append(f"conditionIds:{{{condition_id}}}")

    all_items = []
    reference_image_url = None

    for page in range(max_pages):
        offset = page * 100
        params = {
            "q": keywords,
            "limit": 100,
            "offset": offset,
            "filter": ",".join(filter_parts),
        }

        response = requests.get(
            MARKETPLACE_INSIGHTS_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            },
            params=params,
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()

        items = data.get("itemSales", [])
        if not items:
            break

        for item in items:
            try:
                price_info = item.get("price", {})
                item_price = float(price_info.get("value", 0))

                title = item.get("title", "")
                if exclude_keywords:
                    if any(kw.strip().lower() in title.lower() for kw in exclude_keywords.split(",") if kw.strip()):
                        continue

                last_sold_str = item.get("lastSoldDate", "")
                sold_date = (
                    datetime.fromisoformat(last_sold_str.replace("Z", "+00:00")).date()
                    if last_sold_str else None
                )

                condition = item.get("condition", "Unknown")
                url = item.get("itemWebUrl", "")

                if reference_image_url is None:
                    image = item.get("image", {})
                    if image:
                        reference_image_url = image.get("imageUrl", "")

                all_items.append({
                    "Title": title,
                    "Sale Price": item_price,
                    "Condition": condition,
                    "Sold Date": sold_date,
                    "URL": url,
                })
            except (KeyError, ValueError):
                continue

        total = data.get("total", 0)
        if offset + 100 >= total or len(items) < 100:
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

    default_client_id = st.secrets.get("EBAY_CLIENT_ID", os.environ.get("EBAY_CLIENT_ID", ""))
    default_client_secret = st.secrets.get("EBAY_CLIENT_SECRET", os.environ.get("EBAY_CLIENT_SECRET", ""))

    client_id = st.text_input(
        "eBay App ID (Client ID)",
        value=default_client_id,
        type="password",
        help="Your App ID from developer.ebay.com",
    )
    client_secret = st.text_input(
        "eBay Cert ID (Client Secret)",
        value=default_client_secret,
        type="password",
        help="Your Cert ID from developer.ebay.com → Application Keys",
    )

    st.subheader("Search Settings")
    condition = st.selectbox("Item Condition", list(CONDITIONS.keys()))
    exclude_kw = st.text_input(
        "Exclude keywords (comma-separated)",
        placeholder="lot, broken, parts",
        help="Listings containing these words will be filtered out",
    )
    max_pages = st.slider("Max result pages (100 per page)", min_value=1, max_value=3, value=1)

    TIME_PERIODS = {
        "Last 30 days": 30,
        "Last 60 days": 60,
        "Last 90 days": 90,
    }
    period_label = st.selectbox("Time period", list(TIME_PERIODS.keys()), index=1)
    days = TIME_PERIODS[period_label]

    st.divider()
    st.caption("eBay Marketplace Insights API · Free for registered developers")

# ── Main area ─────────────────────────────────────────────────────────────────
st.header("eBay Sold Price Checker", divider="gray")

col_search, col_btn = st.columns([5, 1])
with col_search:
    keywords = st.text_input("Search item", placeholder='e.g. "iPhone 15 Pro 256GB"', label_visibility="collapsed")
with col_btn:
    search_clicked = st.button("Search", type="primary", use_container_width=True)

# ── Run search ────────────────────────────────────────────────────────────────
if search_clicked:
    if not client_id or not client_secret:
        st.error("Please enter your eBay App ID and Cert ID in the sidebar. Get them at developer.ebay.com → Application Keys.")
        st.stop()
    if not keywords.strip():
        st.warning("Please enter a search term.")
        st.stop()

    condition_id = CONDITIONS[condition]

    with st.spinner(f"Searching eBay for '{keywords}'..."):
        try:
            df, reference_image_url = fetch_sold_items(
                client_id, client_secret, keywords, condition_id, exclude_kw, days, max_pages
            )
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 403:
                st.error(
                    "Access denied (403). Your eBay developer account may not have access to the "
                    "Marketplace Insights API. Visit developer.ebay.com and ensure your application "
                    "has the `buy.marketplace.insights` OAuth scope enabled."
                )
            else:
                st.error(f"Error fetching data: {e}")
            st.stop()
        except Exception as e:
            st.error(f"Error fetching data: {e}")
            st.stop()

    if df.empty:
        st.warning(f"No sold listings found for **{keywords}** in the past {days} days with the selected filters.")
        st.stop()

    price_col = "Sale Price"
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
            st.caption(f"Period: {period_label}")
    else:
        st.subheader(f"Results: {len(df)} sold listings for \"{keywords}\"")
        st.caption(f"Period: {period_label}")

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
    csv = display_df[["Sold Date", "Sale Price", "Condition", "Outlier", "Title", "URL"]].to_csv(index=False)
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
    - Download results as CSV
    """)
