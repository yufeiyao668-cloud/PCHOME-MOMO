# -*- coding: utf-8 -*-
"""
MOMO × PChome 商品價格查詢、視覺化與比價 (Streamlit)
執行：streamlit run app.py
"""
import re
import time

import pandas as pd
import plotly.express as px
import streamlit as st
from curl_cffi import requests

# ------------------------------------------------------------------
# 常數設定
# ------------------------------------------------------------------
MOMO_API = "https://apisearch.momoshop.com.tw/momoSearchCloud/moec/textSearch"
MOMO_HOME = "https://www.momoshop.com.tw/"
MOMO_ITEM = "https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code={}"
MOMO_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.momoshop.com.tw",
    "Referer": "https://www.momoshop.com.tw/",
}

PCHOME_API = "https://ecshweb.pchome.com.tw/search/v3.3/all/results"
PCHOME_ITEM = "https://24h.pchome.com.tw/prod/{}"
PCHOME_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": "https://24h.pchome.com.tw/",
}

PCHOME_SORT = {
    "銷量(高→低)": "sale/dc",
    "價格(低→高)": "prc/ac",
    "價格(高→低)": "prc/dc",
    "相關度": "rnk/dc",
}
PLATFORMS = ["MOMO", "PChome"]
COLUMNS = ["平台", "頁數", "品名", "品牌", "價格", "連結"]

st.set_page_config(page_title="MOMO × PChome 比價", page_icon="🛒", layout="wide")


# ------------------------------------------------------------------
# 共用工具
# ------------------------------------------------------------------
def parse_price(value) -> int | None:
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None


def extract_brand(name: str) -> str:
    """品名常以【品牌】開頭，抓不到則歸為『其他』。"""
    m = re.search(r"[【\[](.+?)[】\]]", name or "")
    return m.group(1).strip()[:12] if m else "其他"


# ------------------------------------------------------------------
# 爬蟲：MOMO
# ------------------------------------------------------------------
def _momo_payload(keyword: str, page: int) -> dict:
    return {
        "host": "momoshop",
        "flag": "searchEngine",
        "data": {
            "searchValue": keyword,
            "searchType": "1",
            "cateLevel": "-1",
            "curPage": str(page),
            "priceS": "0",
            "priceE": "9999999",
            "showType": "chessboardType",
            "isFuzzy": "0",
            "rtnCateDatainfo": {
                "cateCode": "",
                "cateLv": "-1",
                "keyword": keyword,
                "curPage": str(page),
                "historyDoPush": "false",
                "timestamp": int(time.time() * 1000),
            },
            "flag": 2018,
            "serviceCode": "MT01",
            "addressSearchData": {},
        },
    }


@st.cache_data(show_spinner=False, ttl=600)
def fetch_momo(keyword: str, pages: int, delay: float) -> pd.DataFrame:
    session = requests.Session(impersonate="chrome")  # 模擬 Chrome TLS 指紋
    session.get(MOMO_HOME, headers=MOMO_HEADERS, timeout=20)  # 先取得 cookie

    rows = []
    for page in range(1, pages + 1):
        resp = session.post(MOMO_API, headers=MOMO_HEADERS,
                            json=_momo_payload(keyword, page), timeout=20)
        if resp.status_code != 200:
            raise RuntimeError(f"MOMO 第 {page} 頁失敗：HTTP {resp.status_code}")
        goods = resp.json().get("rtnSearchData", {}).get("goodsInfoList", [])
        if not goods:
            break
        for p in goods:
            code = p.get("goodsCode", "")
            name = p.get("goodsName", "")
            rows.append({
                "平台": "MOMO",
                "頁數": page,
                "品名": name,
                "品牌": extract_brand(name),
                "價格": parse_price(p.get("goodsPrice", "")),
                "連結": MOMO_ITEM.format(code) if code else "",
            })
        if page < pages:
            time.sleep(delay)
    return _finalize(rows)


# ------------------------------------------------------------------
# 爬蟲：PChome
# ------------------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=600)
def fetch_pchome(keyword: str, pages: int, sort: str, delay: float) -> pd.DataFrame:
    session = requests.Session(impersonate="chrome")
    rows = []
    for page in range(1, pages + 1):
        resp = session.get(PCHOME_API,
                           params={"q": keyword, "page": page, "sort": sort},
                           headers=PCHOME_HEADERS, timeout=20)
        if resp.status_code != 200:
            raise RuntimeError(f"PChome 第 {page} 頁失敗：HTTP {resp.status_code}")
        prods = resp.json().get("prods", [])
        if not prods:
            break
        for p in prods:
            name = p.get("name", "")
            pid = p.get("Id", "")
            rows.append({
                "平台": "PChome",
                "頁數": page,
                "品名": name,
                "品牌": extract_brand(name),
                "價格": parse_price(p.get("price", "")),
                "連結": PCHOME_ITEM.format(pid) if pid else "",
            })
        if page < pages:
            time.sleep(delay)
    return _finalize(rows)


def _finalize(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=COLUMNS)
    df = df.dropna(subset=["價格"])
    df = df[df["品名"].astype(bool)].reset_index(drop=True)
    df["價格"] = df["價格"].astype(int)
    return df


def fetch_all(keyword: str, pages: int, platforms: list, sort: str, delay: float):
    """依序抓取各平台；單一平台失敗不影響其他平台。回傳 (合併 df, 錯誤訊息 dict)。"""
    frames, errors = [], {}
    bar = st.progress(0.0, text="準備中…")
    for i, plat in enumerate(platforms):
        bar.progress(i / len(platforms), text=f"正在抓取 {plat}…")
        try:
            if plat == "MOMO":
                frames.append(fetch_momo(keyword, pages, delay))
            else:
                frames.append(fetch_pchome(keyword, pages, sort, delay))
        except Exception as e:  # noqa: BLE001
            errors[plat] = str(e)
    bar.empty()
    frames = [f for f in frames if not f.empty]
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    return df, errors


# ------------------------------------------------------------------
# 資料處理
# ------------------------------------------------------------------
def add_price_bins(df: pd.DataFrame, n_bins: int) -> pd.DataFrame:
    """兩平台共用同一組價格區間（分位數切分），圖表才能互相比較。"""
    df = df.copy()
    prices = df["價格"]
    if prices.nunique() < 2:
        df["價格區間"] = f"{prices.iloc[0]}"
        return df
    _, edges = pd.qcut(prices, q=n_bins, retbins=True, duplicates="drop")
    edges = sorted(set(int(round(e)) for e in edges))
    if len(edges) < 2:
        df["價格區間"] = f"{int(prices.iloc[0])}"
        return df
    labels = [f"{edges[i]}~{edges[i + 1]}" for i in range(len(edges) - 1)]
    df["價格區間"] = pd.cut(prices, bins=edges, labels=labels,
                          include_lowest=True).astype(str)
    df["價格區間"] = pd.Categorical(df["價格區間"], categories=labels, ordered=True)
    return df


def _bigrams(text: str) -> set:
    t = re.sub(r"[【】\[\]()（）\s/\\,，.\-_+*]+", "", text.lower())
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else {t}


def _model_tokens(text: str) -> set:
    """含數字的英數型號，例如 20000mAh、A2B3C。"""
    return {t.lower() for t in re.findall(r"[A-Za-z]*\d+[A-Za-z0-9]*", text) if len(t) >= 3}


def match_products(df: pd.DataFrame, threshold: float, limit: int = 400) -> pd.DataFrame:
    """以字元 bigram 相似度 + 共同型號加權，找出 MOMO / PChome 疑似同款商品。"""
    a = df[df["平台"] == "MOMO"].head(limit).reset_index(drop=True)
    b = df[df["平台"] == "PChome"].head(limit).reset_index(drop=True)
    if a.empty or b.empty:
        return pd.DataFrame()

    b_grams = [_bigrams(n) for n in b["品名"]]
    b_models = [_model_tokens(n) for n in b["品名"]]

    out = []
    for _, ra in a.iterrows():
        ga, ma = _bigrams(ra["品名"]), _model_tokens(ra["品名"])
        best_j, best_score = -1, 0.0
        for j, gb in enumerate(b_grams):
            union = len(ga | gb)
            if not union:
                continue
            score = len(ga & gb) / union
            if ma & b_models[j]:
                score = min(1.0, score + 0.25)  # 型號相同加分
            if score > best_score:
                best_j, best_score = j, score
        if best_j >= 0 and best_score >= threshold:
            rb = b.iloc[best_j]
            diff = int(ra["價格"]) - int(rb["價格"])
            out.append({
                "相似度": round(best_score, 2),
                "MOMO 品名": ra["品名"],
                "MOMO 價格": int(ra["價格"]),
                "PChome 品名": rb["品名"],
                "PChome 價格": int(rb["價格"]),
                "價差 (MOMO−PChome)": diff,
                "較便宜": "MOMO" if diff < 0 else ("PChome" if diff > 0 else "相同"),
                "MOMO 連結": ra["連結"],
                "PChome 連結": rb["連結"],
            })
    res = pd.DataFrame(out)
    if not res.empty:
        res = res.sort_values("相似度", ascending=False).drop_duplicates(
            subset=["PChome 品名"]).reset_index(drop=True)
    return res


# ------------------------------------------------------------------
# 圖表 (Plotly，字型由瀏覽器渲染)
# ------------------------------------------------------------------
COLOR_MAP = {"MOMO": "#e5006e", "PChome": "#1a73e8"}


def pie_charts(df: pd.DataFrame, kw: str):
    cols = st.columns(df["平台"].nunique())
    for col, (plat, g) in zip(cols, df.groupby("平台")):
        cnt = g.groupby("價格區間", observed=True).size().reset_index(name="商品數")
        fig = px.pie(cnt, names="價格區間", values="商品數", hole=0.35,
                     title=f"{plat}「{kw}」價格區間")
        fig.update_traces(textinfo="percent+label")
        fig.update_layout(height=480, showlegend=False)
        col.plotly_chart(fig, use_container_width=True)


def line_chart(df: pd.DataFrame, kw: str, top_n: int):
    d = df.groupby("平台", group_keys=False).head(top_n).copy()
    d["序號"] = d.groupby("平台").cumcount() + 1
    fig = px.line(d, x="序號", y="價格", color="平台", markers=True,
                  color_discrete_map=COLOR_MAP, hover_data={"品名": True},
                  title=f"「{kw}」售價走勢（依搜尋排序）")
    for plat, g in d.groupby("平台"):
        fig.add_hline(y=g["價格"].mean(), line_dash="dash",
                      line_color=COLOR_MAP.get(plat), opacity=0.6,
                      annotation_text=f"{plat} 平均 {g['價格'].mean():,.0f}")
    fig.update_layout(height=600)
    return fig


def sunburst_chart(df: pd.DataFrame, kw: str):
    d = df.copy()
    d["價格區間"] = d["價格區間"].astype(str)
    top = d["品牌"].value_counts().head(12).index
    d["品牌"] = d["品牌"].where(d["品牌"].isin(top), "其他")
    grp = d.groupby(["平台", "價格區間", "品牌"]).size().reset_index(name="商品數")
    fig = px.sunburst(grp, path=["平台", "價格區間", "品牌"], values="商品數",
                      title=f"「{kw}」平台 → 價格區間 → 品牌")
    fig.update_layout(height=650)
    return fig


def box_chart(df: pd.DataFrame, kw: str):
    fig = px.box(df, x="平台", y="價格", color="平台", points="outliers",
                 color_discrete_map=COLOR_MAP, hover_data={"品名": True},
                 title=f"「{kw}」價格分布（箱型圖）")
    fig.update_layout(height=600, showlegend=False)
    return fig


# ------------------------------------------------------------------
# 介面
# ------------------------------------------------------------------
st.title("🛒 MOMO × PChome 商品比價與視覺化")

with st.sidebar:
    st.header("查詢條件")
    keyword = st.text_input("商品關鍵字", value="行動電源")
    pages = st.number_input("爬取頁數（每個平台）", min_value=1, max_value=20, value=2, step=1)
    platforms = st.multiselect("比價平台", PLATFORMS, default=PLATFORMS)
    sort_label = st.selectbox("PChome 排序方式", list(PCHOME_SORT))
    delay = st.slider("每頁間隔秒數", 0.0, 5.0, 0.8, 0.2,
                      help="間隔太短可能被網站限制")
    st.divider()
    st.header("圖表設定")
    chart_type = st.radio("圖表類型", ["圓餅圖", "折線圖", "旭日圖", "箱型圖"])
    n_bins = st.slider("價格區間數（圓餅圖／旭日圖）", 3, 8, 5)
    top_n = st.slider("折線圖每平台顯示筆數", 10, 200, 60, 10)
    st.divider()
    threshold = st.slider("同款比價相似度門檻", 0.2, 0.9, 0.45, 0.05,
                          help="越高越嚴格，結果越少但越準")
    run = st.button("🔍 開始查詢", type="primary", use_container_width=True)

if run:
    if not keyword.strip():
        st.warning("請輸入商品關鍵字")
        st.stop()
    if not platforms:
        st.warning("請至少選擇一個平台")
        st.stop()
    df_new, errors = fetch_all(keyword.strip(), int(pages), platforms,
                               PCHOME_SORT[sort_label], delay)
    for plat, msg in errors.items():
        st.error(f"{plat} 抓取失敗：{msg}")
    st.session_state["df"] = df_new
    st.session_state["keyword"] = keyword.strip()

df = st.session_state.get("df")
if df is None:
    st.info("請在左側輸入關鍵字與頁數，選擇平台後按「開始查詢」。")
    st.stop()
if df.empty:
    st.warning("查無資料，請換個關鍵字或稍後再試。")
    st.stop()

kw = st.session_state["keyword"]

# ---- 各平台統計 ----
stats = (df.groupby("平台")["價格"]
         .agg(商品數="count", 平均="mean", 中位數="median", 最高="max", 最低="min")
         .round(0).astype(int))
cols = st.columns(len(stats))
for col, (plat, r) in zip(cols, stats.iterrows()):
    with col:
        st.markdown(f"**{plat}**")
        a, b = st.columns(2)
        a.metric("商品數", f"{r['商品數']:,}")
        b.metric("平均價格", f"${r['平均']:,}")
        c, d = st.columns(2)
        c.metric("最低", f"${r['最低']:,}")
        d.metric("最高", f"${r['最高']:,}")

df_binned = add_price_bins(df, n_bins)

tab_chart, tab_match, tab_data = st.tabs(["📊 圖表", "⚖️ 同款比價", "📋 商品明細"])

with tab_chart:
    if chart_type == "圓餅圖":
        pie_charts(df_binned, kw)
    elif chart_type == "折線圖":
        st.plotly_chart(line_chart(df, kw, top_n), use_container_width=True)
    elif chart_type == "旭日圖":
        st.plotly_chart(sunburst_chart(df_binned, kw), use_container_width=True)
    else:
        st.plotly_chart(box_chart(df, kw), use_container_width=True)

with tab_match:
    if df["平台"].nunique() < 2:
        st.info("需要同時有 MOMO 與 PChome 的資料才能進行同款比價。")
    else:
        matched = match_products(df, threshold)
        if matched.empty:
            st.warning("找不到相似商品，可調低左側「相似度門檻」或增加爬取頁數。")
        else:
            m1, m2, m3 = st.columns(3)
            m1.metric("配對商品數", len(matched))
            m2.metric("MOMO 較便宜", int((matched["較便宜"] == "MOMO").sum()))
            m3.metric("PChome 較便宜", int((matched["較便宜"] == "PChome").sum()))
            st.caption("配對依品名相似度推估，可能有誤，請點連結確認規格後再比較。")
            st.dataframe(
                matched, use_container_width=True, hide_index=True,
                column_config={
                    "MOMO 連結": st.column_config.LinkColumn("MOMO 連結", display_text="開啟"),
                    "PChome 連結": st.column_config.LinkColumn("PChome 連結", display_text="開啟"),
                },
            )
            st.download_button(
                "下載比價結果 CSV",
                matched.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"比價_{kw}_{time.strftime('%Y%m%d')}.csv",
                mime="text/csv",
            )

with tab_data:
    view = st.multiselect("篩選平台", sorted(df["平台"].unique()),
                          default=sorted(df["平台"].unique()))
    show = df[df["平台"].isin(view)].sort_values("價格").reset_index(drop=True)
    st.dataframe(
        show, use_container_width=True, hide_index=True,
        column_config={
            "價格": st.column_config.NumberColumn("價格", format="$%d"),
            "連結": st.column_config.LinkColumn("連結", display_text="開啟"),
        },
    )
    st.download_button(
        "下載 CSV",
        show.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"商品價格_{kw}_{time.strftime('%Y%m%d')}.csv",
        mime="text/csv",
    )
