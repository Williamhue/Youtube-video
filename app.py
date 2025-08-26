# app.py —— 只读 CSV 的 Streamlit 看板（无外部 API 调用）
import os
from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

st.set_page_config(page_title="YouTube Tracker", layout="wide")

# 可选：页面自动刷新（若未安装则自动跳过）
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=5 * 60 * 1000, key="auto-refresh")  # 每5分钟刷新一次页面
except Exception:
    pass

# ---- 小样式：让左侧缩略图垂直居中 ----
st.markdown(
    """
<style>
.thumb-cell { display: flex; align-items: center; height: 100%; }
.thumb-cell img { max-width: 100%; }
</style>
""",
    unsafe_allow_html=True,
)

# ====== 工具函数 ======
def coerce_date_range(picked, fallback_start, fallback_end):
    """把 st.date_input 的返回值（tuple/list/single）规范化为 (start_date, end_date)。"""
    if isinstance(picked, (list, tuple)):
        if len(picked) == 2:
            s, e = picked
        elif len(picked) == 1:
            s, e = picked[0], picked[0]
        else:
            s, e = fallback_start, fallback_end
    else:
        s, e = picked, picked
    s = s or fallback_start
    e = e or fallback_end
    if s > e:
        s, e = e, s
    return s, e


def days_since(d):
    """返回从发布时间到现在的天数；兼容 tz-naive / tz-aware。"""
    if pd.isna(d):
        return None
    if getattr(d, "tzinfo", None) is None:
        d_utc = d.tz_localize("UTC")
    else:
        d_utc = d.tz_convert("UTC")
    now_utc = pd.Timestamp.now(tz="UTC")
    return (now_utc - d_utc).days


# 每5分钟重新读一次 CSV（线上自动拿到最新数据）
@st.cache_data(ttl=300)
def load_data():
    # 读取并清洗
    with open("data/history.csv", "rb") as f:
        raw = f.read()
    # 去掉 UTF-8 BOM（如果有）
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    from io import BytesIO
    df = pd.read_csv(BytesIO(raw))

    # 列名与字符串值去除首尾空白
    df.columns = df.columns.map(lambda c: str(c).strip())
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype(str).str.strip()

    # 关键列：统一为 tz-aware UTC
    # 你的样例是 'YYYY-MM-DD'，这里强制以 UTC 00:00 解析
    df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=True)
    df["published_at"] = pd.to_datetime(df.get("published_at"), errors="coerce", utc=True)

    return df


# ====== 主体 ======
df = load_data()

st.title("📈 YouTube 视频追踪面板")

if df.empty or df["date"].isna().all():
    st.error("无法解析 data/history.csv 的日期列。请检查是否存在隐藏空格/BOM 或日期格式异常。")
    st.stop()

# ---- 数据最后更新时间（基于 CSV 内容 + 文件写入时间）----
csv_last_ts = pd.to_datetime(df["date"], errors="coerce").max()
last_file_time_la = None
try:
    mtime = os.path.getmtime("data/history.csv")
    last_file_time_la = (
        pd.to_datetime(mtime, unit="s", utc=True)
        .tz_convert("America/Los_Angeles")
        .strftime("%Y-%m-%d %H:%M:%S %Z")
    )
except Exception:
    pass

msg_left = (
    f"CSV 最新日期：**{csv_last_ts.tz_convert('UTC').date().isoformat()}**"
    if pd.notna(csv_last_ts)
    else "CSV 最新日期：**未知**"
)
msg_right = f"｜ 文件更新时间（LA）：**{last_file_time_la}**" if last_file_time_la else ""
st.info(f"🕒 {msg_left} {msg_right}")

# ---- 计算“每个视频最新一行”与默认排序 ----
latest = df.sort_values("date").groupby("video_id", as_index=False).tail(1).copy()
latest = latest.sort_values("published_at", ascending=False, na_position="last")

# -------- 侧边筛选 --------
with st.sidebar:
    st.header("筛选 & 工具")

    # 频道筛选（含 All）
    channels = sorted(latest["channel_title"].dropna().unique().tolist())
    channel_options = ["All"] + channels
    sel_channel = st.selectbox("按频道筛选", channel_options, index=0, key="channel_select")

    # 指标与数值模式
    metric_label = st.selectbox(
        "折线图指标", ["播放量 (Views)", "点赞数 (Likes)", "评论数 (Comments)"], index=0, key="metric_select"
    )
    metric_map = {
        "播放量 (Views)": ("views", "播放量"),
        "点赞数 (Likes)": ("likes", "点赞数"),
        "评论数 (Comments)": ("comments", "评论数"),
    }
    metric_col, metric_cn = metric_map[metric_label]

    mode = st.radio("数值模式", ["累计", "每日增量"], index=0, horizontal=True, key="mode_radio")

    # 日期范围（影响：折线图、顶部区间增量KPI）
    min_d = df["date"].min()
    max_d = df["date"].max()
    # 转为日期（去时区）
    min_date = min_d.tz_convert("UTC").date() if pd.notna(min_d) else date.today()
    max_date = max_d.tz_convert("UTC").date() if pd.notna(max_d) else date.today()

    picked = st.date_input(
        "折线图日期范围",
        value=(min_date, max_date),
        key="date_range",
    )
    start_date, end_date = coerce_date_range(picked, min_date, max_date)
    st.caption(f"已选日期：{start_date} → {end_date}")

    # 排序依据（含“按发布日期（新→旧）”）
    sort_label = st.selectbox(
        "排序依据", ["按播放量", "按点赞数", "按评论数", "按发布日期（新→旧）"], index=3, key="sort_select"
    )
    sort_map = {"按播放量": "views", "按点赞数": "likes", "按评论数": "comments"}

    st.write("---")
    # 手动刷新按钮（清缓存并重跑）
    if st.button("🔄 刷新数据（清缓存）", key="refresh"):
        st.cache_data.clear()
        st.rerun()

# 根据频道筛选
filtered_latest = latest if sel_channel == "All" else latest[latest["channel_title"] == sel_channel]

# 应用排序
if sort_label == "按发布日期（新→旧）":
    filtered_latest = filtered_latest.sort_values("published_at", ascending=False, na_position="last")
else:
    sort_col = sort_map[sort_label]
    filtered_latest = filtered_latest.sort_values(sort_col, ascending=False)

selected_ids = set(filtered_latest["video_id"].tolist())

# ---- 将日期边界规范为 UTC 的闭区间 [start, end] ----
start_ts_utc = pd.to_datetime(start_date).tz_localize("UTC")
end_ts_utc = (pd.to_datetime(end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)).tz_localize("UTC")

# ---- 历史筛选（供图表/对比使用） ----
hist_df = df[df["video_id"].isin(selected_ids)].copy()
show_df_for_chart = hist_df[(hist_df["date"] >= start_ts_utc) & (hist_df["date"] <= end_ts_utc)].copy()

# 如果选择的结束日期 > 数据最新日期，提示
data_max_day = max_date
if data_max_day and end_date > data_max_day:
    st.warning(f"所选结束日期 **{end_date}** 超过当前数据最新日期 **{data_max_day}**，图表只显示到 {data_max_day}。")

st.caption(f"数据按天记录；频道：{sel_channel} ｜ 视频数：{filtered_latest.shape[0]}")

# ---- 全局 KPI（总量/率）：针对当前频道筛选（各视频“最新一行”加总） ----
kpi_scope = filtered_latest.copy()
total_views = int(kpi_scope["views"].sum())
total_likes = int(kpi_scope["likes"].sum())
total_comments = int(kpi_scope["comments"].sum())
like_rate = (total_likes / total_views * 100) if total_views > 0 else 0.0
comment_rate = (total_comments / total_views * 100) if total_views > 0 else 0.0

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("总播放量（截至最新）", f"{total_views:,}")
k2.metric("总点赞数（截至最新）", f"{total_likes:,}")
k3.metric("总评论数（截至最新）", f"{total_comments:,}")
k4.metric("Like Rate（点赞率）", f"{like_rate:.2f}%")
k5.metric("Comment Rate（评论率）", f"{comment_rate:.2f}%")

# ===== 顶部 KPI 汇总（严格按所选日期范围的“区间增量”） =====
# 先在全量历史上计算“每日增量”，再按所选日期范围切片求和
base_df = df[df["video_id"].isin(selected_ids)].sort_values(["video_id", "date"]).copy()
for col in ["views", "likes", "comments"]:
    inc_col = f"{col}_inc"
    base_df[inc_col] = base_df.groupby("video_id")[col].diff().fillna(0)
    base_df.loc[base_df[inc_col] < 0, inc_col] = 0  # 防抖：出现回退时不计负增量

interval_df = base_df[(base_df["date"] >= start_ts_utc) & (base_df["date"] <= end_ts_utc)].copy()

iv_views = int(interval_df["views_inc"].sum()) if not interval_df.empty else 0
iv_likes = int(interval_df["likes_inc"].sum()) if not interval_df.empty else 0
iv_comments = int(interval_df["comments_inc"].sum()) if not interval_df.empty else 0

i1, i2, i3 = st.columns(3)
i1.metric("本期总增量 · 播放量", f"{iv_views:,}")
i2.metric("本期总增量 · 点赞数", f"{iv_likes:,}")
i3.metric("本期总增量 · 评论数", f"{iv_comments:,}")

# ====== 各视频单卡片 + 折线（带点与数值标签） ======
for _, row in filtered_latest.iterrows():
    vid = row["video_id"]
    col1, col2 = st.columns([1, 3])

    with col1:
        thumb = row.get("thumbnail_url", None)
        st.markdown("<div class='thumb-cell'>", unsafe_allow_html=True)
        if pd.notna(thumb) and thumb:
            st.image(thumb, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)
        st.markdown(f"[▶️ 打开视频]({row['video_url']})")

    with col2:
        st.subheader(f"{row['title']}")
        st.write(f"**频道**：{row['channel_title']}")
        pub = row["published_at"]
        dcount = days_since(pub)
        pub_text = pub.tz_convert("UTC").date().isoformat() if pd.notna(pub) else "未知"
        st.write(f"**发布日期**：{pub_text}  ｜  **已发布**：{dcount} 天")
        c1, c2, c3 = st.columns(3)
        c1.metric("总播放量", f"{int(row['views']):,}")
        c2.metric("总点赞数", f"{int(row['likes']):,}")
        c3.metric("总评论数", f"{int(row['comments']):,}")

        vhist = show_df_for_chart[show_df_for_chart["video_id"] == vid].sort_values("date").copy()
        if vhist.empty:
            st.info("当前日期范围内无数据")
            continue

        if mode == "每日增量":
            vhist["value"] = vhist[metric_col].diff().fillna(0)
            vhist.loc[vhist["value"] < 0, "value"] = 0
            y_title = f"{metric_cn}（每日增量）"
        else:
            vhist["value"] = vhist[metric_col]
            y_title = f"{metric_cn}（累计）"

        chart_base = alt.Chart(vhist).encode(
            x=alt.X("date:T", title="日期"),
            y=alt.Y("value:Q", title=y_title),
            tooltip=[
                alt.Tooltip("date:T", title="日期"),
                alt.Tooltip("value:Q", title=y_title, format=","),
            ],
        )
        line = chart_base.mark_line()
        points = chart_base.mark_point(size=40)
        labels = chart_base.mark_text(dy=-8).encode(text=alt.Text("value:Q", format=","))

        chart = (line + points + labels).properties(height=220)
        st.altair_chart(chart, use_container_width=True)

# ====== 多视频对比（一张图） ======
st.write("---")
st.subheader("📊 多视频对比（同一张图）")

label_map = dict(
    zip(
        filtered_latest["video_id"],
        filtered_latest["title"] + " — " + filtered_latest["channel_title"],
    )
)
default_compare_ids = list(label_map.keys())
compare_labels = st.multiselect(
    "选择参与对比的视频",
    options=[label_map[v] for v in default_compare_ids],
    default=[label_map[v] for v in default_compare_ids],
    key="compare_select",
)
compare_ids = {vid for vid, label in label_map.items() if label in compare_labels}

cmp = show_df_for_chart[show_df_for_chart["video_id"].isin(compare_ids)].copy()
if cmp.empty:
    st.info("当前筛选下没有可对比的数据")
else:
    cmp = cmp.sort_values(["video_id", "date"]).copy()
    if mode == "每日增量":
        cmp["value"] = cmp.groupby("video_id")[metric_col].diff().fillna(0)
        cmp.loc[cmp["value"] < 0, "value"] = 0
        y_title = f"{metric_cn}（每日增量）"
    else:
        cmp["value"] = cmp[metric_col]
        y_title = f"{metric_cn}（累计）"

    cmp["label"] = cmp["video_id"].map(label_map)
    legend_sel = alt.selection_point(fields=["label"], bind="legend", toggle=True)

    base_cmp = (
        alt.Chart(cmp)
        .transform_filter(legend_sel)
        .encode(
            x=alt.X("date:T", title="日期"),
            y=alt.Y("value:Q", title=y_title),
            color=alt.Color("label:N", title="视频"),
            tooltip=[
                alt.Tooltip("label:N", title="视频"),
                alt.Tooltip("date:T", title="日期"),
                alt.Tooltip("value:Q", title=y标题, format=","),
            ],
        )
    ).add_params(legend_sel)

    line_cmp = base_cmp.mark_line()
    points_cmp = base_cmp.mark_point(size=36)
    labels_cmp = base_cmp.mark_text(dy=-8).encode(text=alt.Text("value:Q", format=","))

    compare_chart = (line_cmp + points_cmp + labels_cmp).properties(height=360)
    st.altair_chart(compare_chart, use_container_width=True)

st.write("---")
# ---- DEBUG：帮助你快速判断过滤是否生效 ----
st.caption(
    f"DEBUG · 当前区间：{start_ts_utc.date()} → {end_ts_utc.date()} · "
    f"过滤后样本行数：{show_df_for_chart.shape[0]}"
)

st.caption("数据来源：data/history.csv（由定时任务更新）。时区：America/Los_Angeles。")
