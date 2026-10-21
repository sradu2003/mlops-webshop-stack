"""Internal analytics dashboard for the co-creation webshop.

WHAT CHANGED VS. THE ORIGINAL
-----------------------------
The original dashboard called `GET /products` and read `name`, `price` and
`stock` fields. The platform API has no `/products` route at all — the
content model is exposed through `/api/content` — so the dashboard ALWAYS
rendered "API unreachable" and the analytics surface was dead on arrival.

This version is built against the endpoints that actually exist:

    GET /api/usage/summary?since_hours=N   -> the monetisation funnel
    GET /api/content?page_size=200          -> the published catalogue

Both are public read endpoints, so no credentials are needed. Prices are
integer minor units (`price_cents`) and are formatted as currency here
rather than in the API, so the API never has to guess a currency symbol.
"""

import os

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

API = os.environ.get("API_BASE", "http://127.0.0.1:3000").rstrip("/")
TIMEOUT = int(os.environ.get("API_TIMEOUT", "10"))
CURRENCY = os.environ.get("CURRENCY_SYMBOL", "€")

st.set_page_config(page_title="Shop Insights", layout="wide")
st.title("Shop Insights")


def _get(path: str, params: dict | None = None) -> dict:
    """GET a JSON object from the API, raising on any failure."""
    response = requests.get(f"{API}{path}", params=params, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def _money(cents: float) -> str:
    return f"{CURRENCY}{cents / 100:,.2f}"


# --- Controls ---------------------------------------------------------------
window = st.sidebar.selectbox(
    "Time window",
    options=[1, 6, 24, 72, 168, 720],
    index=2,
    format_func=lambda h: f"last {h}h" if h < 24 else f"last {h // 24}d",
)
st.sidebar.caption(f"API: {API}")

# --- Funnel -----------------------------------------------------------------
try:
    summary = _get("/api/usage/summary", {"since_hours": window})
except Exception as exc:  # noqa: BLE001 - the dashboard must not crash
    st.error(f"API unreachable: {exc}")
    st.info("Is the platform API running? Check: systemctl status python-api")
    st.stop()

events_total = summary.get("total_events", 0)
registered = summary.get("unique_registered_viewers", 0)
anonymous = summary.get("unique_anonymous_viewers", 0)
watch_seconds = summary.get("watch_seconds", 0)
avg_completion = summary.get("avg_completion_ratio", 0.0)

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Events", f"{events_total:,}")
c2.metric("Registered viewers", f"{registered:,}")
c3.metric("Anonymous viewers", f"{anonymous:,}")
c4.metric("Watch time", f"{watch_seconds / 3600:,.1f} h")
c5.metric("Avg completion", f"{avg_completion * 100:.1f}%")

if events_total == 0:
    st.info(
        "No usage events in this window. Record one with: "
        "curl -X POST /api/usage/events -H 'Content-Type: application/json' "
        "-d '{\"event_type\":\"page_view\"}'"
    )

# --- Events by type ---------------------------------------------------------
by_type = summary.get("events_by_type", {})
if by_type:
    st.subheader("Events by type")
    type_df = pd.DataFrame(
        sorted(by_type.items(), key=lambda kv: kv[1], reverse=True),
        columns=["event_type", "events"],
    )
    st.bar_chart(type_df.set_index("event_type")["events"])
    st.dataframe(type_df, use_container_width=True, hide_index=True)

# --- Top content ------------------------------------------------------------
top_content = summary.get("top_content", [])
if top_content:
    st.subheader("Top content")
    content_df = pd.DataFrame(top_content)
    content_df = content_df.rename(
        columns={"content_id": "id", "title": "title", "events": "events", "watch_seconds": "watch_seconds"}
    )
    content_df["watch_hours"] = (content_df["watch_seconds"] / 3600).round(2)
    st.dataframe(
        content_df[["id", "title", "events", "watch_hours"]],
        use_container_width=True,
        hide_index=True,
    )
    fig = px.bar(
        content_df.head(10),
        x="title",
        y="events",
        title="Events per content item",
    )
    st.plotly_chart(fig, use_container_width=True)

# --- Catalogue --------------------------------------------------------------
st.subheader("Published catalogue")
try:
    catalogue = _get("/api/content", {"page_size": 200})
    items = catalogue.get("items", [])
except Exception as exc:  # noqa: BLE001
    st.warning(f"Could not load the catalogue: {exc}")
    items = []

if not items:
    st.info("No published content yet.")
else:
    cat_df = pd.DataFrame(items)
    cat_df["price"] = cat_df["price_cents"].map(_money)
    st.dataframe(
        cat_df[["id", "title", "content_type", "price", "created_at"]],
        use_container_width=True,
        hide_index=True,
    )
    total_value = cat_df["price_cents"].sum()
    st.caption(
        f"{len(cat_df)} published item(s) · "
        f"catalogue list value {total_value / 100:,.2f} {CURRENCY}"
    )
